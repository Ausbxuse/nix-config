# lib.sh — shared shell utilities for nix-config scripts.
# Inlined at build time via @source_lib@ replacement in mkScriptApp.
#
# Scripts should set these globals BEFORE the lib is inlined (or accept defaults):
#   USE_COLOR   1|0   (default: 1, auto-set to 0 when NO_COLOR is set or stdout is not a TTY)
#   ASSUME_YES  1|0   (default: 0, skip confirmation prompts when 1)

: "${USE_COLOR:=1}"
: "${ASSUME_YES:=0}"

if [[ -n "${NO_COLOR:-}" ]] || [[ ! -t 1 ]]; then
  USE_COLOR=0
fi

# --------------------------------------------------------------------- colors

C_RESET="" C_DIM="" C_BOLD=""
C_RED="" C_GREEN="" C_YELLOW="" C_CYAN=""

_SYM_OK="✓" _SYM_WARN="!" _SYM_ERR="✗" _SYM_PROMPT="›"

apply_colors() {
  if [[ $USE_COLOR -eq 1 ]]; then
    C_RESET=$'\033[0m'
    C_DIM=$'\033[2m'
    C_BOLD=$'\033[1m'
    C_RED=$'\033[31m'
    C_GREEN=$'\033[32m'
    C_YELLOW=$'\033[33m'
    C_CYAN=$'\033[36m'
  else
    C_RESET="" C_DIM="" C_BOLD=""
    C_RED="" C_GREEN="" C_YELLOW="" C_CYAN=""
    _SYM_OK="+" _SYM_WARN="!" _SYM_ERR="x" _SYM_PROMPT=">"
  fi
}

apply_colors

# --------------------------------------------------------------------- output

banner() {
  local title=$1 desc=${2-}
  printf '\n%s%s==>%s %s%s%s' "${C_BOLD}" "${C_GREEN}" "${C_RESET}" "${C_BOLD}" "$title" "${C_RESET}"
  if [[ -n "$desc" ]]; then
    printf ' %s— %s%s' "${C_DIM}" "$desc" "${C_RESET}"
  fi
  printf '\n'
}
section() { printf '\n%s==>%s %s\n' "${C_GREEN}" "${C_RESET}" "$*"; }
info()    { printf '  %s%s%s\n' "${C_DIM}" "$*" "${C_RESET}"; }
ok()      { printf '  %s%s%s %s\n' "${C_GREEN}" "$_SYM_OK" "${C_RESET}" "$*"; }
warn()    { printf '  %s%s%s %s\n' "${C_YELLOW}" "$_SYM_WARN" "${C_RESET}" "$*" >&2; }
err()     { printf '  %s%s%s %s\n' "${C_RED}" "$_SYM_ERR" "${C_RESET}" "$*" >&2; }
die()     { err "$*"; exit 1; }

kv() {
  printf '  %s%-15s%s %s\n' "${C_DIM}" "$1" "${C_RESET}" "$2"
}

# -------------------------------------------------------------------- helpers

