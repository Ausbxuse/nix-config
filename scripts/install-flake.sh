#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

readonly REPO_SOURCE='@repoSource@'
readonly HOST_DEFS_FILE='@hostDefsFile@'
readonly DEFAULT_USERNAME='@username@'
readonly OFFLINE_INSTALL_MANIFEST='@offlineInstallManifest@'
readonly SECRET_KEY_PATH='/tmp/secret.key'

HOST=""
SYSTEM=""
DISK=""
DISK_FROM_ARGS=0
ASK_DISK=0
USERNAME=""
FULL_NAME=""
EMAIL=""
NIXOS_MODE=""       # yes|no
HOME_MODE=""        # yes|no
NIXOS_PROFILE=""
HOME_PROFILE=""

DISPLAY_PROFILE=""
SWAP_SIZE=""
INSTALL_LAYOUT=""
PLATFORM=""
VISIBILITY=""
COPY_REPO="yes"     # yes|no; the installed host should be self-manageable
CAPS_REMAP=""       # yes|no
REPO_DEST=""
KNOWN_HOST=0
DRY_RUN=0
PORTABLE=0
SKIP_PARTITIONING=0
OFFLINE_POLICY="auto" # auto|require|disable
OFFLINE_INSTALL=0
OFFLINE_TOPLEVEL=""
OFFLINE_CLOSURE_INFO=""
OFFLINE_DISKO_SCRIPT=""
OFFLINE_DISK_ALIAS=""
OFFLINE_DISK_ALIAS_CREATED=0
WORKTREE=""
declare -a INSTALL_ARTIFACTS=()

INSTALL_STARTED_SECONDS=0
PROFILE_PREPARE_SECONDS=0
PROFILE_DISKO_SECONDS=0
PROFILE_HARDWARE_SECONDS=0
PROFILE_STORE_COPY_SECONDS=0
PROFILE_STORE_REGISTER_SECONDS=0
PROFILE_NIXOS_INSTALL_SECONDS=0
PROFILE_FINISH_SECONDS=0

@source_lib@

script_banner() { banner "nixos installer" "partition, format, and install NixOS"; }

format_duration() {
  local total=${1:-0}
  local hours=$((total / 3600))
  local minutes=$(((total % 3600) / 60))
  local seconds=$((total % 60))

  if (( hours > 0 )); then
    printf '%dh %02dm %02ds' "$hours" "$minutes" "$seconds"
  elif (( minutes > 0 )); then
    printf '%dm %02ds' "$minutes" "$seconds"
  else
    printf '%ds' "$seconds"
  fi
}

show_install_timing() {
  local total=$((SECONDS - INSTALL_STARTED_SECONDS))

  section "installation time"
  kv "prepare" "$(format_duration "$PROFILE_PREPARE_SECONDS")"
  if [[ "$NIXOS_MODE" == "yes" ]]; then
    if [[ $SKIP_PARTITIONING -eq 0 ]]; then
      kv "disk + LUKS" "$(format_duration "$PROFILE_DISKO_SECONDS")"
    fi
    kv "hardware config" "$(format_duration "$PROFILE_HARDWARE_SECONDS")"
    if [[ $OFFLINE_INSTALL -eq 1 ]]; then
      kv "store copy" "$(format_duration "$PROFILE_STORE_COPY_SECONDS")"
      kv "store register" "$(format_duration "$PROFILE_STORE_REGISTER_SECONDS")"
    fi
    kv "nixos-install" "$(format_duration "$PROFILE_NIXOS_INSTALL_SECONDS")"
  fi
  kv "finish" "$(format_duration "$PROFILE_FINISH_SECONDS")"
  kv "total" "${C_BOLD}$(format_duration "$total")${C_RESET}"
}

show_next_steps() {
  section "next step"

  if [[ "$NIXOS_MODE" == "yes" ]]; then
    ok "installation completed successfully on ${DISK}"
    info "1. Power off this live installer: sudo poweroff"
    info "2. Remove/eject the installer USB or ISO."
    info "3. Boot from ${DISK} and enter the LUKS passphrase."
    if [[ "$HOST" == "razy" || "$HOST" == "spacy" ]]; then
      info "4. After first boot, enroll ${HOST} to add private secrets and admin access."
    fi
    warn "Do not run the installer on ${DISK} again unless you intend to erase it."
  else
    ok "Home Manager activation completed successfully"
    info "Open a new login session so shell and group changes are fully applied."
  fi

  # Desktop launchers otherwise close their terminal immediately, hiding the
  # timing and safety reminder. Automated --yes installs remain non-blocking.
  if [[ $ASSUME_YES -eq 0 && -r /dev/tty ]]; then
    printf '  %sPress Enter after reading the next steps.%s ' "$C_BOLD" "$C_RESET" >/dev/tty
    IFS= read -r _ </dev/tty || true
  fi
}

bool_word() {
  if [[ "$1" == "yes" ]]; then
    printf 'true'
  else
    printf 'false'
  fi
}

nix_escape_string() {
  printf '%s' "$1" | sed 's/[&|]/\\&/g; s/\\/\\\\/g; s/"/\\"/g'
}

export NIX_CONFIG="${NIX_CONFIG:+$NIX_CONFIG
}experimental-features = nix-command flakes"
readonly NIX_CMD=(nix --extra-experimental-features "nix-command flakes")

cleanup() {
  stop_installer_prefetch
  if [[ $OFFLINE_DISK_ALIAS_CREATED -eq 1 && -L "$OFFLINE_DISK_ALIAS" ]]; then
    sudo rm -f "$OFFLINE_DISK_ALIAS" 2>/dev/null || true
  fi

  if [[ -f "$SECRET_KEY_PATH" ]]; then
    sudo rm -f "$SECRET_KEY_PATH" 2>/dev/null || true
  fi

  if [[ -n "$WORKTREE" && -d "$WORKTREE" ]]; then
    if ! rm -rf "$WORKTREE" 2>/dev/null; then
      sudo rm -rf "$WORKTREE" 2>/dev/null || true
    fi
  fi
}
trap cleanup EXIT

record_install_artifact() {
  INSTALL_ARTIFACTS+=("$1")
}

default_swap_size() {
  printf '%sG\n' "$(detect_ram_gib)"
}

suggest_nixos_profile() {
  if command -v lspci >/dev/null 2>&1 && lspci 2>/dev/null | grep -qi 'NVIDIA'; then
    printf 'portable-nvidia-gnome\n'
  else
    printf 'portable-gnome\n'
  fi
}

home_profile_supports_display_profile() {
  case "$1" in
    minimal-gui|personal-gnome) return 0 ;;
    *)                          return 1 ;;
  esac
}

is_debian_like_host() {
  if [[ ! -r /etc/os-release ]]; then
    return 1
  fi

  # shellcheck disable=SC1091
  . /etc/os-release
  case " ${ID:-} ${ID_LIKE:-} " in
    *" ubuntu "*|*" debian "*) return 0 ;;
    *)                         return 1 ;;
  esac
}

