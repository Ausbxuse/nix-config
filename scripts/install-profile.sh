#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

readonly INSTALLER_CONFIG_TEMPLATE='@installerConfigTemplate@'
readonly OFFLINE_INSTALL_MANIFEST='@offlineInstallManifest@'
readonly INSTALLER_EDITOR='@installerEditor@'

CONFIG_PATH="${NIXOS_INSTALLER_CONFIG:-${HOME}/nixos-install.conf}"
PROFILE_OVERRIDE=""
EDIT_ONLY=0
INTERACTIVE=0
NO_EDIT_PROMPT=0
declare -a INTERACTIVE_ARGS=()
declare -a INSTALL_CONFIG_ARGS=()

INSTALL_PROFILE="auto"
INSTALL_SOURCE="auto"
EDIT_BEFORE_INSTALL="ask"
DISK="auto"
COPY_REPO="yes"
REPO_DEST=""
SKIP_PARTITIONING="no"
DRY_RUN="no"
COLOR="yes"
ASSUME_INSTALL_YES="no"
SYSTEM=""
USERNAME=""
FULL_NAME=""
EMAIL=""
NIXOS_MODE="auto"
HOME_MODE="auto"
NIXOS_PROFILE=""
HOME_PROFILE=""
DISPLAY_PROFILE=""
INSTALL_LAYOUT=""
SWAP_SIZE=""
CAPS_REMAP="ask"
PORTABLE="no"
SELECTED_PROFILE=""
declare -a INSTALL_ARGS=()

@source_lib@

trap stop_installer_prefetch EXIT

script_banner() {
  banner "nixos profile installer" "hardware-aware offline installation"
}

usage() {
  cat <<EOF
Usage:
  install-profile [--config PATH] [--profile auto|razy|spacy] [-- OPTIONS]
  install-profile --edit [--config PATH]
  install-profile --interactive [install-config options]

Options:
  --config PATH       Use a different single-file installer configuration
  --profile NAME      Override install_profile from the file
  --edit              Edit the configuration with the packaged Neovim and exit
  --interactive       Run the original interactive install-config terminal UI
  --no-edit-prompt    Do not offer to edit before installing
  -- OPTIONS          Append low-level install-config options (except --host)
  -h, --help          Show this help

Default configuration: ${CONFIG_PATH}
EOF
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --config)
        CONFIG_PATH=${2:?missing value for --config}
        shift 2
        ;;
      --profile)
        PROFILE_OVERRIDE=${2:?missing value for --profile}
        shift 2
        ;;
      --edit)
        EDIT_ONLY=1
        shift
        ;;
      --interactive)
        INTERACTIVE=1
        shift
        INTERACTIVE_ARGS=("$@")
        break
        ;;
      --no-edit-prompt)
        NO_EDIT_PROMPT=1
        shift
        ;;
      --)
        shift
        INSTALL_CONFIG_ARGS=("$@")
        break
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        die "unknown argument: $1"
        ;;
    esac
  done
}

reset_config_values() {
  INSTALL_PROFILE="auto"
  INSTALL_SOURCE="auto"
  EDIT_BEFORE_INSTALL="ask"
  DISK="auto"
  COPY_REPO="yes"
  REPO_DEST=""
  SKIP_PARTITIONING="no"
  DRY_RUN="no"
  COLOR="yes"
  ASSUME_INSTALL_YES="no"
  SYSTEM=""
  USERNAME=""
  FULL_NAME=""
  EMAIL=""
  NIXOS_MODE="auto"
  HOME_MODE="auto"
  NIXOS_PROFILE=""
  HOME_PROFILE=""
  DISPLAY_PROFILE=""
  INSTALL_LAYOUT=""
  SWAP_SIZE=""
  CAPS_REMAP="ask"
  PORTABLE="no"
}

