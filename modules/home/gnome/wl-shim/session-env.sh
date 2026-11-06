# Sourced by the wl-copy/wl-paste stand-ins. Callers started outside the
# desktop session (tmux run-shell, panes restored at boot, services) lack
# XDG_CURRENT_DESKTOP or carry the Xwayland auth file of an earlier login;
# the real tool then maps its helper window. Take the session's own values.
session_env() {
  local line
  while IFS= read -r line; do
    case "$line" in
      XDG_CURRENT_DESKTOP=* | DISPLAY=* | XAUTHORITY=*) export "${line?}" ;;
    esac
  done < <(systemctl --user show-environment 2>/dev/null)
}
case "${XDG_CURRENT_DESKTOP:-}" in
  *GNOME*) [ -n "${DISPLAY:-}" ] && [ -r "${XAUTHORITY:-/nonexistent}" ] || session_env ;;
  *) session_env ;;
esac