apt_get_noninteractive() {
  sudo env DEBIAN_FRONTEND=noninteractive apt-get \
    -o Acquire::Retries=5 \
    -o Acquire::http::Timeout=20 \
    -o Acquire::https::Timeout=20 \
    "$@"
}

remove_user_shadowed_gnome_extensions() {
  local target_dir="${HOME}/.local/share/gnome-shell/extensions"
  local name
  local -a distro_extensions=(
    "drive-menu@gnome-shell-extensions.gcampax.github.com"
    "screenshot-window-sizer@gnome-shell-extensions.gcampax.github.com"
    "user-theme@gnome-shell-extensions.gcampax.github.com"
    "workspace-indicator@gnome-shell-extensions.gcampax.github.com"
  )

  for name in "${distro_extensions[@]}"; do
    if [[ -d "/usr/share/gnome-shell/extensions/${name}" && -e "${target_dir}/${name}" ]]; then
      chmod -R u+w "${target_dir:?}/${name}" 2>/dev/null || true
      rm -rf "${target_dir:?}/${name}"
    fi
  done
}

list_nixos_profiles() {
  find "$REPO_SOURCE/modules/profiles/nixos" -maxdepth 1 -type f -name '*.nix' \
    -exec basename {} .nix \; 2>/dev/null | sort
}

list_home_profiles() {
  find "$REPO_SOURCE/modules/profiles/home" -maxdepth 1 -type f -name '*.nix' \
    -exec basename {} .nix \; 2>/dev/null | sort
}

list_install_layouts() {
  find "$REPO_SOURCE/modules/nixos/install" -maxdepth 1 -type f -name '*.nix' \
    -exec basename {} .nix \; 2>/dev/null | sort
}

list_display_profiles() {
  awk '
    /^  profiles = {/ { in_profiles = 1; next }
    in_profiles && /^  };/ { in_profiles = 0 }
    in_profiles && /^    [A-Za-z0-9_-]+ = {/ { print $1 }
  ' "$REPO_SOURCE/modules/home/display-profile.nix" 2>/dev/null
}

host_known() {
  jq -e --arg host "$1" 'has($host)' "$HOST_DEFS_FILE" >/dev/null
}

host_query() {
  jq -r --arg host "$1" "$2" "$HOST_DEFS_FILE"
}

host_bool() {
  if [[ "$(host_query "$1" "$2")" == "true" ]]; then
    printf 'yes\n'
  else
    printf 'no\n'
  fi
}

list_known_hosts() {
  jq -r 'keys[]' "$HOST_DEFS_FILE"
}

# Parse `lsblk` into two parallel arrays: disk paths and pretty labels.
declare -a DISK_PATHS=()
declare -a DISK_LABELS=()

load_disks() {
  DISK_PATHS=()
  DISK_LABELS=()

  while IFS=$'\t' read -r path type rm size model tran; do
    if [[ "$type" != "disk" || "$rm" != "0" ]]; then
      continue
    fi
    DISK_PATHS+=("$path")
    DISK_LABELS+=("$(printf '%-14s %-8s %s %s' "$path" "$size" "${model:-?}" "${tran:-?}")")
  done < <(
    lsblk -ndP -o PATH,TYPE,RM,SIZE,MODEL,TRAN 2>/dev/null \
      | sed 's/ *\([A-Z]\{1,\}\)="/\t\1="/g; s/"//g; s/^[A-Z]\{1,\}=//; s/\t[A-Z]\{1,\}=/\t/g'
  )
}

# --------------------------------------------------------------------- args

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --host)            HOST=${2:?missing value};            shift 2 ;;
      --disk)            DISK=${2:?missing value}; DISK_FROM_ARGS=1; ASK_DISK=0; shift 2 ;;
      --ask-disk)        DISK=""; DISK_FROM_ARGS=0; ASK_DISK=1; shift ;;
      --system)          SYSTEM=${2:?missing value};          shift 2 ;;
      --username)        USERNAME=${2:?missing value};        shift 2 ;;
      --name)            FULL_NAME=${2:?missing value};       shift 2 ;;
      --email)           EMAIL=${2:?missing value};           shift 2 ;;
      --nixos)           NIXOS_MODE=yes;                      shift ;;
      --no-nixos)        NIXOS_MODE=no;                       shift ;;
      --home)            HOME_MODE=yes;                       shift ;;
      --no-home)         HOME_MODE=no;                        shift ;;

      --nixos-profile)   NIXOS_PROFILE=${2:?missing value};   shift 2 ;;
      --home-profile)    HOME_PROFILE=${2:?missing value};    shift 2 ;;
      --display-profile) DISPLAY_PROFILE=${2:?missing value}; shift 2 ;;
      --swap-size)       SWAP_SIZE=${2:?missing value};       shift 2 ;;
      --install-layout)  INSTALL_LAYOUT=${2:?missing value};  shift 2 ;;
      --copy-repo)       COPY_REPO=${2:?missing value};       shift 2 ;;
      --caps-remap)      CAPS_REMAP=${2:?missing value};      shift 2 ;;
      --repo-dest)       REPO_DEST=${2:?missing value};       shift 2 ;;
      --portable)        PORTABLE=1;                            shift ;;
      --skip-partitioning) SKIP_PARTITIONING=1;               shift ;;
      --offline)          OFFLINE_POLICY=require;             shift ;;
      --online)           OFFLINE_POLICY=disable;             shift ;;
      --dry-run)         DRY_RUN=1;                           shift ;;
      --no-color)        USE_COLOR=0; apply_colors;          shift ;;
      -y|--yes)          ASSUME_YES=1;                        shift ;;
      -h|--help)
        cat <<EOF
Usage:
  nix run .#install -- [options]

Options:
  --host NAME              Known host name or a new custom host name
  --disk PATH              Target disk for NixOS installation
  --ask-disk               Ignore a known host's disk and prompt for one
  --system SYSTEM          Override detected system, e.g. x86_64-linux
  --username NAME          Override the host user name
  --name NAME              Override the Git user name written into globals.nix
  --email EMAIL            Override the Git user email written into globals.nix
  --nixos / --no-nixos     Enable or disable NixOS installation mode
  --home / --no-home       Enable or disable Home Manager mode
  --nixos-profile NAME     Profile basename under modules/profiles/nixos/
  --home-profile NAME      Profile basename under modules/profiles/home/
  --display-profile NAME   Display profile for custom home configs
  --swap-size SIZE         Swapfile size, e.g. 32G (default: RAM-matched)
  --install-layout NAME    Layout under modules/nixos/install/
  --copy-repo yes|no       Copy the repo into the installed system (default: yes)
  --caps-remap yes|no      Configure Caps as tap Escape / hold Control on
                           generic Linux home installs
  --repo-dest PATH         Destination for copied repo inside the target root
  --portable               Portable mode for non-root, non-NixOS hosts using
                           nix-portable (implies --home)
  --skip-partitioning      Skip disko and reuse an already-partitioned, already-
                           mounted target root at /mnt
  --offline                Require a prebuilt closure embedded in the installer
  --online                 Ignore embedded closures and use the normal network path
  --dry-run                Show the resolved plan and exit without touching disks
  --no-color               Disable ANSI colors even on a TTY
  -y, --yes                Accept all confirmation prompts