require_cmd() {
  local cmd
  local -a missing=()

  for cmd in "$@"; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      missing+=("$cmd")
    fi
  done

  if [[ ${#missing[@]} -gt 0 ]]; then
    die "Missing required command(s): ${missing[*]}"
  fi
}

require_tty() {
  if [[ ! -r /dev/tty ]]; then
    die "interactive TTY required"
  fi
}

# --------------------------------------------------------------------- spinner

run_with_spinner() {
  local msg=$1
  shift

  local frames='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
  if [[ $USE_COLOR -eq 0 ]]; then
    frames="|/-\\"
  fi

  local log
  log=$(mktemp)

  local tty_fd=""
  if [[ -w /dev/tty ]]; then
    exec {tty_fd}>/dev/tty
  fi

  "$@" >"$log" 2>&1 &
  local pid=$!
  local i=0
  local n=${#frames}
  local rc=0

  if [[ -n "$tty_fd" ]]; then
    while kill -0 "$pid" 2>/dev/null; do
      printf '\r  %s%s%s %s' \
        "${C_CYAN}" "${frames:i%n:1}" "${C_RESET}" "$msg" >&"$tty_fd"
      i=$((i + 1))
      sleep 0.1
    done
    printf '\r\033[K' >&"$tty_fd"
  else
    info "$msg"
  fi

  wait "$pid"
  rc=$?

  if [[ $rc -eq 0 ]]; then
    ok "$msg"
    rm -f "$log"
    if [[ -n "$tty_fd" ]]; then
      exec {tty_fd}>&-
    fi
    return 0
  fi

  err "$msg (exit $rc)"
  if [[ -s "$log" ]]; then
    sed 's/^/    /' "$log" >&2
  fi
  rm -f "$log"
  if [[ -n "$tty_fd" ]]; then
    exec {tty_fd}>&-
  fi
  return "$rc"
}

# --------------------------------------------------------------------- prompts

prompt_select() {
  local prompt=$1
  local default=$2
  shift 2

  local -a options=("$@")
  local n=${#options[@]}
  local reply
  local i
  local default_idx=0

  if [[ $n -eq 0 ]]; then
    die "prompt_select: no options provided for '$prompt'"
  fi

  require_tty

  if [[ $n -eq 2 ]] &&
     { [[ "${options[0],,}" == "yes" && "${options[1],,}" == "no" ]] ||
       [[ "${options[0],,}" == "no" && "${options[1],,}" == "yes" ]]; }; then
    while true; do
      printf '  %s%s%s %s%s%s [%s/%s] ' \
        "${C_BOLD}" "$prompt" "${C_RESET}" \
        "${C_CYAN}" "$_SYM_PROMPT" "${C_RESET}" \
        "$([[ "${default,,}" == yes ]] && printf Y || printf y)" \
        "$([[ "${default,,}" == no  ]] && printf N || printf n)" >&2
      read -r reply </dev/tty || die "no input"

      [[ -z "$reply" && "$default" == "yes" ]] && { printf 'yes\n'; return 0; }
      [[ -z "$reply" && "$default" == "no"  ]] && { printf 'no\n';  return 0; }
      [[ "$reply" =~ ^([Yy]|[Yy][Ee][Ss])$ ]] && { printf 'yes\n'; return 0; }
      [[ "$reply" =~ ^([Nn]|[Nn][Oo])$   ]] && { printf 'no\n';  return 0; }

      warn "Invalid selection: '$reply' — enter y or n."
    done
  fi
 
  printf '  %s%s%s\n' "${C_BOLD}" "$prompt" "${C_RESET}" >&2

  i=1
  for opt in "${options[@]}"; do
    if [[ "$opt" == "$default" ]]; then
      default_idx=$i
      printf '    %s%2d%s %s%s%s\n' \
        "${C_CYAN}" "$i" "${C_RESET}" "${C_GREEN}" "$opt" "${C_RESET}" >&2
    else
      printf '    %s%2d%s %s\n' "${C_CYAN}" "$i" "${C_RESET}" "$opt" >&2
    fi
    i=$((i + 1))
  done

  while true; do
    printf '  %s%s%s [1-%d] ' "${C_CYAN}" "$_SYM_PROMPT" "${C_RESET}" "$n" >&2
    read -r reply </dev/tty || die "no input"

    if [[ -z "$reply" && $default_idx -gt 0 ]]; then
      printf '%s\n' "${options[default_idx - 1]}"
      return 0
    fi

    if [[ "$reply" =~ ^[0-9]+$ ]] && (( reply >= 1 && reply <= n )); then
      printf '%s\n' "${options[reply - 1]}"
      return 0
    fi

    for opt in "${options[@]}"; do
      if [[ "$reply" == "$opt" ]]; then
        printf '%s\n' "$opt"
        return 0
      fi
    done

    warn "Invalid selection: '$reply' — enter a number 1-$n."
  done
}

prompt_text() {
  local prompt=$1
  local default=${2-}
  local validator=${3-}
  local reply

  require_tty

  while true; do
    if [[ -n "$default" ]]; then
      printf '  %s %s%s%s ' \
        "$prompt" "${C_CYAN}" "$_SYM_PROMPT" "${C_RESET}" >&2
      read -e -i "$default" -r reply </dev/tty || die "no input"
    else
      printf '  %s %s%s%s ' \
        "$prompt" "${C_CYAN}" "$_SYM_PROMPT" "${C_RESET}" >&2
      read -e -r reply </dev/tty || die "no input"
    fi

    reply=${reply:-$default}

    if [[ -z "$reply" ]]; then
      warn "value cannot be empty"
      continue
    fi

    if [[ -n "$validator" ]]; then
      if ! "$validator" "$reply"; then
        continue
      fi
    fi

    printf '%s\n' "$reply"
    return 0
  done
}

prompt_bool() {
  local prompt=$1
  local default=${2:-yes}
  local reply=""
  local hint

  if [[ $ASSUME_YES -eq 1 ]]; then
    return 0
  fi

  require_tty

  if [[ "$default" == "yes" ]]; then
    hint="Y/n"
  else
    hint="y/N"
  fi

  printf '  %s [%s] %s%s%s ' \
    "$prompt" "$hint" "${C_CYAN}" "$_SYM_PROMPT" "${C_RESET}" >&2

  if ! IFS= read -r reply </dev/tty; then
    err "failed to read confirmation from /dev/tty"
    return 1
  fi

  case "$reply" in
    "")
      if [[ "$default" == "yes" ]]; then return 0; else return 1; fi
      ;;
    [Yy]|[Yy][Ee][Ss]) return 0 ;;
    [Nn]|[Nn][Oo])      return 1 ;;
    *)
      warn "invalid response: '$reply'"
      return 1
      ;;
  esac
}

# ----------------------------------------------------------------- validators

validate_hostname() {
  if [[ "$1" =~ ^[a-zA-Z][a-zA-Z0-9-]*$ ]]; then
    return 0
  fi
  warn "hostname must start with a letter and contain only [A-Za-z0-9-]"
  return 1
}

validate_username() {
  if [[ "$1" =~ ^[a-z_][a-z0-9_-]*$ ]]; then
    return 0
  fi
  warn "username must start with a letter and contain only [a-z0-9_-]"
  return 1
}

validate_email() {
  if [[ "$1" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]]; then
    return 0
  fi
  warn "email must look like local-part@example.com"
  return 1
}

validate_swap_size() {
  if [[ "$1" =~ ^[0-9]+[KMGkmg]?$ ]]; then
    return 0
  fi
  warn "swap size must look like '32G', '512M', or '20M' (got '$1')"
  return 1
}

validate_ssh_dest() {
  if [[ "$1" =~ ^[a-zA-Z0-9._-]+@[a-zA-Z0-9._-]+(:[0-9]+)?$ ]]; then
    return 0
  fi
  warn "ssh destination must be user@host or user@host:port"
  return 1
}

# --------------------------------------------------------------------- detect

detect_system() {
  case "$(uname -m)" in
    x86_64)        printf 'x86_64-linux\n' ;;
    aarch64|arm64) printf 'aarch64-linux\n' ;;
    *)             die "Unsupported architecture: $(uname -m)" ;;
  esac
}

