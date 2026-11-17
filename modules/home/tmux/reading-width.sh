#!/usr/bin/env bash
# Keep agent text readable on wide terminals, like a blog post: the agent pane
# of each workbench window is held at most @reading-width cells wide (default
# 80) and @reading-width-mm on screen (default 200 mm, a blog column's physical
# width; measured from the local panel's EDID, skipped for SSH clients).
#
# The width left over goes to a shell on the left (@pane-role side-terminal,
# opened in the task root), so the agent column sits off the left edge. When
# the terminal is too narrow for a useful side shell, the agent pane is zoomed
# instead and the shell waits behind it. Windows with panes of their own, or
# zoomed by hand, are left alone. Hooks in tmux.conf call this on resizes,
# attaches, window switches and layout changes.
set -euo pipefail

tmux_bin="${TMUX_BIN:-tmux}"
min_side=30

# Hooks fire in bursts; one run at a time, or two would each add a side shell.
exec 9>"${XDG_RUNTIME_DIR:-/tmp}/tmux-reading-width.lock"
flock 9

max_cols="$("$tmux_bin" show -gqv @reading-width)"
[[ "$max_cols" =~ ^[0-9]+$ ]] || max_cols=80
max_mm="$("$tmux_bin" show -gqv @reading-width-mm)"
[[ "$max_mm" =~ ^[0-9]+$ ]] || max_mm=200

# Millimetres per pixel of the local panel: EDID width (cm) over its widest mode.
panel_mm_per_px() {
  local edid cm px
  for edid in /sys/class/drm/card*-*/edid; do
    [[ "$(cat "${edid%/edid}/status" 2>/dev/null)" == connected ]] || continue
    cm="$(od -An -tu1 -j21 -N1 "$edid" 2>/dev/null | tr -d ' ')"
    px="$(head -n1 "${edid%/edid}/modes" 2>/dev/null | cut -dx -f1)"
    [[ "$cm" =~ ^[1-9][0-9]*$ && "$px" =~ ^[1-9][0-9]*$ ]] || continue
    awk -v cm="$cm" -v px="$px" 'BEGIN { printf "%.5f\n", cm * 10 / px }'
    return 0
  done
  return 1
}

viewed_over_ssh() {
  tr '\0' '\n' <"/proc/$1/environ" 2>/dev/null | grep -q '^SSH_CONNECTION='
}

# An earlier version narrowed windows with a scrollbar margin; undo that.
drop_old_layout() {
  local w="$1" opt
  for opt in window-size pane-scrollbars pane-scrollbars-position pane-scrollbars-style \
    fill-character pane-border-lines @reading-layout; do
    "$tmux_bin" set -wu -t "$w" "$opt" 2>/dev/null || true
  done
}

fit_window() {
  local w="$1" width="$2" cols="$3" agent="$4" root="$5" zoomed="$6" ours="$7"
  local pane role pane_w side="" agent_w="" others=0 spare

  while IFS='|' read -r pane role pane_w; do
    if [[ "$pane" == "$agent" ]]; then
      agent_w="$pane_w"
    elif [[ "$role" == side-terminal && -z "$side" ]]; then
      side="$pane"
    else
      others=1
    fi
  done < <("$tmux_bin" list-panes -t "$w" -F '#{pane_id}|#{@pane-role}|#{pane_width}')

  [[ -n "$agent_w" && "$others" == 0 ]] || return 0
  # A zoom we did not make is the user's; leave it.
  [[ "$zoomed" == 1 && -z "$ours" ]] && return 0

  spare=$((width - cols - 1))
  if ((spare >= min_side)); then
    if [[ "$zoomed" == 1 ]]; then
      "$tmux_bin" resize-pane -Z -t "$agent" \; set -wu -t "$w" @reading-zoomed
    fi
    if [[ -z "$side" ]]; then
      side="$("$tmux_bin" split-window -hbd -l "$spare" -t "$agent" -c "$root" -P -F '#{pane_id}')"
      "$tmux_bin" set -p -t "$side" @pane-role side-terminal \; \
        set -p -t "$side" @workbench-window "$w"
    elif [[ "$agent_w" != "$cols" ]]; then
      "$tmux_bin" resize-pane -t "$agent" -x "$cols"
    fi
    # The divider stays the plain border colour whichever side is focused.
    "$tmux_bin" set -w -t "$w" pane-active-border-style '#{pane-border-style}'
  elif [[ -n "$side" && "$zoomed" != 1 ]]; then
    "$tmux_bin" resize-pane -Z -t "$agent" \; set -w -t "$w" @reading-zoomed 1
  fi
}

mm_per_px="$(panel_mm_per_px || true)"

# The most recently active client of each session decides its layout.
declare -A client_w client_cell client_pid
while read -r _ session width cell pid; do
  [[ -n "${client_w[$session]:-}" ]] && continue
  client_w[$session]="$width"
  client_cell[$session]="$cell"
  client_pid[$session]="$pid"
done < <("$tmux_bin" list-clients -F '#{client_activity} #{client_session} #{client_width} #{client_cell_width} #{client_pid}' | sort -rn)

for session in "${!client_w[@]}"; do
  [[ "$session" == *-terms ]] && continue
  width="${client_w[$session]}"

  cols="$max_cols"
  cell="${client_cell[$session]}"
  if [[ -n "$mm_per_px" && "$cell" =~ ^[1-9][0-9]*$ ]] && ! viewed_over_ssh "${client_pid[$session]}"; then
    fit="$(awk -v mm="$max_mm" -v cell="$cell" -v r="$mm_per_px" 'BEGIN { print int(mm / (cell * r)) }')"
    ((fit > 0 && fit < cols)) && cols="$fit"
  fi

  while IFS='|' read -r w root agent zoomed ours old; do
    [[ -n "$old" ]] && drop_old_layout "$w"
    [[ -n "$root" && -d "$root" && -n "$agent" ]] || continue
    fit_window "$w" "$width" "$cols" "$agent" "$root" "$zoomed" "$ours"
  done < <("$tmux_bin" list-windows -t "$session" -F '#{window_id}|#{@task-root}|#{@agent-pane}|#{window_zoomed_flag}|#{@reading-zoomed}|#{@reading-layout}')
done