EOF
        exit 0
        ;;
      *)
        die "Unknown argument: $1"
        ;;
    esac
  done

  if [[ -n "$HOST" ]]; then
    validate_hostname "$HOST" || die "invalid --host: $HOST"
  fi

  if [[ -n "$USERNAME" ]]; then
    validate_username "$USERNAME" || die "invalid --username: $USERNAME"
  fi

  if [[ -n "$EMAIL" ]]; then
    validate_email "$EMAIL" || die "invalid --email: $EMAIL"
  fi

  if [[ -n "$SWAP_SIZE" ]]; then
    validate_swap_size "$SWAP_SIZE" || die "invalid --swap-size: $SWAP_SIZE"
  fi

  if [[ -n "$COPY_REPO" && "$COPY_REPO" != "yes" && "$COPY_REPO" != "no" ]]; then
    die "--copy-repo must be yes or no"
  fi

  if [[ -n "$CAPS_REMAP" && "$CAPS_REMAP" != "yes" && "$CAPS_REMAP" != "no" ]]; then
    die "--caps-remap must be yes or no"
  fi
}

# --------------------------------------------------------------- resolve plan
# shellcheck disable=SC2016
resolve_target() {
  if [[ -z "$HOST" ]]; then
    local -a known
    mapfile -t known < <(list_known_hosts)

    if [[ ${#known[@]} -gt 0 ]]; then
      HOST=$(prompt_select "pick host (or type new)" "" "${known[@]}" "<new custom host>")
      if [[ "$HOST" == "<new custom host>" ]]; then
        HOST=$(prompt_text "hostname" "" validate_hostname)
      fi
    else
      HOST=$(prompt_text "hostname" "" validate_hostname)
    fi
  fi

  if host_known "$HOST"; then
    KNOWN_HOST=1

    SYSTEM=${SYSTEM:-$(host_query "$HOST" '.[$host].system // empty')}
    USERNAME=${USERNAME:-$(host_query "$HOST" '.[$host].username // empty')}
    PLATFORM=${PLATFORM:-$(host_query "$HOST" '.[$host].platform // "custom"')}
    VISIBILITY=${VISIBILITY:-$(host_query "$HOST" '.[$host].visibility // "private"')}
    INSTALL_LAYOUT=${INSTALL_LAYOUT:-$(host_query "$HOST" '.[$host].install.layout // empty')}
    DISK=${DISK:-$(host_query "$HOST" '.[$host].install.disk // empty')}
    SWAP_SIZE=${SWAP_SIZE:-$(host_query "$HOST" '.[$host].install.swapSize // empty')}

    if [[ -z "$NIXOS_MODE" ]]; then
      NIXOS_MODE=$(host_bool "$HOST" '.[$host].nixos.enable // false')
    fi
    if [[ -z "$HOME_MODE" ]]; then
      HOME_MODE=$(host_bool "$HOST" '.[$host].home.enable // false')
    fi
    if [[ -z "$NIXOS_PROFILE" ]]; then
      NIXOS_PROFILE=$(host_query "$HOST" '.[$host].nixos.profile // empty')
    fi
    if [[ -z "$HOME_PROFILE" ]]; then
      HOME_PROFILE=$(host_query "$HOST" '.[$host].home.profile // empty')
    fi
    if [[ -z "$DISPLAY_PROFILE" ]]; then
      DISPLAY_PROFILE=$(host_query "$HOST" '.[$host].home.displayProfile // empty')
    fi
  else
    SYSTEM=${SYSTEM:-$(detect_system)}
    if [[ -z "$USERNAME" ]]; then
      USERNAME=$(prompt_text "username" "$DEFAULT_USERNAME" validate_username)
    fi
    PLATFORM=${PLATFORM:-custom}
    VISIBILITY=${VISIBILITY:-private}
  fi

  if [[ $ASK_DISK -eq 1 ]]; then
    DISK=""
  fi

  USERNAME=${USERNAME:-$DEFAULT_USERNAME}
}

resolve_modes() {
  if [[ -z "$NIXOS_MODE" && -z "$HOME_MODE" ]]; then
    NIXOS_MODE=$(prompt_select "install nixos on this machine?" no yes no)
    HOME_MODE=$(prompt_select "activate home-manager?" no yes no)
  fi

  if [[ "$NIXOS_MODE" != "yes" && "$HOME_MODE" != "yes" ]]; then
    die "Nothing to do: enable at least --nixos or --home."
  fi
}

resolve_identity() {
  if [[ $OFFLINE_INSTALL -eq 1 ]]; then
    FULL_NAME=${FULL_NAME:-$(offline_target_value '.name')}
    EMAIL=${EMAIL:-$(offline_target_value '.email')}
  fi

  if [[ -z "$FULL_NAME" ]]; then
    FULL_NAME=$(prompt_text "git name")
  fi

  if [[ -z "$EMAIL" ]]; then
    EMAIL=$(prompt_text "git email" "" validate_email)
  fi

  if [[ $OFFLINE_INSTALL -eq 1 ]]; then
    [[ "$FULL_NAME" == "$(offline_target_value '.name')" ]] \
      || die "offline '${HOST}' closure uses the embedded Git name; use --online to override it"
    [[ "$EMAIL" == "$(offline_target_value '.email')" ]] \
      || die "offline '${HOST}' closure uses the embedded Git email; use --online to override it"
  fi
}

offline_manifest_value() {
  jq -r "$1 // empty" "$OFFLINE_INSTALL_MANIFEST"
}

offline_target_value() {
  jq -r --arg host "$HOST" ".targets[\$host]$1 // empty" "$OFFLINE_INSTALL_MANIFEST"
}

select_offline_target() {
  if [[ "$OFFLINE_POLICY" == "disable" ]]; then
    return 0
  fi

  local manifest_strict="false"
  local target_system=""

  if [[ -r "$OFFLINE_INSTALL_MANIFEST" ]]; then
    manifest_strict=$(offline_manifest_value '.strict')
    target_system=$(offline_target_value '.system')
  fi

  if [[ "$NIXOS_MODE" != "yes" ]]; then
    if [[ "$OFFLINE_POLICY" == "require" || "$manifest_strict" == "true" ]]; then
      die "embedded closures install a complete NixOS target; use --online for Home-only or portable installs"
    fi
    return 0
  fi

  if [[ -z "$target_system" ]]; then
    if [[ "$OFFLINE_POLICY" == "require" || "$manifest_strict" == "true" ]]; then
      die "no embedded offline closure for '${HOST}' (${SYSTEM}); rerun with --online for the network installer"
    fi
    return 0
  fi

  if [[ "$target_system" != "$SYSTEM" ]]; then
    die "embedded closure for '${HOST}' is ${target_system}, not ${SYSTEM}"
  fi

  local expected_username expected_nixos_mode expected_home_mode
  local expected_nixos_profile expected_home_profile
  local expected_display_profile expected_layout expected_swap_size
  expected_username=$(offline_target_value '.username')
  expected_nixos_mode=$(offline_target_value '.nixosEnabled')
  expected_home_mode=$(offline_target_value '.homeEnabled')
  expected_nixos_profile=$(offline_target_value '.nixosProfile')
  expected_home_profile=$(offline_target_value '.homeProfile')
  expected_display_profile=$(offline_target_value '.displayProfile')
  expected_layout=$(offline_target_value '.installLayout')
  expected_swap_size=$(offline_target_value '.swapSize')

  [[ "$USERNAME" == "$expected_username" ]] \
    || die "offline '${HOST}' closure requires --username ${expected_username}; use --online for overrides"
  if [[ -n "$expected_nixos_mode" ]]; then
    [[ "$NIXOS_MODE" == "$expected_nixos_mode" ]] \
      || die "offline '${HOST}' closure requires NixOS mode '${expected_nixos_mode}'; use --online for overrides"
  fi
  if [[ -n "$expected_home_mode" ]]; then
    [[ "$HOME_MODE" == "$expected_home_mode" ]] \
      || die "offline '${HOST}' closure requires Home Manager mode '${expected_home_mode}'; use --online for overrides"
  fi
  [[ "$NIXOS_PROFILE" == "$expected_nixos_profile" ]] \
    || die "offline '${HOST}' closure requires NixOS profile '${expected_nixos_profile}'; use --online for overrides"
  [[ "$HOME_PROFILE" == "$expected_home_profile" ]] \
    || die "offline '${HOST}' closure requires Home profile '${expected_home_profile}'; use --online for overrides"
  [[ "$DISPLAY_PROFILE" == "$expected_display_profile" ]] \
    || die "offline '${HOST}' closure requires display profile '${expected_display_profile}'; use --online for overrides"
  [[ "$INSTALL_LAYOUT" == "$expected_layout" ]] \
    || die "offline '${HOST}' closure requires install layout '${expected_layout}'; use --online for overrides"
  [[ "$SWAP_SIZE" == "$expected_swap_size" ]] \
    || die "offline '${HOST}' closure requires swap size '${expected_swap_size}'; use --online for overrides"

  OFFLINE_TOPLEVEL=$(offline_target_value '.toplevel')
  OFFLINE_CLOSURE_INFO=$(offline_target_value '.closureInfo')
  OFFLINE_DISKO_SCRIPT=$(offline_target_value '.diskoScript')
  OFFLINE_DISK_ALIAS=$(offline_target_value '.diskAlias')
  OFFLINE_INSTALL=1
}

copy_offline_store() {
  local image=${1:-/iso/nix-store.squashfs}
  local target=${2:-/mnt/nix/store}
  # Read the SquashFS directly so decompression can run ahead of file creation.
  # The mounted-store fallback also supports running outside the live ISO.
  if [[ -r "$image" ]]; then
    local -a paths=()
    mapfile -t paths <"$OFFLINE_CLOSURE_INFO/store-paths" || return 1
    [[ ${#paths[@]} -gt 0 ]] || return 1
    # Check membership without following symlinks. -match follows absolute
    # store symlinks against the image root, where /nix/store does not exist.
    local entries path
    entries=$(unsquashfs -quiet -ls -max-depth 1 -dest /nix/store "$image") || return 1
    local -A present=()
    while IFS= read -r path; do
      present["$path"]=1
    done <<<"$entries"
    for path in "${paths[@]}"; do
      if [[ "$path" != /nix/store/* || -z "${present[$path]:-}" ]]; then
        err "store path missing from ISO: $path"
        return 1
      fi
    done
    sudo unsquashfs -force -no-progress -strict-errors -no-wildcards \
      -dest "$target" "$image" "${paths[@]#/nix/store/}"
    return $?
  fi

  # The embedded manifest is world-readable; only the destination needs root.
  # shellcheck disable=SC2024
  sudo xargs "$(command -v xcp)" --recursive --target-directory "$target" \
    <"$OFFLINE_CLOSURE_INFO/store-paths"
}

preflight_offline_target() {
  if [[ $OFFLINE_INSTALL -ne 1 ]]; then
    return 0
  fi

  require_cmd nix-store nixos-install
  if [[ -r /iso/nix-store.squashfs ]]; then
    require_cmd unsquashfs
  else
    require_cmd xcp
  fi
  [[ -d "$OFFLINE_TOPLEVEL" ]] || die "embedded system closure is missing: ${OFFLINE_TOPLEVEL}"
  [[ -x "$OFFLINE_DISKO_SCRIPT" ]] || die "embedded Disko script is missing: ${OFFLINE_DISKO_SCRIPT}"
  [[ -r "$OFFLINE_CLOSURE_INFO/store-paths" ]] \
    || die "embedded closure path list is missing: ${OFFLINE_CLOSURE_INFO}/store-paths"
  [[ -r "$OFFLINE_CLOSURE_INFO/registration" ]] \
    || die "embedded closure registration is missing: ${OFFLINE_CLOSURE_INFO}/registration"
  [[ "$OFFLINE_DISK_ALIAS" == /run/nix-config-installer/* ]] \
    || die "unsafe offline disk alias in manifest: ${OFFLINE_DISK_ALIAS}"

  local path=""
  local missing_path_found=0
  while IFS= read -r path; do
    if [[ ! -e "$path" && ! -L "$path" ]]; then
      err "missing embedded store path: $path"
      missing_path_found=1
    fi
  done <"$OFFLINE_CLOSURE_INFO/store-paths"
  [[ $missing_path_found -eq 0 ]] || die "offline closure is incomplete; rebuild the installer image"
}

resolve_custom_profiles() {
  if [[ $KNOWN_HOST -ne 0 ]]; then
    return 0
  fi

  if [[ "$NIXOS_MODE" == "yes" && -z "$NIXOS_PROFILE" ]]; then
    local -a profiles
    mapfile -t profiles < <(list_nixos_profiles)
    if [[ ${#profiles[@]} -eq 0 ]]; then
      die "no nixos profiles under modules/profiles/nixos/"
    fi
    NIXOS_PROFILE=$(prompt_select "pick nixos profile" "$(suggest_nixos_profile)" "${profiles[@]}")
  fi

  if [[ "$HOME_MODE" == "yes" && -z "$HOME_PROFILE" ]]; then
    local -a profiles
    mapfile -t profiles < <(list_home_profiles)
    if [[ ${#profiles[@]} -eq 0 ]]; then
      die "no home profiles under modules/profiles/home/"
    fi
    HOME_PROFILE=$(prompt_select "pick home profile" "personal-gnome" "${profiles[@]}")
  fi

  if [[ "$HOME_MODE" == "yes" && -n "$DISPLAY_PROFILE" ]]; then
    if ! home_profile_supports_display_profile "$HOME_PROFILE"; then
      warn "home profile '${HOME_PROFILE}' does not use display profiles — ignoring"
      DISPLAY_PROFILE=""
    fi
  fi

  if [[ "$HOME_MODE" == "yes" && -z "$DISPLAY_PROFILE" ]]; then
    if home_profile_supports_display_profile "$HOME_PROFILE"; then
      local -a profiles
      mapfile -t profiles < <(list_display_profiles)
      if [[ ${#profiles[@]} -eq 0 ]]; then
        profiles=(none gnome-default)
      fi
      DISPLAY_PROFILE=$(prompt_select "pick display profile" "gnome-default" "${profiles[@]}")
    fi
  fi

  if [[ "$NIXOS_MODE" == "yes" && -z "$INSTALL_LAYOUT" ]]; then
    local -a layouts
    mapfile -t layouts < <(list_install_layouts)
    if [[ ${#layouts[@]} -eq 0 ]]; then
      die "no install layouts under modules/nixos/install/"
    fi
    INSTALL_LAYOUT=$(prompt_select "pick install layout" "luks-btrfs" "${layouts[@]}")
  fi

  if [[ "$NIXOS_MODE" == "yes" && -z "$SWAP_SIZE" ]]; then
    section "swap"
    local ram_gib
    ram_gib=$(detect_ram_gib)
    printf '  %sram %s GiB · default matches ram for hibernation%s\n' \
      "${C_DIM}" "$ram_gib" "${C_RESET}"
    SWAP_SIZE=$(prompt_text "size" "$(default_swap_size)" validate_swap_size)
  fi
}

resolve_disk() {
  if [[ "$NIXOS_MODE" != "yes" ]]; then
    return 0
  fi

  load_disks
  if [[ ${#DISK_PATHS[@]} -eq 0 ]]; then
    die "no non-removable disks detected"
  fi

  if [[ -n "$DISK" && $DISK_FROM_ARGS -eq 0 ]]; then
    if [[ ! -b "$DISK" ]] || [[ "$(lsblk -ndo TYPE "$DISK" 2>/dev/null || true)" != "disk" ]]; then
      warn "configured target disk ${DISK} is unavailable; choose from the detected disks"
      DISK=""
    fi
  fi

  if [[ -z "$DISK" ]]; then
    local default_label=""
    local chosen=""
    local i

    if [[ ${#DISK_PATHS[@]} -eq 1 ]]; then
      default_label=${DISK_LABELS[0]}
    fi

    chosen=$(prompt_select "pick target disk" "$default_label" "${DISK_LABELS[@]}")

    for i in "${!DISK_LABELS[@]}"; do
      if [[ "${DISK_LABELS[i]}" == "$chosen" ]]; then
        DISK=${DISK_PATHS[i]}
        break
      fi
    done
  fi

  [[ -b "$DISK" ]] || die "Not a block device: $DISK"
  [[ "$(lsblk -ndo TYPE "$DISK")" == "disk" ]] || die "Not a whole disk: $DISK"
}

# --------------------------------------------------------------- recap + exec

recap() {
  section "summary"
  kv "host"       "$HOST"
  kv "system"     "$SYSTEM"
  kv "user"       "$USERNAME"
  kv "git name"   "$FULL_NAME"
  kv "git email"  "$EMAIL"
  kv "platform"   "$PLATFORM"
  kv "visibility" "$VISIBILITY"
  kv "nixos"      "${NIXOS_PROFILE:+$NIXOS_PROFILE}"
  kv "home"       "${HOME_PROFILE:+$HOME_PROFILE}"

  if [[ -n "$DISPLAY_PROFILE" ]]; then
    kv "display" "$DISPLAY_PROFILE"
  fi

  if [[ "$NIXOS_MODE" == "yes" ]]; then
    kv "layout" "$INSTALL_LAYOUT"
    kv "disk"   "$DISK"
    kv "swap"   "$SWAP_SIZE"
  fi

  if [[ $PORTABLE -eq 1 ]]; then
    kv "mode" "${C_CYAN}portable${C_RESET}"
  fi

  if [[ $DRY_RUN -eq 1 ]]; then
    kv "mode" "${C_YELLOW}dry run${C_RESET}"
  fi

  if [[ $SKIP_PARTITIONING -eq 1 ]]; then
    kv "partitioning" "skipped"
  fi

  if [[ $OFFLINE_INSTALL -eq 1 ]]; then
    kv "install source" "${C_CYAN}embedded closure (network disabled)${C_RESET}"
  elif [[ "$NIXOS_MODE" == "yes" ]]; then
    kv "install source" "network/build fallback"
  fi

  if [[ "$HOME_MODE" == "yes" && "$NIXOS_MODE" != "yes" && $PORTABLE -eq 0 ]]; then
    kv "caps remap" "${CAPS_REMAP:-prompt}"
  fi
}

prepare_worktree() {
  WORKTREE=$(mktemp -d "${TMPDIR:-/tmp}/nixos-installer.XXXXXX")
  run_with_spinner "preparing worktree at ${WORKTREE}" \
    rsync -a --delete --chmod=Du+rwx,Dgo+rx,Fu+rw,Fgo+r "$REPO_SOURCE"/ "$WORKTREE"/
}

write_worktree_host_defs() {
  local defs_file="$WORKTREE/machines/defs.nix"
  local home_enable
  local nixos_enable

  home_enable=$(bool_word "$HOME_MODE")
  nixos_enable=$(bool_word "$NIXOS_MODE")

  # Build a self-contained defs.nix: read the original, strip the outer
  # `{lib, const, ...}: {` header and closing `}`, then remove any existing
  # block for HOST and append a fresh one.
  #
  # This avoids a separate defs-known.nix file that would break when
  # enroll rsyncs the repo (it excludes defs.nix but not defs-known.nix).
  local body
  body=$(sed '1,/^}: {$/d; $d' "$defs_file")
  # Remove existing HOST block (top-level `  HOST = {` … `  };`) to avoid
  # duplicate attribute errors.  Uses awk to skip lines between the opening
  # and closing markers.
  body=$(printf '%s\n' "$body" | awk -v host="  ${HOST} = {" '
    $0 == host { skip=1; next }
    skip && /^  };/ { skip=0; next }
    !skip
  ')

  cat >"$defs_file" <<EOF
{lib, const, ...}: {
${body}
  ${HOST} = {
    system = "${SYSTEM}";
    username = "${USERNAME}";
    platform = "${PLATFORM}";
    visibility = "${VISIBILITY}";

    home = {
      enable = ${home_enable};$(
        if [[ -n "$HOME_PROFILE" ]]; then
          printf '\n      profile = "%s";' "$HOME_PROFILE"
        fi
        if [[ -n "$DISPLAY_PROFILE" ]]; then
          printf '\n      displayProfile = "%s";' "$DISPLAY_PROFILE"
        fi
      )
    };

    nixos = {
      enable = ${nixos_enable};$(
        if [[ -n "$NIXOS_PROFILE" ]]; then
          printf '\n      profile = "%s";' "$NIXOS_PROFILE"
        fi
      )
    };

    install = {
      layout = "${INSTALL_LAYOUT}";
      disk = "${DISK}";
      swapSize = "${SWAP_SIZE}";$(
        if [[ $KNOWN_HOST -eq 0 && "$NIXOS_MODE" == "yes" ]]; then
          printf '\n      canTouchEfiVariables = false;'
          printf '\n      efiInstallAsRemovable = true;'
        fi
      )
    };
  };
}
EOF
}

prepare_target_config() {
  local escaped_username
  local escaped_name
  local escaped_email

  write_worktree_host_defs

  if [[ "$NIXOS_MODE" == "yes" ]]; then
    mkdir -p "$WORKTREE/machines/$HOST"
  fi

  # Update globals.nix in the worktree so the copied repo reflects the
  # selected install identity even before private globals are available.
  escaped_username=$(nix_escape_string "$USERNAME")
  escaped_name=$(nix_escape_string "$FULL_NAME")
  escaped_email=$(nix_escape_string "$EMAIL")

  sed -i "s|^  username = .*|  username = \"$escaped_username\";|" "$WORKTREE/globals.nix"
  if grep -q '^  name = ' "$WORKTREE/globals.nix"; then
    sed -i "s|^  name = .*|  name = \"$escaped_name\";|" "$WORKTREE/globals.nix"
  else
    perl -0pi -e 's/\{\n/\{\n  name = "'"$escaped_name"'";\n/' "$WORKTREE/globals.nix"
  fi
  if grep -q '^  email = ' "$WORKTREE/globals.nix"; then
    sed -i "s|^  email = .*|  email = \"$escaped_email\";|" "$WORKTREE/globals.nix"
  else
    perl -0pi -e 's/\{\n/\{\n  email = "'"$escaped_email"'";\n/' "$WORKTREE/globals.nix"
  fi

  INSTALL_ARTIFACTS=()
  record_install_artifact "machines/defs.nix"
  record_install_artifact "globals.nix"

  ok "host definitions written"
}

write_secret_key() {
  section "luks passphrase"
  require_tty

  local luks_pw=""
  local confirm=""

  while true; do
    read -r -s -p "  enter: " luks_pw </dev/tty
    printf '\n'
    if [[ -z "$luks_pw" ]]; then
      warn "passphrase cannot be empty"
      continue
    fi

    read -r -s -p "  confirm: " confirm </dev/tty
    printf '\n'

    if [[ "$luks_pw" == "$confirm" ]]; then
      break
    fi

    warn "passphrases did not match"
  done

  printf '%s' "$luks_pw" | sudo tee "$SECRET_KEY_PATH" >/dev/null
  sudo chmod 600 "$SECRET_KEY_PATH"
  ok "staged at ${SECRET_KEY_PATH}"
}

run_nixos_install() {
  local phase_started=$SECONDS

  if [[ "$NIXOS_MODE" != "yes" ]]; then
    return 0
  fi

  if [[ $DRY_RUN -eq 1 ]]; then
    info "Dry run: skipping disko + nixos-install"
    return 0
  fi

  if [[ $SKIP_PARTITIONING -eq 1 ]]; then
    section "partitioning"
    if ! mountpoint -q /mnt; then
      die "--skip-partitioning requires the target root to already be mounted at /mnt"
    fi
    ok "using existing target mount at /mnt"
  else
    section "destructive"
    warn "this will DESTROY all data on ${C_BOLD}${DISK}${C_RESET}"

    if ! prompt_bool "proceed with disko + nixos-install on ${DISK}?" no; then
      die "aborted."
    fi

    phase_started=$SECONDS
    if [[ "$INSTALL_LAYOUT" == luks-* ]]; then
      write_secret_key
    fi

    stop_installer_prefetch

    section "disko"
    if [[ $OFFLINE_INSTALL -eq 1 ]]; then
      local alias_dir=""
      local resolved_disk=""
      alias_dir=$(dirname "$OFFLINE_DISK_ALIAS")
      resolved_disk=$(realpath "$DISK")
      sudo install -d -m 0755 "$alias_dir"
      sudo ln -sfn "$resolved_disk" "$OFFLINE_DISK_ALIAS"
      OFFLINE_DISK_ALIAS_CREATED=1
      sudo --non-interactive true 2>/dev/null || true
      sudo "$OFFLINE_DISKO_SCRIPT"
    else
      local -a disko_args=(--mode "destroy,format,mount" --flake ".#${HOST}")
      (
        cd "$WORKTREE"

        if [[ $ASSUME_YES -eq 1 ]]; then
          disko_args=(--yes-wipe-all-disks "${disko_args[@]}")
        fi

        sudo --non-interactive true 2>/dev/null || true
        exec </dev/tty >/dev/tty 2>&1
        sudo disko "${disko_args[@]}"
      )
    fi
    ok "disko finished"
    PROFILE_DISKO_SECONDS=$((SECONDS - phase_started))
  fi

  stop_installer_prefetch
  section "hardware config"
  phase_started=$SECONDS
  sudo nixos-generate-config --no-filesystems --root /mnt
  sudo install -D -m 0644 /mnt/etc/nixos/hardware-configuration.nix \
    "$WORKTREE/machines/$HOST/hardware-configuration.nix"
  record_install_artifact "machines/$HOST/hardware-configuration.nix"
  ok "hardware-configuration.nix staged"
  PROFILE_HARDWARE_SECONDS=$((SECONDS - phase_started))

  section "nixos-install"
  local install_log=""
  install_log=$(mktemp)

  if [[ $OFFLINE_INSTALL -eq 1 ]]; then
    section "offline store copy"
    sudo mkdir -p /mnt/nix/store
    phase_started=$SECONDS
    if ! run_with_spinner "copying the prebuilt ${HOST} closure from the ISO" \
      copy_offline_store; then
      rm -f "$install_log"
      die "offline store copy failed"
    fi
    PROFILE_STORE_COPY_SECONDS=$((SECONDS - phase_started))

    # The registration file is world-readable; only nix-store needs root.
    phase_started=$SECONDS
    # shellcheck disable=SC2024
    sudo env NIX_STATE_DIR=/mnt/nix/var/nix \
      nix-store --load-db <"$OFFLINE_CLOSURE_INFO/registration"
    PROFILE_STORE_REGISTER_SECONDS=$((SECONDS - phase_started))

    phase_started=$SECONDS
    if ! sudo nixos-install \
        --no-channel-copy \
        --no-root-password \
        --option builders "" \
        --option substitute false \
        --root /mnt \
        --system "$OFFLINE_TOPLEVEL" \
        2>&1 | tee "$install_log"; then
      rm -f "$install_log"
      die "offline nixos-install failed"
    fi
    PROFILE_NIXOS_INSTALL_SECONDS=$((SECONDS - phase_started))
  else
    phase_started=$SECONDS
    if ! (
      cd "$WORKTREE"
      sudo nixos-install --root /mnt --flake ".#${HOST}" 2>&1 | tee "$install_log"
    ); then
      rm -f "$install_log"
      die "nixos-install failed"
    fi
    PROFILE_NIXOS_INSTALL_SECONDS=$((SECONDS - phase_started))
  fi

  if grep -Eq '^ERROR:' "$install_log"; then
    err "nixos-install reported errors despite exiting successfully:"
    grep -E '^ERROR:' "$install_log" | sed 's/^/    /' >&2
    rm -f "$install_log"
    die "nixos-install reported an installation error"
  fi

  rm -f "$install_log"
  ok "nixos-install finished"
}

copy_repo_to_target() {
  if [[ "$NIXOS_MODE" != "yes" && "$HOME_MODE" != "yes" ]]; then
    return 0
  fi

  if [[ $DRY_RUN -eq 1 ]]; then
    info "Dry run: skipping repo copy"
    return 0
  fi

  if [[ -z "$REPO_DEST" ]]; then
    if [[ "$NIXOS_MODE" == "yes" ]]; then
      REPO_DEST="/mnt/home/${USERNAME}/src/public/nix-config"
    else
      REPO_DEST="${HOME}/src/public/nix-config"
    fi
  fi

  COPY_REPO=${COPY_REPO:-yes}

  if [[ "$COPY_REPO" == "yes" ]]; then
    local git_source=""
    local owner_spec=""
    local repo_parent=""
    local staging_dest=""

    if git_source=$(git rev-parse --show-toplevel 2>/dev/null); then
      if [[ ! -f "$git_source/flake.nix" || ! -d "$git_source/.git" ]]; then
        git_source=""
      fi
    else
      git_source=""
    fi

    if [[ -n "$git_source" ]]; then
      local real_git_source=""
      local real_repo_dest=""
      real_git_source=$(realpath -m "$git_source")
      real_repo_dest=$(realpath -m "$REPO_DEST")
      if [[ "$real_git_source" == "$real_repo_dest" ]]; then
        ok "repo already present at ${REPO_DEST}"
        return 0
      fi
    fi

    repo_parent=$(dirname "$REPO_DEST")
    staging_dest="${REPO_DEST}.tmp.$$"

    if [[ "$NIXOS_MODE" == "yes" ]]; then
      sudo mkdir -p "$repo_parent"
      sudo rm -rf "$staging_dest"
      owner_spec="${USERNAME}:users"
    else
      mkdir -p "$repo_parent"
      rm -rf "$staging_dest"
      owner_spec="${USERNAME}:$(id -gn)"
    fi

    if [[ -n "$git_source" ]]; then
      if [[ "$NIXOS_MODE" == "yes" ]]; then
        run_with_spinner "cloning repo history → ${REPO_DEST}" \
          sudo git clone --quiet "$git_source" "$staging_dest"
      else
        run_with_spinner "cloning repo history → ${REPO_DEST}" \
          git clone --quiet "$git_source" "$staging_dest"
      fi

      for artifact in "${INSTALL_ARTIFACTS[@]}"; do
        if [[ -e "$WORKTREE/$artifact" ]]; then
          if [[ "$NIXOS_MODE" == "yes" ]]; then
            sudo install -D -m 0644 "$WORKTREE/$artifact" "$staging_dest/$artifact"
          else
            install -D -m 0644 "$WORKTREE/$artifact" "$staging_dest/$artifact"
          fi
        fi
      done
    else
      warn "local git checkout not detected; falling back to rsync snapshot copy"
      if [[ "$NIXOS_MODE" == "yes" ]]; then
        run_with_spinner "copying repo snapshot → ${REPO_DEST}" \
          sudo rsync -a --delete "$WORKTREE"/ "$staging_dest/"
      else
        run_with_spinner "copying repo snapshot → ${REPO_DEST}" \
          rsync -a --delete "$WORKTREE"/ "$staging_dest/"
      fi
    fi

    if [[ "$NIXOS_MODE" == "yes" ]]; then
      sudo rm -rf "$REPO_DEST"
      sudo mv "$staging_dest" "$REPO_DEST"

      # Fix ownership inside the target system, where the installed user exists.
      sudo nixos-enter --root /mnt -c "chown -R ${owner_spec} /home/${USERNAME}/src"
    else
      rm -rf "$REPO_DEST"
      mv "$staging_dest" "$REPO_DEST"
      chown -R "$owner_spec" "$REPO_DEST" 2>/dev/null || true
    fi
  fi
}

run_home_install() {
  if [[ "$HOME_MODE" != "yes" ]]; then
    return 0
  fi

  if [[ "$NIXOS_MODE" == "yes" ]]; then
    return 0
  fi

  if [[ $DRY_RUN -eq 1 ]]; then
    info "Dry run: skipping home-manager switch"
    return 0
  fi

  section "home-manager"
  info "switching ${USERNAME}@${HOST}"
  "${NIX_CMD[@]}" run nixpkgs#home-manager -- switch --flake "${WORKTREE}#${USERNAME}@${HOST}"
  ok "home-manager switch finished"
}

configure_home_default_shell() {
  if [[ "$HOME_MODE" != "yes" || "$NIXOS_MODE" == "yes" || $PORTABLE -eq 1 ]]; then
    return 0
  fi

  if [[ $DRY_RUN -eq 1 ]]; then
    info "Dry run: skipping default shell configuration"
    return 0
  fi

  local zsh_path="${HOME}/.nix-profile/bin/zsh"
  local current_shell=""

  if [[ ! -x "$zsh_path" ]]; then
    warn "zsh was not found at ${zsh_path}; leaving login shell unchanged"
    return 0
  fi

  current_shell=$(getent passwd "$USERNAME" | awk -F: '{print $7}')
  if [[ "$current_shell" == "$zsh_path" ]]; then
    ok "default shell already set to zsh"
    return 0
  fi

  section "default shell"

  if ! sudo -v; then
    warn "sudo authentication failed; leaving login shell unchanged"
    return 0
  fi

  if ! grep -Fxq "$zsh_path" /etc/shells 2>/dev/null; then
    info "adding ${zsh_path} to /etc/shells"
    printf '%s\n' "$zsh_path" | sudo tee -a /etc/shells >/dev/null
  fi

  info "setting ${USERNAME}'s login shell to ${zsh_path}"
  sudo chsh -s "$zsh_path" "$USERNAME"
  ok "default shell set to zsh"
}

configure_gnome_shell_extensions() {
  if [[ "$HOME_MODE" != "yes" || "$NIXOS_MODE" == "yes" || $PORTABLE -eq 1 ]]; then
    return 0
  fi

  if [[ $DRY_RUN -eq 1 ]]; then
    info "Dry run: skipping distro GNOME extension package setup"
    return 0
  fi

  if ! home_profile_supports_display_profile "$HOME_PROFILE"; then
    return 0
  fi

  if ! is_debian_like_host; then
    return 0
  fi

  if ! command -v apt-get >/dev/null 2>&1; then
    warn "apt-get not found; skipping distro GNOME extension package setup"
    return 0
  fi

  section "gnome shell extensions"

  if ! sudo -v; then
    warn "sudo authentication failed; leaving distro GNOME extensions uninstalled"
    return 0
  fi

  info "installing distro GNOME Shell extensions package for workspace-indicator"
  if ! apt_get_noninteractive update; then
    warn "failed to update apt metadata; leaving distro GNOME extensions uninstalled"
    return 0
  fi

  if ! apt_get_noninteractive install -y gnome-shell-extensions; then
    warn "failed to install gnome-shell-extensions; workspace-indicator may be unavailable"
    return 0
  fi

  remove_user_shadowed_gnome_extensions
  ok "distro GNOME Shell extensions installed"
  info "restart GNOME Shell or log out and back in for extension discovery to refresh"
}

configure_caps_remap() {
  if [[ "$HOME_MODE" != "yes" || "$NIXOS_MODE" == "yes" || $PORTABLE -eq 1 ]]; then
    return 0
  fi

  if [[ $DRY_RUN -eq 1 ]]; then
    info "Dry run: skipping Caps remap setup"
    return 0
  fi

  if [[ -z "$CAPS_REMAP" ]]; then
    if prompt_bool "configure Caps as tap Escape / hold Control with interception-tools?" yes; then
      CAPS_REMAP=yes
    else
      CAPS_REMAP=no
    fi
  fi

  if [[ "$CAPS_REMAP" != "yes" ]]; then
    return 0
  fi

  if ! is_debian_like_host; then
    warn "Caps remap setup currently supports Debian/Ubuntu hosts"
    return 0
  fi

  section "caps remap"

  if ! sudo -v; then
    warn "sudo authentication failed; leaving Caps remap unconfigured"
    return 0
  fi

  if ! command -v apt-get >/dev/null 2>&1; then
    warn "apt-get not found; skipping interception-tools setup"
    return 0
  fi

  info "updating apt package metadata"
  if ! apt_get_noninteractive update; then
    warn "failed to update apt metadata; leaving Caps remap unconfigured"
    return 0
  fi

  info "installing interception-tools"
  if ! apt_get_noninteractive install -y interception-tools interception-caps2esc; then
    warn "failed to install interception-tools; leaving Caps remap unconfigured"
    if command -v systemctl >/dev/null 2>&1; then
      sudo systemctl disable --now udevmon 2>/dev/null || true
    fi
    return 0
  fi

  if ! command -v interception >/dev/null 2>&1 ||
     ! command -v caps2esc >/dev/null 2>&1 ||
     ! command -v udevmon >/dev/null 2>&1 ||
     ! command -v uinput >/dev/null 2>&1; then
    warn "interception-tools commands are missing after apt install; leaving Caps remap unconfigured"
    if command -v systemctl >/dev/null 2>&1; then
      sudo systemctl disable --now udevmon 2>/dev/null || true
    fi
    return 0
  fi

  sudo mkdir -p /etc/interception/udevmon.d
  sudo rm -f /etc/dual-function-keys.yaml /etc/interception/udevmon.d/dual-function-keys.yaml

  sudo tee /etc/interception/udevmon.yaml >/dev/null <<'EOF'
- JOB: "interception -g $DEVNODE | caps2esc -m 1 | uinput -d $DEVNODE"
  DEVICE:
    EVENTS:
      EV_KEY: [KEY_CAPSLOCK]
EOF

  sudo groupadd -f input
  sudo groupadd -f uinput
  sudo usermod -aG input "$USERNAME"
  sudo usermod -aG uinput "$USERNAME"

  if command -v systemctl >/dev/null 2>&1; then
    if ! systemctl list-unit-files udevmon.service --no-pager 2>/dev/null | grep -q '^udevmon\.service'; then
      sudo tee /etc/systemd/system/udevmon.service >/dev/null <<'EOF'
[Unit]
Description=Monitor input devices for launching interception-tools jobs
Wants=systemd-udev-settle.service
After=systemd-udev-settle.service
Documentation=man:udev(7)

[Service]
ExecStart=/usr/bin/udevmon -c /etc/interception/udevmon.yaml
Nice=-20
Restart=on-failure
OOMScoreAdjust=-1000

[Install]
WantedBy=multi-user.target
EOF
    fi
    sudo systemctl daemon-reload
    sudo systemctl enable udevmon
    sudo systemctl restart udevmon
    ok "Caps remap configured with interception-tools"
  else
    warn "systemctl not found; Caps remap config written but udevmon was not started"
  fi

  info "log out and back in for input/uinput group membership to refresh"
}

# --------------------------------------------------------- portable helpers

setup_portable_env() {
  if [[ $PORTABLE -eq 0 ]]; then
    return 0
  fi

  section "portable shell"

  local np_runtime="${NP_RUNTIME:-bwrap}"
  local nix_portable_bin
  nix_portable_bin=$(command -v nix-portable 2>/dev/null || echo "$HOME/.local/bin/nix-portable")

  cat >"$HOME/hm-env" <<WRAPPER
#!/usr/bin/env bash
NP_RUNTIME=${np_runtime} exec "${nix_portable_bin}" nix run nixpkgs#zsh
WRAPPER
  chmod +x "$HOME/hm-env"
  ok "created ~/hm-env"
  info "run ${C_BOLD}~/hm-env${C_RESET} to enter your managed shell"
}

# --------------------------------------------------------------------- main

main() {
  parse_args "$@"

  if [[ $PORTABLE -eq 1 ]]; then
    NIXOS_MODE=no
    HOME_MODE=${HOME_MODE:-yes}
    require_cmd jq rsync awk sed nix
  else
    require_cmd jq rsync lsblk awk sed nix
  fi
  apply_colors

  script_banner
  resolve_target
  resolve_modes
  select_offline_target
  resolve_identity
  resolve_custom_profiles
  resolve_disk
  preflight_offline_target
  if [[ $OFFLINE_INSTALL -eq 1 && $DRY_RUN -eq 0 ]]; then
    start_installer_prefetch
  fi
  recap

  if ! prompt_bool "proceed?" yes; then
    die "aborted."
  fi

  INSTALL_STARTED_SECONDS=$SECONDS
  local phase_started=$SECONDS
  prepare_worktree
  prepare_target_config
  PROFILE_PREPARE_SECONDS=$((SECONDS - phase_started))

  if [[ $DRY_RUN -eq 1 ]]; then
    section "dry run · defs.nix"
    sed 's/^/  /' "$WORKTREE/machines/defs.nix"
    printf '\n'
    ok "dry run complete — no disks touched"
    return 0
  fi

  run_nixos_install
  phase_started=$SECONDS
  copy_repo_to_target
  configure_gnome_shell_extensions
  run_home_install
  configure_home_default_shell
  configure_caps_remap
  setup_portable_env
  PROFILE_FINISH_SECONDS=$((SECONDS - phase_started))

  section "done"
  ok "finished install for ${C_BOLD}${HOST}${C_RESET}"
  show_install_timing
  show_next_steps
}

main "$@"