detect_ram_gib() {
  local kb
  kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)

  if [[ "$kb" -eq 0 ]]; then
    printf '8\n'
    return 0
  fi

  awk -v kb="$kb" 'BEGIN { printf "%d\n", int((kb + 1024*1024 - 1) / (1024*1024)) }'
}

# Only the live ISO supplies this optional helper. It reads compressed image
# pages into reclaimable cache while the installer waits for user input.
INSTALLER_PREFETCH_PID=""
start_installer_prefetch() {
  [[ "${NIXOS_INSTALLER_PREFETCH:-1}" != 0 ]] || return 0
  [[ -z "$INSTALLER_PREFETCH_PID" ]] || return 0
  [[ -r /iso/nix-store.squashfs ]] || return 0
  command -v installer-prefetch >/dev/null 2>&1 || return 0
  installer-prefetch </dev/null >/dev/null 2>&1 &
  INSTALLER_PREFETCH_PID=$!
}

stop_installer_prefetch() {
  if [[ -n "$INSTALLER_PREFETCH_PID" ]]; then
    local job=""
    # A completed worker may have been reaped during a long editor session.
    # Only signal a still-running job owned by this shell, never a reused PID.
    for job in $(jobs -pr); do
      if [[ "$job" == "$INSTALLER_PREFETCH_PID" ]]; then
        kill "$job" 2>/dev/null || true
      fi
    done
    wait "$INSTALLER_PREFETCH_PID" 2>/dev/null || true
    INSTALLER_PREFETCH_PID=""
  fi
}
