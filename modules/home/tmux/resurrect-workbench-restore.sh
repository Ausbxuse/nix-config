#!/usr/bin/env bash
set -euo pipefail

tmux_bin="${TMUX_BIN:-tmux}"
resurrect_dir="${TMUX_RESURRECT_DIR:-$HOME/.local/share/tmux/resurrect}"
workbench_state="$resurrect_dir/workbench.tsv"

[[ -f "$workbench_state" ]] || exit 0

declare -A window_by_key
declare -A pane_by_key
declare -A saved_window_key
declare -A saved_pane_key

split_tab_line() {
  local line="$1"
  local -n fields_ref="$2"

  fields_ref=()
  while [[ "$line" == *$'\t'* ]]; do
    fields_ref+=("${line%%$'\t'*}")
    line="${line#*$'\t'}"
  done
  fields_ref+=("$line")
}

filtered_session() {
  local session="${1:-}"

  [[ "$session" == "__workbench-park-terms" || "$session" == *-terms-terms ]]
}

while IFS=$'\t' read -r session index window_id; do
  [[ -n "$session" && -n "$index" && -n "$window_id" ]] || continue
  filtered_session "$session" && continue
  window_by_key["$session:$index"]="$window_id"
done < <("$tmux_bin" list-windows -a -F '#{session_name}	#{window_index}	#{window_id}' 2>/dev/null || true)

while IFS=$'\t' read -r session window_index pane_index pane_id; do
  [[ -n "$session" && -n "$window_index" && -n "$pane_index" && -n "$pane_id" ]] || continue
  filtered_session "$session" && continue
  pane_by_key["$session:$window_index:$pane_index"]="$pane_id"
done < <("$tmux_bin" list-panes -a -F '#{session_name}	#{window_index}	#{pane_index}	#{pane_id}' 2>/dev/null || true)

while IFS= read -r line; do
  split_tab_line "$line" fields
  type="${fields[0]:-}"
  session="${fields[1]:-}"
  window_index="${fields[2]:-}"
  filtered_session "$session" && continue

  case "$type" in
    window)
      saved_window_id="${fields[3]:-}"
      [[ -n "$saved_window_id" ]] || continue
      saved_window_key["$saved_window_id"]="$session:$window_index"
      ;;
    pane)
      pane_index="${fields[3]:-}"
      saved_pane_id="${fields[4]:-}"
      [[ -n "$pane_index" && -n "$saved_pane_id" ]] || continue
      saved_pane_key["$saved_pane_id"]="$session:$window_index:$pane_index"
      ;;
  esac
done < "$workbench_state"

map_saved_window() {
  local saved="$1" key

  [[ -n "$saved" ]] || return 1
  key="${saved_window_key[$saved]:-}"
  [[ -n "$key" ]] || return 1
  printf '%s\n' "${window_by_key[$key]:-}"
}

map_saved_pane() {
  local saved="$1" key

  [[ -n "$saved" ]] || return 1
  key="${saved_pane_key[$saved]:-}"
  [[ -n "$key" ]] || return 1
  printf '%s\n' "${pane_by_key[$key]:-}"
}

set_window_option_if_value() {
  local window="$1" option="$2" value="$3"

  [[ -n "$value" ]] || return 0
  "$tmux_bin" set-option -wq -t "$window" "$option" "$value" 2>/dev/null || true
}

set_window_option_mapped_window() {
  local window="$1" option="$2" saved="$3" mapped

  mapped="$(map_saved_window "$saved" 2>/dev/null || true)"
  [[ -n "$mapped" ]] || return 0
  "$tmux_bin" set-option -wq -t "$window" "$option" "$mapped" 2>/dev/null || true
}

set_window_option_mapped_pane() {
  local window="$1" option="$2" saved="$3" mapped

  mapped="$(map_saved_pane "$saved" 2>/dev/null || true)"
  [[ -n "$mapped" ]] || return 0
  "$tmux_bin" set-option -wq -t "$window" "$option" "$mapped" 2>/dev/null || true
}

set_pane_option_if_value() {
  local pane="$1" option="$2" value="$3"

  [[ -n "$value" ]] || return 0
  "$tmux_bin" set-option -pq -t "$pane" "$option" "$value" 2>/dev/null || true
}

set_pane_option_mapped_window() {
  local pane="$1" option="$2" saved="$3" mapped

  mapped="$(map_saved_window "$saved" 2>/dev/null || true)"
  [[ -n "$mapped" ]] || return 0
  "$tmux_bin" set-option -pq -t "$pane" "$option" "$mapped" 2>/dev/null || true
}

valid_task_root_value() {
  local root="$1"

  [[ -n "$root" && -d "$root" ]]
}

valid_terminal_session_value() {
  local session="$1" term_session="$2"

  [[ -n "$term_session" && "$term_session" != "$session" && "$term_session" != @* ]]
}

window_has_valid_task_root() {
  local window="$1" root

  root="$("$tmux_bin" show-options -wqv -t "$window" @task-root 2>/dev/null || true)"
  valid_task_root_value "$root"
}

