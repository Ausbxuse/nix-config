#!/usr/bin/env bash
# Keep agent text readable on wide terminals, like a blog post: the agent pane
# of each workbench window is held at most @reading-width cells wide (default
# 80) and @reading-width-mm on screen (default 200 mm, a blog column's physical
# width; measured from the local panel's EDID, skipped for SSH clients).
#
# The agent column is centred where there is room: a left column holds a
# shell opened in the task root (@pane-role side-terminal), and the window is
# narrowed so the same width stays blank on the right. On narrower terminals
# the left column just takes the spare width, and below $min_side spare cells
# the agent pane is zoomed with the shell waiting behind it.
#
# Each agent window's left column has an agent list (agent-bar.sh, @pane-role
# agent-bar) above the shell. Windows with panes of their own, or zoomed by
# hand, are left alone. Hooks in tmux.conf call this on resizes,
# attaches, window switches and layout changes.
set -euo pipefail

tmux_bin="${TMUX_BIN:-tmux}"
agent_bar="${AGENT_BAR:-$HOME/.local/bin/tmux/agent-bar.sh}"
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
  for opt in window-size fill-character pane-scrollbars pane-scrollbars-position \
    pane-scrollbars-style pane-border-lines @reading-layout; do
    "$tmux_bin" set -wu -t "$w" "$opt" 2>/dev/null || true
  done
}

# Hand a window's size back to tmux.
unnarrow() {
  local w="$1"
  "$tmux_bin" set -wu -t "$w" window-size \; set -wu -t "$w" fill-character \; \
    set -wu -t "$w" @reading-narrow \; set -wu -t "$w" @reading-border \; \
    set -wu -t "$w" pane-active-border-style 2>/dev/null || true
}

# Lay out one agent window. Its size is set here up front, not left to tmux,
# so every window already fits the terminal before it is shown: nothing
# reflows or redraws on a window switch.
fit_window() {
  local w="$1" width="$2" height="$3" cols="$4" agent="$5" root="$6" zoomed="$7" ours="$8" narrow="$9"
  local bar_h="${10}" border="${11}"
  local pane role pane_w pane_h start dead side="" bar="" bar_cur_h="" bar_ok=0 agent_w="" others=0
  local spare left target

  while IFS='|' read -r pane role pane_w pane_h start dead; do
    if [[ "$pane" == "$agent" ]]; then
      agent_w="$pane_w"
    elif [[ "$role" == side-terminal && -z "$side" ]]; then
      side="$pane"
    elif [[ "$role" == agent-bar && -z "$bar" ]]; then
      bar="$pane" bar_cur_h="$pane_h"
      [[ "$start" == *agent-bar.sh* && "$dead" != 1 ]] && bar_ok=1
    else
      others=1
    fi
  done < <("$tmux_bin" list-panes -t "$w" -F '#{pane_id}|#{@pane-role}|#{pane_width}|#{pane_height}|#{pane_start_command}|#{pane_dead}')

  if [[ -z "$agent_w" || "$others" == 1 || ("$zoomed" == 1 && -z "$ours") ]]; then
    [[ -n "$narrow" ]] && unnarrow "$w"
    return 0
  fi

  # Centre the agent column when the left half has room for a usable shell;
  # otherwise the left column takes the spare width, and below $min_side the
  # agent pane is zoomed over it.
  spare=$((width - cols - 1))
  left=$(((width - cols) / 2))
  ((left >= min_side)) || left="$spare"
  target="$width"
  ((spare >= min_side)) && target=$((left + 1 + cols))

  if [[ "$narrow" != "$target:$height" ]]; then
    "$tmux_bin" set -w -t "$w" fill-character ' ' \; \
      set -w -t "$w" @reading-narrow "$target:$height" \; \
      resize-window -t "$w" -x "$target" -y "$height"
  fi

  if ((spare < min_side)); then
    if [[ -n "$side" && "$zoomed" != 1 ]]; then
      "$tmux_bin" resize-pane -Z -t "$agent" \; set -w -t "$w" @reading-zoomed 1
    fi
    return 0
  fi
  if [[ "$zoomed" == 1 ]]; then
    "$tmux_bin" resize-pane -Z -t "$agent" \; set -wu -t "$w" @reading-zoomed
  fi

  if [[ -z "$side" ]]; then
    side="$("$tmux_bin" split-window -hbd -l "$left" -t "$agent" -c "$root" -P -F '#{pane_id}')"
    "$tmux_bin" set -p -t "$side" @pane-role side-terminal \; \
      set -p -t "$side" @workbench-window "$w"
  elif [[ "$agent_w" != "$cols" ]]; then
    "$tmux_bin" resize-pane -t "$agent" -x "$cols"
  fi

  # The agent list sits above the shell. A session restore brings it back as
  # a plain shell, and a dead list stays as an empty pane; restart it.
  if [[ -z "$bar" ]]; then
    bar="$("$tmux_bin" split-window -vbd -l "$bar_h" -t "$side" -P -F '#{pane_id}' "exec $agent_bar")"
    "$tmux_bin" set -p -t "$bar" @pane-role agent-bar \; \
      set -p -t "$bar" @workbench-window "$w"
  else
    ((bar_ok)) || "$tmux_bin" respawn-pane -k -t "$bar" "exec $agent_bar"
    [[ "$bar_cur_h" == "$bar_h" ]] || "$tmux_bin" resize-pane -t "$bar" -y "$bar_h"
  fi

  # The dividers stay the plain border colour whichever pane is focused.
  if [[ "$border" != 1 ]]; then
    "$tmux_bin" set -w -t "$w" pane-active-border-style '#{pane-border-style}' \; \
      set -w -t "$w" @reading-border 1
  fi
}

