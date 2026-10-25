#!/usr/bin/env bash
# Install only this Nix-built service, without activating unrelated home changes.
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/agent-hub"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$state_dir" "$unit_dir"
nix build "$repo_dir#agent-hub-service" --out-link "$state_dir/service-package"
unit_file="$unit_dir/agent-hub.service"
if [[ -e "$unit_file" && ! -L "$unit_file" ]]; then
  cp -p "$unit_file" "$state_dir/service-backup-$(date +%s).service"
fi
ln -sfn "$state_dir/service-package/lib/systemd/user/agent-hub.service" "$unit_file"
systemctl --user daemon-reload
systemctl --user enable agent-hub.service
systemctl --user restart agent-hub.service
