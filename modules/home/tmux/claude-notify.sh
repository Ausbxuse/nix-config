#!/usr/bin/env bash
# Claude Code lifecycle hook -> workbench window state.
#
# Registered (by home.activation.configureClaudeHooks) for SessionStart,
# UserPromptSubmit, PreToolUse, Notification, Stop, StopFailure and SessionEnd
# in ~/.claude/settings.json. Claude Code pipes the event as JSON on stdin and
# hooks inherit the pane's environment, so $TMUX_PANE points at the window to
# mark. This replaces pane/transcript scraping as the source of truth:
# workbench.sh sync skips windows carrying @agent-hook-pid while that process
# is alive (see sync_agent_states).
set -euo pipefail

[[ -n "${TMUX_PANE:-}" ]] || exit 0
command -v tmux >/dev/null 2>&1 || exit 0

payload=""
[[ -t 0 ]] || payload="$(cat || true)"

json_field() {
  printf '%s' "$payload" | sed -n 's/.*"'"$1"'"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1
}

event="$(json_field hook_event_name)"
[[ -n "$event" ]] || exit 0

# The claude process this event belongs to: nearest ancestor that is claude
# (or node, for npm installs). Guards against a nested `claude -p` launched
# from inside a tool call stealing the window's state: whichever claude
# registered first keeps it while it lives.
owner_pid=""
pid="$$"
for _ in 1 2 3 4 5 6; do
  pid="$(awk '{print $4}' "/proc/$pid/stat" 2>/dev/null || true)"
  [[ -n "$pid" && "$pid" != 0 && "$pid" != 1 ]] || break
  case "$(cat "/proc/$pid/comm" 2>/dev/null || true)" in
    claude* | .claude* | node) owner_pid="$pid"; break ;;
  esac
done
[[ -n "$owner_pid" ]] || owner_pid="$PPID"

registered="$(tmux show-options -wqv -t "$TMUX_PANE" @agent-hook-pid 2>/dev/null || true)"
if [[ -n "$registered" && "$registered" != "$owner_pid" && -d "/proc/$registered" ]]; then
  exit 0
fi

state=""
summary=""
case "$event" in
  SessionStart)
    state=idle summary=ready
    ;;
  UserPromptSubmit)
    state=running summary=working
    ;;
  # Covers resuming after an approval or a deny-with-message, which emit no
  # dedicated event: the next tool call is the first sign Claude moved on.
  PreToolUse)
    state=running summary=working
    ;;
  Notification)
    case "$(json_field notification_type)" in
      permission_prompt) state=waiting summary="needs approval" ;;
      elicitation_dialog | elicitation_url_dialog | agent_needs_input)
        state=waiting summary="needs input"
        ;;
      # idle_prompt fires a minute after Stop already marked the window done;
      # the rest (auth, quota) say nothing about this window's work.
      *) exit 0 ;;
    esac
    ;;
  Stop)
    state=done summary="needs review"
    ;;
  StopFailure)
    state=blocked summary=error
    ;;
  SessionEnd)
    tmux set-option -w -t "$TMUX_PANE" -u @agent-hook-pid 2>/dev/null || true
    state=idle summary=ready
    ;;
  *)
    exit 0
    ;;
esac

if [[ "$event" != SessionEnd ]]; then
  tmux set-option -w -t "$TMUX_PANE" @agent-hook-pid "$owner_pid" 2>/dev/null || true
fi

TMUX_WORKBENCH_TARGET_PANE="$TMUX_PANE" \
  "$HOME/.local/bin/tmux/workbench.sh" state "$state" "$summary" 2>/dev/null || true