mm_per_px="$(panel_mm_per_px || true)"

# The most recently active client of each session decides its layout.
declare -A client_w client_h client_cell client_pid
while read -r _ session width height cell pid; do
  [[ -n "${client_w[$session]:-}" ]] && continue
  client_w[$session]="$width"
  client_h[$session]="$height"
  client_cell[$session]="$cell"
  client_pid[$session]="$pid"
done < <("$tmux_bin" list-clients -F '#{client_activity} #{client_session} #{client_width} #{client_height} #{client_cell_width} #{client_pid}' | sort -rn)

for session in "${!client_w[@]}"; do
  [[ "$session" == *-terms ]] && continue
  width="${client_w[$session]}"
  status="$("$tmux_bin" show -t "$session" -qv status)"
  [[ -n "$status" ]] || status="$("$tmux_bin" show -gqv status)"
  case "$status" in on) lines=1 ;; off) lines=0 ;; *) lines="$status" ;; esac
  height=$((client_h[$session] - lines))

  cols="$max_cols"
  cell="${client_cell[$session]}"
  if [[ -n "$mm_per_px" && "$cell" =~ ^[1-9][0-9]*$ ]] && ! viewed_over_ssh "${client_pid[$session]}"; then
    fit="$(awk -v mm="$max_mm" -v cell="$cell" -v r="$mm_per_px" 'BEGIN { print int(mm / (cell * r)) }')"
    ((fit > 0 && fit < cols)) && cols="$fit"
  fi

  windows="$("$tmux_bin" list-windows -t "$session" -F '#{window_id}|#{@task-root}|#{@agent-pane}|#{window_zoomed_flag}|#{@reading-zoomed}|#{@reading-narrow}|#{@reading-layout}|#{@reading-border}')"
  # The agent list shows one row per agent window, up to half the height.
  agents="$(grep -c '^[^|]*|[^|]' <<<"$windows" || true)"
  bar_h=$((agents < height / 2 ? agents : height / 2))
  ((bar_h >= 1)) || bar_h=1

  while IFS='|' read -r w root agent zoomed ours narrow old border; do
    [[ -n "$old" ]] && drop_old_layout "$w"
    if [[ -z "$root" || ! -d "$root" || -z "$agent" ]]; then
      [[ -n "$narrow" ]] && unnarrow "$w"
      continue
    fi
    fit_window "$w" "$width" "$height" "$cols" "$agent" "$root" "$zoomed" "$ours" "$narrow" "$bar_h" "$border"
  done <<<"$windows"
done
