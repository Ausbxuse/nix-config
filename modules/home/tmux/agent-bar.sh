#!/usr/bin/env bash
# Herdr-style agent list, one per session, kept above the current agent
# window's side shell by reading-width.sh: one row per workbench window with
# its number, name, state and the workbench's one-line summary. Colours match
# the status bar's window numbers: green ready, blue working, yellow needs
# input, red failed, grey idle by hand.
#
# The highlighted row is the current window until the list is focused (Alt+K
# from the shell below). Then j/k or the arrows move it, g/G jump to the ends,
# Enter or l opens that agent, r renames it (the usual rename prompt,
# prefilled), and Esc, q, h or Ctrl+C return to the current agent.
# Redraws only when something changed.
set -uo pipefail

tmux_bin="${TMUX_BIN:-tmux}"
pane="${TMUX_PANE:?agent-bar must run inside tmux}"

printf '\e[?25l'
trap 'printf "\e[?25h"' EXIT
redraw=1
trap 'redraw=1' WINCH

sel="" sel_window=""
ids=() agents=()

# One row; plain text decides the cut, so colour codes never count.
row() {
  local selected="$1" mark="$2" index="$3" name="$4" state="$5" manual="$6" bell="$7" summary="$8" width="$9"
  local colour word head plain rest

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

  head="$(printf '%s%2s %-12.12s ● %-7s' "$mark" "$index" "$name" "$word")"
  head="${head:0:width}"
  rest=$((width - ${#head} - 1))
  if ((rest > 0)) && [[ -n "$summary" ]]; then summary="${summary:0:rest}"; else summary=""; fi
  plain="$head${summary:+ $summary}"

  # The highlighted row is plain reverse video, like any list selection.
  if [[ "$selected" == 1 ]]; then
    printf '\e[7m%s%*s\e[27m' "$plain" $((width - ${#plain})) ''
    return
  fi
  printf '%s\e[3%sm%s\e[39m' "${head:0:16}" "$colour" "${head:16}"
  [[ -n "$summary" ]] && printf ' \e[90m%s\e[39m' "$summary"
  printf '%*s' $((width - ${#plain})) ''
}

# tmux's own rename prompt, so the workbench rename hooks run as usual. The
# prefill is format-expanded, hence the doubled #.
rename() {
  local id name=""
  [[ -n "$sel" ]] || return 0
  while IFS="|" read -r id _ name _; do [[ "$id" == "$sel" ]] && break; name=""; done <<<"$list"
  "$tmux_bin" command-prompt -I "${name//#/##}" -p "rename:" "rename-window -t '$sel' -- \"%%%\"" 2>/dev/null
}

focus_agent() {
  local agent
  agent="$("$tmux_bin" display -p -t "$1" '#{@agent-pane}' 2>/dev/null)"
  [[ -n "$agent" ]] && "$tmux_bin" select-pane -t "$agent" 2>/dev/null
}

move() {
  local i n="${#ids[@]}"
  ((n)) || return 0
  for i in "${!ids[@]}"; do [[ "${ids[$i]}" == "$sel" ]] && break; done
  case "$1" in
    down) ((i < n - 1)) && ((i++)) ;;
    up) ((i > 0)) && ((i--)) ;;
    top) i=0 ;;
    bottom) i=$((n - 1)) ;;
  esac
  sel="${ids[$i]}"
  redraw=1
}

# Ctrl+C would end the list and leave a dead pane; treat it like Esc.
trap 'sel="$self" redraw=1; focus_agent "$self"' INT

self=""
last=""
while :; do
  info="$("$tmux_bin" display -p -t "$pane" '#{window_id}|#{session_name}|#{window_active}|#{session_attached}|#{pane_width}' 2>/dev/null)" || exit 0
  IFS='|' read -r self session active attached width <<<"$info"

  # Hidden lists keep drawing so a window switch shows them already current;
  # the highlight returns to this window whenever it is not on screen.
  if [[ "$active" != 1 || "$attached" == 0 ]]; then
    [[ "$sel" == "$self" ]] || redraw=1
    sel="$self"
  fi
  if :; then
    list="$("$tmux_bin" list-windows -t "$session" -F '#{window_id}|#{window_index}|#{window_name}|#{@agent-state}|#{@agent-manual-idle}|#{window_bell_flag}|#{@agent-summary}|#{@task-root}' 2>/dev/null)"
    if ((redraw)) || [[ "$list|$width|$sel" != "$last" ]]; then
      redraw=0
      ids=()
      while IFS='|' read -r id _ _ _ _ _ _ root; do
        [[ -n "$root" ]] && ids+=("$id")
      done <<<"$list"
      [[ " ${ids[*]} " == *" $sel "* ]] || sel="$self"
      last="$list|$width|$sel"
      out=$'\e[H'
      while IFS='|' read -r id index name state manual bell summary root; do
        [[ -n "$root" ]] || continue
        mark=" " selected=0
        [[ "$id" == "$self" ]] && mark="▸"
        [[ "$id" == "$sel" ]] && selected=1
        [[ "$bell" == 1 ]] || bell=""
        out+="$(row "$selected" "$mark" "$index" "$name" "$state" "$manual" "$bell" "$summary" "$width")"$'\e[K\n'
      done <<<"$list"
      printf '%s\e[J' "${out%$'\n'}"
    fi
  fi

  key=""
  if IFS= read -rsn1 -t 1 key; then
    if [[ "$key" == $'\e' ]]; then
      rest=""
      IFS= read -rsn2 -t 0.02 rest || true
      key+="$rest"
    fi
    case "$key" in
      j | $'\e[B') move down ;;
      k | $'\e[A') move up ;;
      g) move top ;;
      G) move bottom ;;
      r) rename ;;
      "" | l | $'\e[C')
        if [[ -n "$sel" && "$sel" != "$self" ]]; then
          "$tmux_bin" select-window -t "$sel" 2>/dev/null && focus_agent "$sel"
        else
          focus_agent "$self"
        fi
        ;;
      $'\e' | q | h | $'\e[D') sel="$self" redraw=1; focus_agent "$self" ;;
    esac
  fi
done
