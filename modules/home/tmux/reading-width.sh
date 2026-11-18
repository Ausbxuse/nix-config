#!/usr/bin/env bash
# Keep agent text readable on wide terminals, like a blog post: the agent pane
# of each workbench window is held at most @reading-width cells wide (default
# 80) and @reading-width-mm on screen (default 200 mm, a blog column's physical
# width; measured from the local panel's EDID, skipped for SSH clients).
#
# The agent column sits toward your line of sight (the primary monitor's
# centre), as told per terminal window by the terminal-side GNOME extension:
#   window spanning it: centred where there is room; a left column holds a
#     shell opened in the task root (@pane-role side-terminal), and the window
#     is narrowed so the same width stays blank on the right
#   window left of it: the shell column takes all the spare width on the left
#   window right of it: the agent column hugs the left edge, shell on its right
# SSH clients and unknown windows count as spanning it. Below $min_side spare
# cells the agent pane is zoomed with the shell waiting behind it.
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

side_file="${XDG_RUNTIME_DIR:-/tmp}/terminal-side"

# Which side of your line of sight a client's terminal window is on: the line
# for its pid or its nearest ancestor's in $side_file, else centre.
client_side() {
  local pid="$1" stat side
  while [[ -r "$side_file" && "$pid" =~ ^[0-9]+$ ]] && ((pid > 1)); do
    side="$(awk -v p="$pid" '$1 == p { print $2; exit }' "$side_file")"
    [[ -n "$side" ]] && { echo "$side"; return 0; }
    stat="$(cat "/proc/$pid/stat" 2>/dev/null)" || break
    read -r _ pid _ <<<"${stat##*) }" # the name before it may hold spaces
  done
  echo centre
}

# A tmux layout string with its checksum, for select-layout.
with_csum() {
  local s="$1" c=0 i ch
  for ((i = 0; i < ${#s}; i++)); do
    printf -v ch '%d' "'${s:i:1}"
    c=$((((c >> 1) + ((c & 1) << 15) + ch) & 0xffff))
  done
  printf '%04x,%s' "$c" "$s"
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
  local bar_h="${10}" border="${11}" sight="${12}"
  local pane role pane_w pane_h pane_x start dead side="" bar="" bar_cur_h="" bar_ok=0 agent_w="" others=0
  local spare left target side_x="" agent_x="" col_w shell_h layout

  while IFS='|' read -r pane role pane_w pane_h pane_x start dead; do
    if [[ "$pane" == "$agent" ]]; then
      agent_w="$pane_w" agent_x="$pane_x"
    elif [[ "$role" == side-terminal && -z "$side" ]]; then
      side="$pane" side_x="$pane_x"
    elif [[ "$role" == agent-bar && -z "$bar" ]]; then
      bar="$pane" bar_cur_h="$pane_h"
      [[ "$start" == *agent-bar.sh* && "$dead" != 1 ]] && bar_ok=1
    else
      others=1
    fi
  done < <("$tmux_bin" list-panes -t "$w" -F '#{pane_id}|#{@pane-role}|#{pane_width}|#{pane_height}|#{pane_left}|#{pane_start_command}|#{pane_dead}')

  if [[ -z "$agent_w" || "$others" == 1 || ("$zoomed" == 1 && -z "$ours") ]]; then
    [[ -n "$narrow" ]] && unnarrow "$w"
    return 0
  fi

  # Spanning your line of sight, centre the agent column when the left half
  # has room for a usable shell; otherwise, and off to either side, the shell
  # column takes all the spare width. Below $min_side the agent pane is
  # zoomed over it.
  spare=$((width - cols - 1))
  left="$spare"
  target="$width"
  if [[ "$sight" == centre ]]; then
    left=$(((width - cols) / 2))
    ((left >= min_side)) || left="$spare"
    ((spare >= min_side)) && target=$((left + 1 + cols))
  fi

  if [[ "$narrow" != "$target:$height" ]]; then
    "$tmux_bin" set -w -t "$w" fill-character ' ' \; \
      set -w -t "$w" @reading-narrow "$target:$height" \; \
      resize-window -t "$w" -x "$target" -y "$height"
    # Narrowing takes the width from whichever panes it reaches first.
    agent_w="$("$tmux_bin" display -p -t "$agent" '#{pane_width}')"
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
    side="$("$tmux_bin" split-window "$([[ "$sight" == right ]] && echo -hd || echo -hbd)" -l "$left" -t "$agent" -c "$root" -P -F '#{pane_id}')"
    side_x="$("$tmux_bin" display -p -t "$side" '#{pane_left}')"
    agent_x="$("$tmux_bin" display -p -t "$agent" '#{pane_left}')"
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

  # A window that moved across your line of sight has its shell column on the
  # wrong side: lay it out again in one step, shells and all still running.
  local wrong=0
  if [[ -n "$side_x" && -n "$agent_x" ]]; then
    if [[ "$sight" == right ]]; then
      if ((side_x < agent_x)); then wrong=1; fi
    elif ((side_x > agent_x)); then
      wrong=1
    fi
  fi
  if ((wrong)); then
    col_w=$((target - cols - 1)) shell_h=$((height - bar_h - 1))
    if [[ "$sight" == right ]]; then
      layout="${target}x${height},0,0{${cols}x${height},0,0,${agent#%},${col_w}x${height},$((cols + 1)),0[${col_w}x${bar_h},$((cols + 1)),0,${bar#%},${col_w}x${shell_h},$((cols + 1)),$((bar_h + 1)),${side#%}]}"
    else
      layout="${target}x${height},0,0{${col_w}x${height},0,0[${col_w}x${bar_h},0,0,${bar#%},${col_w}x${shell_h},0,$((bar_h + 1)),${side#%}],${cols}x${height},$((col_w + 1)),0,${agent#%}}"
    fi
    "$tmux_bin" select-layout -t "$w" "$(with_csum "$layout")"
    # select-layout fills its cells in pane order, whatever ids it names;
    # swap each pane into its cell (cells left to right, top to bottom).
    local want=("$bar" "$side" "$agent") i at
    [[ "$sight" == right ]] && want=("$agent" "$bar" "$side")
    for i in 0 1 2; do
      at="$("$tmux_bin" list-panes -t "$w" -F '#{pane_id}' | sed -n "$((i + 1))p")"
      [[ "$at" == "${want[$i]}" ]] || "$tmux_bin" swap-pane -d -s "${want[$i]}" -t "$at"
    done
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
  sight=centre
  if ! viewed_over_ssh "${client_pid[$session]}"; then
    sight="$(client_side "${client_pid[$session]}")"
    if [[ -n "$mm_per_px" && "$cell" =~ ^[1-9][0-9]*$ ]]; then
      fit="$(awk -v mm="$max_mm" -v cell="$cell" -v r="$mm_per_px" 'BEGIN { print int(mm / (cell * r)) }')"
      ((fit > 0 && fit < cols)) && cols="$fit"
    fi
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
    fit_window "$w" "$width" "$height" "$cols" "$agent" "$root" "$zoomed" "$ours" "$narrow" "$bar_h" "$border" "$sight"
  done <<<"$windows"
done
