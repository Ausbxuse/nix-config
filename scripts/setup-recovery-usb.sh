#!/usr/bin/env bash
# setup-recovery-usb: partition and format a USB drive for recovery backups.
#
# Creates two partitions:
#   1. up to 32 GiB FAT32 (label: NIX_INSTALL) — bootable offline installer ISO
#   2. Remaining ext4     (label: RECOVERY)    — bundle + restic + media
#
# Usage:
#   nix run .#setup-recovery-usb

set -euo pipefail

@source_lib@

# --------------------------------------------------------------------- main

require_root() {
  if [[ $EUID -ne 0 ]]; then
    die "must run as root (use sudo)"
  fi
}

require_root

section "pick removable disk"

declare -a DISK_PATHS=() DISK_LABELS=()
while IFS=$'\t' read -r path type rm size model tran; do
  [[ "$type" == "disk" && "$rm" == "1" ]] || continue
  DISK_PATHS+=("$path")
  DISK_LABELS+=("$(printf '%-14s %-8s %s %s' "$path" "$size" "${model:-?}" "${tran:-?}")")
done < <(lsblk -ndP -o PATH,TYPE,RM,SIZE,MODEL,TRAN 2>/dev/null \
  | sed 's/ *\([A-Z]\{1,\}\)="/\t\1="/g; s/"//g; s/^[A-Z]\{1,\}=//; s/\t[A-Z]\{1,\}=/\t/g')

if [[ ${#DISK_PATHS[@]} -eq 0 ]]; then
  die "no removable disks found"
fi

for label in "${DISK_LABELS[@]}"; do
  printf '  %s%s%s\n' "${C_DIM}" "$label" "${C_RESET}"
done

default_label=""
if [[ ${#DISK_PATHS[@]} -eq 1 ]]; then
  default_label=${DISK_LABELS[0]}
fi
chosen=$(prompt_select "target disk" "$default_label" "${DISK_LABELS[@]}")
DISK=""
for i in "${!DISK_LABELS[@]}"; do
  if [[ "${DISK_LABELS[i]}" == "$chosen" ]]; then
    DISK=${DISK_PATHS[i]}; break
  fi
done

[[ -b "$DISK" ]] || die "not a block device: $DISK"

section "destructive"
warn "this will DESTROY all data on ${C_BOLD}${DISK}${C_RESET}"
if ! prompt_bool "proceed?" no; then
  die "aborted."
fi

section "partitioning"
info "wiping $DISK"
sgdisk --zap-all "$DISK" || true
partprobe "$DISK" 2>/dev/null || sleep 2

DISK_BYTES=$(blockdev --getsize64 "$DISK")
DISK_GIB=$(( DISK_BYTES / 1073741824 ))
DESIRED_INSTALL_GIB=${NIX_INSTALL_GIB:-32}
if [[ ! "$DESIRED_INSTALL_GIB" =~ ^[0-9]+$ ]] || (( DESIRED_INSTALL_GIB < 1 )); then
  die "NIX_INSTALL_GIB must be a positive integer"
fi
if (( DISK_GIB < 17 )); then
  die "disk too small (${DISK_GIB} GiB); the current offline image needs a 16 GiB installer partition plus recovery space"
fi
# The embedded razy closure is substantially larger than a stock live image.
# Prefer 32 GiB, but always leave at least 1 GiB for recovery data.
INSTALL_GIB=$(( DISK_GIB > DESIRED_INSTALL_GIB + 1 ? DESIRED_INSTALL_GIB : DISK_GIB - 1 ))
if (( INSTALL_GIB < DESIRED_INSTALL_GIB )); then
  warn "installer partition is only ${INSTALL_GIB} GiB; verify the built ISO fits before writing it"
fi

info "creating partition 1: ${INSTALL_GIB} GiB FAT32 (NIX_INSTALL)"
sgdisk -n 1:0:+${INSTALL_GIB}G -t 1:EF00 -c 1:NIX_INSTALL "$DISK"

info "creating partition 2: remaining ext4 (RECOVERY)"
sgdisk -n 2:0:0 -t 2:8300 -c 2:RECOVERY "$DISK"

partprobe "$DISK" 2>/dev/null || sleep 2

# Detect partition device names (handles nvme-style names)
PART1="${DISK}1"
PART2="${DISK}2"
if [[ ! -b "$PART1" ]]; then
  PART1="${DISK}p1"
  PART2="${DISK}p2"
fi
[[ -b "$PART1" ]] || die "cannot find partition 1 at ${DISK}1 or ${DISK}p1"
[[ -b "$PART2" ]] || die "cannot find partition 2 at ${DISK}2 or ${DISK}p2"

for p in "$PART1" "$PART2"; do
  if mountpoint -q "$p" 2>/dev/null || findmnt -rn "$p" >/dev/null 2>&1; then
    info "unmounting $p"
    umount "$p"
  fi
done

section "formatting"
info "formatting $PART1 as FAT32"
mkfs.vfat -F 32 -n NIX_INSTALL "$PART1"

info "formatting $PART2 as ext4"
mkfs.ext4 -L RECOVERY "$PART2"
ok "partitioning complete"

section "restic init"
MOUNT_DIR=$(mktemp -d)
trap 'umount "$MOUNT_DIR" 2>/dev/null || true; rmdir "$MOUNT_DIR" 2>/dev/null || true' EXIT

mount "$PART2" "$MOUNT_DIR"
mkdir -p "$MOUNT_DIR/restic" "$MOUNT_DIR/media"

info "set a restic repository password"
while true; do
  read -r -s -p "  enter password: " RESTIC_PW </dev/tty; printf '\n'
  [[ -n "$RESTIC_PW" ]] || { warn "password cannot be empty"; continue; }
  read -r -s -p "  confirm password: " confirm </dev/tty; printf '\n'
  if [[ "$RESTIC_PW" == "$confirm" ]]; then break; fi
  warn "passwords did not match"
done

RESTIC_PASSWORD="$RESTIC_PW" restic init --repo "$MOUNT_DIR/restic"
ok "restic repo initialized"

umount "$MOUNT_DIR"
rmdir "$MOUNT_DIR" 2>/dev/null || true
trap - EXIT

section "done"
RECOVERY_UUID=$(blkid -s UUID -o value "$PART2")
ok "RECOVERY partition UUID: ${C_BOLD}${RECOVERY_UUID}${C_RESET}"
printf '\n'
info "next steps:"
info "  1. paste UUID into the target host's private hosts.nix entry as recovery.partUuid"
info "  2. add restic password to nix-secrets/secrets.yaml as recovery-restic-password"
info "  3. rebuild: sudo nixos-rebuild switch --flake .#\$(hostname)"