while IFS= read -r line; do
  split_tab_line "$line" fields
  type="${fields[0]:-}"
  [[ "$type" == window ]] || continue

  session="${fields[1]:-}"
  window_index="${fields[2]:-}"
  filtered_session "$session" && continue
  task_name="${fields[4]:-}"
  task_root="${fields[5]:-}"
  agent_pane="${fields[6]:-}"
  editor_pane="${fields[7]:-}"
  focus_pane="${fields[8]:-}"
  parked_primary_pane="${fields[9]:-}"
  primary="${fields[10]:-}"
  park_session="${fields[11]:-}"
  park_window="${fields[12]:-}"
  parked_for="${fields[13]:-}"
  term_session="${fields[14]:-}"
  term_window="${fields[15]:-}"
  paired_terminal="${fields[16]:-}"
  workbench_session="${fields[17]:-}"
  workbench_window="${fields[18]:-}"
  term_root="${fields[19]:-}"
  agent_state="${fields[20]:-}"
  agent_summary="${fields[21]:-}"
  agent_updated="${fields[22]:-}"
  agent_session_file="${fields[23]:-}"
  agent_session_scan="${fields[24]:-}"
  agent_session_sig="${fields[25]:-}"
  agent_preview_file="${fields[26]:-}"
  agent_home_session="${fields[27]:-}"
  agent_manual_idle="${fields[28]:-}"

  window="${window_by_key["$session:$window_index"]:-}"
  [[ -n "$window" ]] || continue

  set_window_option_if_value "$window" @agent-manual-idle "$agent_manual_idle"

  if valid_task_root_value "$task_root"; then
    mapped_term_window="$(map_saved_window "$term_window" 2>/dev/null || true)"

    set_window_option_if_value "$window" @task-name "$task_name"
    set_window_option_if_value "$window" @task-root "$task_root"
    set_window_option_mapped_pane "$window" @agent-pane "$agent_pane"
    set_window_option_mapped_pane "$window" @editor-pane "$editor_pane"
    set_window_option_mapped_pane "$window" @focus-pane "$focus_pane"
    set_window_option_mapped_pane "$window" @parked-primary-pane "$parked_primary_pane"
    set_window_option_if_value "$window" @primary "$primary"
    set_window_option_if_value "$window" @park-session "$park_session"
    set_window_option_mapped_window "$window" @park-window "$park_window"
    if valid_terminal_session_value "$session" "$term_session"; then
      set_window_option_if_value "$window" @term-session "$term_session"
    fi
    if [[ -n "$mapped_term_window" && "$mapped_term_window" != "$window" ]]; then
      "$tmux_bin" set-option -wq -t "$window" @term-window "$mapped_term_window" 2>/dev/null || true
    fi
    set_window_option_if_value "$window" @agent-state "$agent_state"
    set_window_option_if_value "$window" @agent-summary "$agent_summary"
    set_window_option_if_value "$window" @agent-updated "$agent_updated"
    set_window_option_if_value "$window" @agent-session-file "$agent_session_file"
    set_window_option_if_value "$window" @agent-session-scan "$agent_session_scan"
    set_window_option_if_value "$window" @agent-session-sig "$agent_session_sig"
    set_window_option_if_value "$window" @agent-preview-file "$agent_preview_file"
    set_window_option_if_value "$window" @agent-home-session "$agent_home_session"
  else
    set_window_option_mapped_window "$window" @parked-for "$parked_for"
    if [[ "$paired_terminal" == 1 ]]; then
      mapped_workbench_window="$(map_saved_window "$workbench_window" 2>/dev/null || true)"
      if [[ -n "$mapped_workbench_window" && "$mapped_workbench_window" != "$window" ]]; then
        set_window_option_if_value "$window" @paired-terminal "$paired_terminal"
        set_window_option_if_value "$window" @workbench-session "$workbench_session"
        "$tmux_bin" set-option -wq -t "$window" @workbench-window "$mapped_workbench_window" 2>/dev/null || true
        set_window_option_if_value "$window" @term-root "$term_root"
      fi
    fi
  fi
done < "$workbench_state"

while IFS= read -r line; do
  split_tab_line "$line" fields
  type="${fields[0]:-}"
  [[ "$type" == pane ]] || continue

  session="${fields[1]:-}"
  window_index="${fields[2]:-}"
  filtered_session "$session" && continue
  pane_index="${fields[3]:-}"
  role="${fields[5]:-}"
  workbench_window="${fields[6]:-}"
  mapped_workbench_window=""
  pane_window=""
  agent_pane=""
  editor_pane=""

  pane="${pane_by_key["$session:$window_index:$pane_index"]:-}"
  [[ -n "$pane" ]] || continue

  mapped_workbench_window="$(map_saved_window "$workbench_window" 2>/dev/null || true)"
  if [[ -n "$mapped_workbench_window" ]]; then
    pane_window="$("$tmux_bin" display -p -t "$pane" '#{window_id}' 2>/dev/null || true)"
    agent_pane="$("$tmux_bin" show-options -wqv -t "$mapped_workbench_window" @agent-pane 2>/dev/null || true)"
    editor_pane="$("$tmux_bin" show-options -wqv -t "$mapped_workbench_window" @editor-pane 2>/dev/null || true)"
    if [[ "$pane" == "$agent_pane" ]]; then
      role=agent
    elif [[ "$pane" == "$editor_pane" ]]; then
      role=editor
    elif [[ "$role" == terminal && "$pane_window" == "$mapped_workbench_window" ]] && window_has_valid_task_root "$mapped_workbench_window"; then
      role=""
    fi
  fi

  set_pane_option_if_value "$pane" @pane-role "$role"
  if [[ -n "$mapped_workbench_window" ]]; then
    "$tmux_bin" set-option -pq -t "$pane" @workbench-window "$mapped_workbench_window" 2>/dev/null || true
  fi
done < "$workbench_state"
