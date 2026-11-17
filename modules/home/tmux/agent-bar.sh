#!/usr/bin/env bash
# Herdr-style agent list for the top of an agent window's left column
# (placed by reading-width.sh): one row per workbench window in this session
# with its number, name, state and the workbench's one-line summary. Colours
# match the status bar's window numbers: green ready, blue working, yellow
# needs input, red failed, grey idle by hand. ▸ marks this window. Read-only;
# it redraws only when something changed and idles while its window is hidden.
set -uo pipefail

tmux_bin="${TMUX_BIN:-tmux}"
pane="${TMUX_PANE:?agent-bar must run inside tmux}"

printf '\e[?25l'
trap 'printf "\e[?25h"' EXIT
redraw=1
trap 'redraw=1' WINCH

row() {
  local mark="$1" index="$2" name="$3" state="$4" manual="$5" bell="$6" summary="$7" width="$8"
  local colour word head rest

  if [[ -n "$manual" ]]; then
    colour=8 word=idle
  else
    case "$state" in
      waiting | needs-input) colour=3 word=input ;;
      blocked | error) colour=1 word=failed ;;
      running | busy) colour=4 word=working ;;
      done | idle | ready) colour=2 word="$state" ;;
      *) if [[ -n "$bell" ]]; then colour=3; else colour=2; fi; word="${state:-ready}" ;;
    esac
  fi
  # Generic summaries only repeat the state.
  case "$summary" in "$word" | "$state" | ready | working | done | idle) summary="" ;; esac

  # Plain text decides the cut, so colour codes never count against the width.
  head="$(printf '%s%2s %-12.12s ● %-7s' "$mark" "$index" "$name" "$word")"
  rest=$((width - ${#head} - 1))
  ((rest > 0)) || summary=""
  ((rest > 0)) && summary="${summary:0:rest}"
  head="${head:0:width}"

  if [[ "$mark" == "▸" ]]; then
    printf '\e[1m%s\e[22m' "${head:0:16}"
  else
    printf '%s' "${head:0:16}"
  fi
  printf '\e[3%sm%s\e[39m' "$colour" "${head:16}"
  [[ -n "$summary" ]] && printf ' \e[90m%s\e[39m' "$summary"
}

last=""
while :; do
  info="$("$tmux_bin" display -p -t "$pane" '#{window_id}|#{session_name}|#{window_active}|#{session_attached}|#{pane_width}' 2>/dev/null)" || exit 0
  IFS='|' read -r self session active attached width <<<"$info"

  if [[ "$active" == 1 && "$attached" != 0 ]]; then
    list="$("$tmux_bin" list-windows -t "$session" -F '#{window_id}|#{window_index}|#{window_name}|#{@agent-state}|#{@agent-manual-idle}|#{window_bell_flag}|#{@agent-summary}|#{@task-root}' 2>/dev/null)"
    if ((redraw)) || [[ "$list|$width" != "$last" ]]; then
      last="$list|$width"
      redraw=0
      out=$'\e[H'
      while IFS='|' read -r id index name state manual bell summary root; do
        [[ -n "$root" ]] || continue
        mark=" "
        [[ "$id" == "$self" ]] && mark="▸"
        [[ "$bell" == 1 ]] || bell=""
        out+="$(row "$mark" "$index" "$name" "$state" "$manual" "$bell" "$summary" "$width")"$'\e[K\n'
      done <<<"$list"
      printf '%s\e[J' "${out%$'\n'}"
    fi
  fi
  sleep 1
done
