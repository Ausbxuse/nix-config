#!/usr/bin/env bash
# Run once on a worker its controller can't open connections to (razy: timy may not reach it).
#   join-over-ssh.sh [controller] [port]
# This machine's hub becomes a worker on loopback only, reached by the controller through the
# tunnel this machine opens (services.agent-hub.uplink: switch home-manager first). On the
# controller: the tunnel's key, allowed nothing but holding 127.0.0.1:<port> and freeing it
# once this machine has gone (asleep) without closing it, and this machine as a peer at
# http://127.0.0.1:<port>. What either side had before is kept beside it, not deleted.
set -euo pipefail
controller=${1:-zhenyu@100.64.0.4}
port=${2:-8766}
node=$(hostname -s)
cfg="${XDG_CONFIG_HOME:-$HOME/.config}/agent-hub"
state="${XDG_STATE_HOME:-$HOME/.local/state}/agent-hub"
key="$HOME/.ssh/agent-hub-uplink"
stamp=$(date +%Y%m%d-%H%M%S)

[[ -f $key ]] || ssh-keygen -q -t ed25519 -N '' -C "agent-hub-uplink@$node" -f "$key"
token=$(head -c 36 /dev/urandom | base64 | tr '+/' '-_' | tr -d '=\n')
bin=/run/current-system/sw/bin
gate="$bin/ss -Hltnp src 127.0.0.1:$port | $bin/grep -o 'sshd[-a-z]*.,pid=[0-9]*' | $bin/cut -d= -f2 | $bin/xargs -r $bin/kill"
line="restrict,port-forwarding,permitlisten=\"127.0.0.1:$port\",command=\"$gate\" $(cat "$key.pub")"

# The controller: the key and the peer. It answers with its own node id.
controller_id=$({
  printf 'NODE, PORT, TOKEN, LINE64, STAMP = "%s", %s, "%s", "%s", "%s"\n' \
    "$node" "$port" "$token" "$(printf '%s' "$line" | base64 -w0)" "$stamp"
  cat <<'PY'
import base64, json, os, shutil, subprocess
from pathlib import Path
LINE = base64.b64decode(LINE64).decode()
keys = Path.home() / '.ssh/authorized_keys'
if keys.exists():
    shutil.copy2(keys, f'{keys}.before-{STAMP}')
comment = f'agent-hub-uplink@{NODE}'
kept = [l for l in (keys.read_text().splitlines() if keys.exists() else []) if not l.rstrip().endswith(comment)]
keys.write_text('\n'.join(kept + [LINE]) + '\n')
keys.chmod(0o600)
path = Path.home() / '.config/agent-hub/node.json'
shutil.copy2(path, f'{path}.before-{STAMP}')
config = json.loads(path.read_text())
config['peers'] = [p for p in config.get('peers', []) if p.get('id') != NODE] + [
    {'id': NODE, 'label': NODE, 'role': 'worker', 'url': f'http://127.0.0.1:{PORT}', 'token': TOKEN}]
fresh = path.with_name('node.json.new')
fresh.write_text(json.dumps(config, indent=1) + '\n')
fresh.chmod(0o600)
os.replace(fresh, path)
subprocess.run(['systemctl', '--user', 'restart', 'agent-hub'], check=True)
print(config['node_id'])
PY
} | ssh "$controller" python3 -)

# Here: a worker on loopback only. A controller's state from before (razy's, until timy) is set aside.
systemctl --user stop agent-hub-uplink agent-hub 2>/dev/null || true
mkdir -p "$cfg"
if [[ -f $cfg/node.json ]]; then
  grep -q '"role": *"worker"' "$cfg/node.json" || { [[ -d $state ]] && mv "$state" "$state.before-$stamp"; }
  mv "$cfg/node.json" "$cfg/node.json.before-$stamp"
fi
uid=$(id -u)
(umask 077; cat > "$cfg/node.json" <<EOF
{
 "node_id": "$node",
 "label": "$node",
 "role": "worker",
 "tmux_socket": "/run/user/$uid/tmux-$uid/default",
 "supervisor_owner": "$node",
 "peers": [{"id": "$controller_id", "role": "controller", "token": "$token"}],
 "allowed_clients": ["127.0.0.1/32", "::1/128"]
}
EOF
)
systemctl --user daemon-reload
systemctl --user restart agent-hub agent-hub-uplink
echo "$node is a worker of $controller_id, through 127.0.0.1:$port there."