trim_whitespace() {
  local value=$1
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

load_config() {
  local line=""
  local key=""
  local value=""
  local line_number=0

  reset_config_values

  while IFS= read -r line || [[ -n "$line" ]]; do
    line_number=$((line_number + 1))
    line=${line%$'\r'}
    line=$(trim_whitespace "$line")

    if [[ -z "$line" || "$line" == \#* ]]; then
      continue
    fi
    if [[ "$line" != *=* ]]; then
      die "${CONFIG_PATH}:${line_number}: expected key=value"
    fi

    key=$(trim_whitespace "${line%%=*}")
    value=$(trim_whitespace "${line#*=}")

    case "$key" in
      install_profile)     INSTALL_PROFILE=$value ;;
      install_source)      INSTALL_SOURCE=$value ;;
      edit_before_install) EDIT_BEFORE_INSTALL=$value ;;
      disk)                DISK=$value ;;
      copy_repo)           COPY_REPO=$value ;;
      repo_dest)           REPO_DEST=$value ;;
      skip_partitioning)   SKIP_PARTITIONING=$value ;;
      dry_run)             DRY_RUN=$value ;;
      color)               COLOR=$value ;;
      assume_yes)          ASSUME_INSTALL_YES=$value ;;
      system)              SYSTEM=$value ;;
      username)            USERNAME=$value ;;
      name)                FULL_NAME=$value ;;
      email)               EMAIL=$value ;;
      nixos)               NIXOS_MODE=$value ;;
      home)                HOME_MODE=$value ;;
      nixos_profile)       NIXOS_PROFILE=$value ;;
      home_profile)        HOME_PROFILE=$value ;;
      display_profile)     DISPLAY_PROFILE=$value ;;
      install_layout)      INSTALL_LAYOUT=$value ;;
      swap_size)           SWAP_SIZE=$value ;;
      caps_remap)          CAPS_REMAP=$value ;;
      portable)            PORTABLE=$value ;;
      *) die "${CONFIG_PATH}:${line_number}: unknown option '${key}'" ;;
    esac
  done <"$CONFIG_PATH"

  validate_choice "install_profile" "$INSTALL_PROFILE" auto razy spacy
  validate_choice "install_source" "$INSTALL_SOURCE" auto offline online
  validate_choice "edit_before_install" "$EDIT_BEFORE_INSTALL" ask yes no
  validate_choice "copy_repo" "$COPY_REPO" ask yes no
  validate_choice "skip_partitioning" "$SKIP_PARTITIONING" yes no
  validate_choice "dry_run" "$DRY_RUN" yes no
  validate_choice "color" "$COLOR" yes no
  validate_choice "assume_yes" "$ASSUME_INSTALL_YES" yes no
  validate_choice "nixos" "$NIXOS_MODE" auto yes no
  validate_choice "home" "$HOME_MODE" auto yes no
  validate_choice "caps_remap" "$CAPS_REMAP" ask yes no
  validate_choice "portable" "$PORTABLE" yes no

  case "$DISK" in
    auto|prompt|/dev/*) ;;
    *) die "disk must be auto, prompt, or a /dev path (got '${DISK}')" ;;
  esac

  if [[ -n "$SYSTEM" && "$SYSTEM" != "x86_64-linux" && "$SYSTEM" != "aarch64-linux" ]]; then
    die "system must be blank, x86_64-linux, or aarch64-linux"
  fi
  if [[ -n "$SWAP_SIZE" ]] && ! validate_swap_size "$SWAP_SIZE"; then
    die "invalid swap_size: ${SWAP_SIZE}"
  fi
}

validate_choice() {
  local key=$1
  local value=$2
  shift 2
  local option=""
  local allowed=""

  for option in "$@"; do
    allowed+="${allowed:+ | }${option}"
    if [[ "$value" == "$option" ]]; then
      return 0
    fi
  done

  die "${key} must be one of: ${allowed} (got '${value}')"
}

ensure_config() {
  if [[ -e "$CONFIG_PATH" ]]; then
    [[ -f "$CONFIG_PATH" ]] || die "configuration is not a regular file: ${CONFIG_PATH}"
    return 0
  fi

  install -D -m 0644 "$INSTALLER_CONFIG_TEMPLATE" "$CONFIG_PATH"
  ok "created editable configuration at ${CONFIG_PATH}"
}

edit_config() {
  info "editing ${CONFIG_PATH} with the declarative nix-config Neovim setup"
  "$INSTALLER_EDITOR" "$CONFIG_PATH"
}

detect_nvidia_display() {
  local pci_root="${NIXOS_INSTALLER_PCI_ROOT:-/sys/bus/pci/devices}"
  local device=""
  local vendor=""
  local class=""

  for device in "$pci_root"/*; do
    [[ -r "$device/vendor" && -r "$device/class" ]] || continue
    vendor=$(<"$device/vendor")
    class=$(<"$device/class")
    if [[ "${vendor,,}" == "0x10de" && "${class,,}" == 0x03* ]]; then
      return 0
    fi
  done

  return 1
}

target_is_embedded() {
  jq -e --arg target "$1" '.targets[$target] != null' "$OFFLINE_INSTALL_MANIFEST" >/dev/null 2>&1
}

select_profile() {
  local recommended=$1
  local selected="${PROFILE_OVERRIDE:-$INSTALL_PROFILE}"

  validate_choice "profile" "$selected" auto razy spacy
  if [[ "$selected" == "auto" ]]; then
    selected=$recommended
  fi

  if [[ "$selected" != "$recommended" ]]; then
    warn "configured profile '${selected}' differs from hardware recommendation '${recommended}'"
  fi

  kv "selected" "$selected"
  if ! prompt_bool "Use the '${selected}' install profile?" yes; then
    selected=$(prompt_select "choose an installer" "$recommended" razy spacy interactive)
    if [[ "$selected" == "interactive" ]]; then
      stop_installer_prefetch
      exec install-config
    fi
  fi

  SELECTED_PROFILE=$selected
}

append_optional_value() {
  local flag=$1
  local value=$2
  if [[ -n "$value" ]]; then
    INSTALL_ARGS+=("$flag" "$value")
  fi
}

run_profile_install() {
  local selected=$1
  local arg=""
  INSTALL_ARGS=(--host "$selected")

  for arg in "${INSTALL_CONFIG_ARGS[@]}"; do
    if [[ "$arg" == "--host" ]]; then
      die "--host cannot override the affirmed profile; use --profile instead"
    fi
  done

  case "$INSTALL_SOURCE" in
    auto) ;;
    offline)
      target_is_embedded "$selected" || die "no embedded offline target for '${selected}'"
      INSTALL_ARGS+=(--offline)
      ;;
    online) INSTALL_ARGS+=(--online) ;;
  esac

  case "$DISK" in
    auto) ;;
    prompt) INSTALL_ARGS+=(--ask-disk) ;;
    /dev/*) INSTALL_ARGS+=(--disk "$DISK") ;;
  esac

  case "$NIXOS_MODE" in
    auto) ;;
    yes) INSTALL_ARGS+=(--nixos) ;;
    no) INSTALL_ARGS+=(--no-nixos) ;;
  esac
  case "$HOME_MODE" in
    auto) ;;
    yes) INSTALL_ARGS+=(--home) ;;
    no) INSTALL_ARGS+=(--no-home) ;;
  esac

  append_optional_value --system "$SYSTEM"
  append_optional_value --username "$USERNAME"
  append_optional_value --name "$FULL_NAME"
  append_optional_value --email "$EMAIL"
  append_optional_value --nixos-profile "$NIXOS_PROFILE"
  append_optional_value --home-profile "$HOME_PROFILE"
  append_optional_value --display-profile "$DISPLAY_PROFILE"
  append_optional_value --install-layout "$INSTALL_LAYOUT"
  append_optional_value --swap-size "$SWAP_SIZE"
  append_optional_value --repo-dest "$REPO_DEST"

  if [[ "$COPY_REPO" != "ask" ]]; then
    INSTALL_ARGS+=(--copy-repo "$COPY_REPO")
  fi
  if [[ "$CAPS_REMAP" != "ask" ]]; then
    INSTALL_ARGS+=(--caps-remap "$CAPS_REMAP")
  fi
  if [[ "$SKIP_PARTITIONING" == "yes" ]]; then
    INSTALL_ARGS+=(--skip-partitioning)
  fi
  if [[ "$DRY_RUN" == "yes" ]]; then
    INSTALL_ARGS+=(--dry-run)
  fi
  if [[ "$COLOR" == "no" ]]; then
    INSTALL_ARGS+=(--no-color)
  fi
  if [[ "$ASSUME_INSTALL_YES" == "yes" ]]; then
    INSTALL_ARGS+=(--yes)
  fi
  if [[ "$PORTABLE" == "yes" ]]; then
    if [[ "$INSTALL_SOURCE" != "online" ]]; then
      die "portable=yes requires install_source=online"
    fi
    INSTALL_ARGS+=(--portable)
  fi

  INSTALL_ARGS+=("${INSTALL_CONFIG_ARGS[@]}")

  stop_installer_prefetch
  exec install-config "${INSTALL_ARGS[@]}"
}

main() {
  parse_args "$@"

  if [[ $INTERACTIVE -eq 1 ]]; then
    exec install-config "${INTERACTIVE_ARGS[@]}"
  fi

  require_cmd install jq nvim install-config
  apply_colors
  script_banner
  ensure_config

  if [[ $EDIT_ONLY -eq 1 ]]; then
    edit_config
    return 0
  fi

  load_config

  local prefetch_allowed=yes
  local arg=""
  for arg in "${INSTALL_CONFIG_ARGS[@]}"; do
    case "$arg" in
      --online|--dry-run|--no-nixos|--portable) prefetch_allowed=no ;;
    esac
  done
  if [[ "$prefetch_allowed" == yes && "$INSTALL_SOURCE" != online && "$DRY_RUN" == no && "$NIXOS_MODE" != no && "$PORTABLE" == no ]]; then
    start_installer_prefetch
  fi

  local recommended="spacy"
  local nvidia="no"
  if detect_nvidia_display; then
    recommended="razy"
    nvidia="yes"
  fi

  section "hardware detection"
  kv "NVIDIA display" "$nvidia"
  kv "recommended" "$recommended"
  info "Razy enables NVIDIA PRIME offload; Spacy contains no NVIDIA configuration."

  if [[ $NO_EDIT_PROMPT -eq 0 ]]; then
    case "$EDIT_BEFORE_INSTALL" in
      yes) edit_config; load_config ;;
      ask)
        if prompt_bool "Edit installer options before continuing?" no; then
          edit_config
          load_config
        fi
        ;;
      no) ;;
    esac
  fi

  section "profile affirmation"
  select_profile "$recommended"
  local selected=$SELECTED_PROFILE
  target_is_embedded "$selected" \
    || warn "'${selected}' is not embedded; use install_source=online or rebuild the ISO"

  run_profile_install "$selected"
}

main "$@"
