#!/usr/bin/env bash
# Install only the controller backup timer; do not activate a full home profile.
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/agent-hub"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
backup_config="${XDG_CONFIG_HOME:-$HOME/.config}/agent-hub/backup/config.json"
test -f "$backup_config"
mkdir -p "$state_dir" "$unit_dir"
nix build "$repo_dir#agent-hub-backup-service" --out-link "$state_dir/backup-service-package"
for kind in service timer; do
  unit_file="$unit_dir/agent-hub-backup.$kind"
  if [[ -e "$unit_file" && ! -L "$unit_file" ]]; then
    cp -p "$unit_file" "$state_dir/backup-unit-$(date +%s).$kind"
  fi
  ln -sfn "$state_dir/backup-service-package/lib/systemd/user/agent-hub-backup.$kind" "$unit_file"
done
systemctl --user daemon-reload
systemctl --user enable --now agent-hub-backup.timer
