#!/usr/bin/env bash
# Status-bar origin label for one tmux client. SSH_CONNECTION in the session
# environment only records the most recent attach, so a local client would show
# user@host after anyone attached over SSH; ask the client process instead.
if tr '\0' '\n' <"/proc/${1:-0}/environ" 2>/dev/null | grep -q '^SSH_CONNECTION='; then
  printf '%s@%s ' "$USER" "$(hostname -s)"
else
  printf 'local '
fi
