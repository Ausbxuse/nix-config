#!/usr/bin/env bash
# Recreate every Codex/Claude agent recorded in the latest tmux-resurrect save,
# each resumed in its own session, window and working directory, using the
# newest builds from sadjow's codex-cli-nix and claude-code-nix flakes.
#
# The save comes from continuum (every 5 minutes); resurrect-workbench-save.sh
# rewrites each agent pane's command to `codex resume <id>` / `claude --resume
# <id>`, which is what this script replays.
#
# Usage: agent-resurrect [-n|--dry-run] [--no-update] [-f|--file SAVE]
#   -n, --dry-run   print what would be launched, change nothing
#   --no-update     use the claude/codex on PATH instead of fetching the latest
#   -f, --file      a specific tmux_resurrect_*.txt (default: the last save)
set -euo pipefail

tmux_bin="${TMUX_BIN:-tmux}"
resurrect_dir="${TMUX_RESURRECT_DIR:-$HOME/.local/share/tmux/resurrect}"
claude_home="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
claude_flake="github:sadjow/claude-code-nix"
codex_flake="github:sadjow/codex-cli-nix"
uuid_re='[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'

save_file="$resurrect_dir/last"
dry_run=0
update=1

while (($#)); do
  case "$1" in
    -n | --dry-run) dry_run=1 ;;
    --no-update) update=0 ;;
    -f | --file)
      save_file="${2:?--file needs a path}"
      shift
      ;;
    -h | --help)
      sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "agent-resurrect: unknown argument: $1" >&2
      exit 2
      ;;
  esac
  shift
done

save_file="$(readlink -f "$save_file" 2>/dev/null || true)"
if [[ -z "$save_file" || ! -f "$save_file" ]]; then
  echo "agent-resurrect: no tmux-resurrect save found in $resurrect_dir" >&2
  exit 1
fi

# The newest build of a flake's default package; --refresh skips Nix's cached
# copy of the branch so "latest" really is the current upstream commit.
latest_bin() {
  local flake="$1" name="$2" out

  out="$(nix build --no-link --print-out-paths --refresh "$flake" 2>/dev/null | tail -n 1)" || true
  if [[ -n "$out" && -x "$out/bin/$name" ]]; then
    printf '%s\n' "$out/bin/$name"
  else
    echo "agent-resurrect: could not build $flake; falling back to $name on PATH" >&2
    command -v "$name"
  fi
}

# Conversation ids already open in a live agent, so a second copy is never
# started: ids in a codex/claude process's argv, the rollout files Codex holds
# open, and the ids live Claude processes record.
running_ids() {
  local proc pid fd

  for proc in /proc/[0-9]*; do
    case "$(cat "$proc/comm" 2>/dev/null)" in
      codex* | .codex* | claude* | .claude*) ;;
      *) continue ;;
    esac
    tr '\0' ' ' 2>/dev/null < "$proc/cmdline" | grep -oE "$uuid_re" || true
    for fd in "$proc"/fd/*; do
      fd="$(readlink "$fd" 2>/dev/null || true)"
      [[ "$fd" == */.codex/sessions/*.jsonl ]] && grep -oE "$uuid_re" <<<"${fd##*/}"
    done
  done
  for proc in "$claude_home"/sessions/*.json; do
    [[ -f "$proc" ]] || continue
    pid="$(basename "$proc" .json)"
    [[ -d "/proc/$pid" ]] || continue
    grep -oE "\"sessionId\":\"$uuid_re\"" "$proc" | grep -oE "$uuid_re" || true
  done
}

declare -A window_name running
while IFS=$'\t' read -r type session index name _; do
  [[ "$type" == window ]] && window_name["$session:$index"]="${name#:}"
done < "$save_file"
while read -r id; do
  running["$id"]=1
done < <(running_ids | sort -u)

# session, window index, window name, directory, agent kind, conversation id.
agents=()
skipped=()
while IFS=$'\t' read -r type session index _ _ _ _ dir _ _ command; do
  [[ "$type" == pane ]] || continue
  dir="${dir#:}" command="${command#:}"
  if [[ "$command" =~ ^codex\ resume\ ($uuid_re)$ ]]; then
    kind=codex
  elif [[ "$command" =~ ^claude\ --resume\ ($uuid_re)$ ]]; then
    kind=claude
  else
    [[ "$command" =~ ^(codex|claude)$ ]] && skipped+=("$session:$index ($command, no conversation yet)")
    continue
  fi
  id="${BASH_REMATCH[1]}"
  if [[ -n "${running[$id]:-}" ]]; then
    skipped+=("$session:$index ($kind $id, already running)")
    continue
  fi
  agents+=("$session"$'\t'"$index"$'\t'"${window_name["$session:$index"]:-$kind}"$'\t'"$dir"$'\t'"$kind"$'\t'"$id")
done < "$save_file"

echo "Save: $save_file"
for line in "${skipped[@]}"; do
  echo "  skip   $line"
done
if ((${#agents[@]} == 0)); then
  echo "Nothing to resurrect."
  exit 0
fi

if ((dry_run)); then
  claude_bin="claude (latest $claude_flake)" codex_bin="codex (latest $codex_flake)"
  ((update)) || claude_bin="$(command -v claude)" codex_bin="$(command -v codex)"
else
  if ((update)); then
    echo "Fetching the latest claude and codex..."
    claude_bin="$(latest_bin "$claude_flake" claude)"
    codex_bin="$(latest_bin "$codex_flake" codex)"
  else
    claude_bin="$(command -v claude)" codex_bin="$(command -v codex)"
  fi
  echo "  claude: $claude_bin"
  echo "  codex:  $codex_bin"
fi

for line in "${agents[@]}"; do
  IFS=$'\t' read -r session index name dir kind id <<<"$line"
  [[ -d "$dir" ]] || dir="$HOME"
  if [[ "$kind" == codex ]]; then
    agent_command="$(printf '%q resume %q' "$codex_bin" "$id")"
  else
    agent_command="$(printf '%q --resume %q' "$claude_bin" "$id")"
  fi
  # Drop back to a shell when the agent exits, like a normal pane.
  pane_command="$agent_command; exec \"\${SHELL:-bash}\" -l"

  echo "  launch $session:$index [$name] $kind $id  ($dir)"
  ((dry_run)) && continue

  if ! "$tmux_bin" has-session -t "=$session" 2>/dev/null; then
    "$tmux_bin" new-session -d -s "$session" -n "$name" -c "$dir" "$pane_command"
  elif "$tmux_bin" list-windows -t "=$session" -F '#{window_index}' | grep -qx "$index"; then
    # The original slot is taken; append rather than clobber a live window.
    "$tmux_bin" new-window -d -t "=$session:" -n "$name" -c "$dir" "$pane_command"
  else
    "$tmux_bin" new-window -d -t "=$session:$index" -n "$name" -c "$dir" "$pane_command"
  fi
done

((dry_run)) || echo "Resurrected ${#agents[@]} agent(s)."
