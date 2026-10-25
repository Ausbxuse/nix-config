#!/usr/bin/env bash
set -euo pipefail

tmux_bin="${TMUX_BIN:-tmux}"
resurrect_dir="${TMUX_RESURRECT_DIR:-$HOME/.local/share/tmux/resurrect}"
last_link="$resurrect_dir/last"
workbench_state="$resurrect_dir/workbench.tsv"

codex_home="${CODEX_HOME:-$HOME/.codex}"
claude_home="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
uuid_re='[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'

mkdir -p "$resurrect_dir"

# The conversation an agent process is in, from the process itself: Codex keeps
# its current rollout open (a resumed or forked thread writes a new id there,
# so its argv can be stale) and Claude records ~/.claude/sessions/<pid>.json.
# Cached window options are not used: they outlive the agent that set them.
codex_session_id() {
  local pid="$1" fd file header best_header="" best_rank=-1 rank

  for fd in /proc/"$pid"/fd/*; do
    file="$(readlink "$fd" 2>/dev/null || true)"
    [[ "$file" == "$codex_home"/sessions/*.jsonl && -f "$file" ]] || continue
    header="$(head -n 1 "$file" 2>/dev/null || true)"
    # Prefer the user's thread over sub-agent threads the same process writes.
    rank="$(stat -c %Y "$file" 2>/dev/null || echo 0)"
    [[ "$header" == *'"thread_source":"user"'* ]] && rank=$((rank + 1000000000))
    ((rank > best_rank)) && best_header="$header" best_rank="$rank"
  done
  if [[ "$best_header" =~ \"session_id\":\"($uuid_re)\" || "$best_header" =~ \"id\":\"($uuid_re)\" ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  argv_session_id "$pid" resume
}

claude_session_id() {
  local pid="$1" record id=""

  record="$(cat "$claude_home/sessions/$pid.json" 2>/dev/null || true)"
  [[ "$record" =~ \"sessionId\":\"($uuid_re)\" ]] && id="${BASH_REMATCH[1]}"
  [[ -n "$id" ]] || id="$(argv_session_id "$pid" --resume -r || true)"
  # A session with no transcript yet has nothing to resume.
  [[ -n "$id" ]] && compgen -G "$claude_home/projects/*/$id.jsonl" >/dev/null && printf '%s\n' "$id"
}

# The id following any of the given flags in the process's argv.
argv_session_id() {
  local pid="$1" flag arg take=0
  shift
  while IFS= read -r -d '' arg; do
    if ((take)) && [[ "$arg" =~ ^$uuid_re$ ]]; then
      printf '%s\n' "$arg"
      return 0
    fi
    take=0
    for flag in "$@"; do
      [[ "$arg" == "$flag" ]] && take=1
    done
  done < "/proc/$pid/cmdline" 2>/dev/null
  return 1
}

# session:window:pane, working directory and resume command for every pane whose
# process tree runs Codex or Claude.
agent_resume_commands() {
  local processes session window pane pane_pid pid comm agent kind id dir

  processes="$(ps -e -o pid=,ppid=,comm= 2>/dev/null || true)"
  "$tmux_bin" list-panes -a -F '#{session_name}	#{window_index}	#{pane_index}	#{pane_pid}' 2>/dev/null |
    while IFS=$'\t' read -r session window pane pane_pid; do
      [[ -n "$pane_pid" ]] || continue
      agent="" kind=""
      # Breadth-first from the pane's shell: the nearest agent owns the pane.
      local -a queue=("$pane_pid")
      while ((${#queue[@]})) && [[ -z "$agent" ]]; do
        pid="${queue[0]}"
        queue=("${queue[@]:1}")
        comm="$(awk -v p="$pid" '$1 == p { print $3; exit }' <<<"$processes")"
        case "$comm" in
          codex* | .codex*) agent="$pid" kind=codex ;;
          claude* | .claude*) agent="$pid" kind=claude ;;
          *) mapfile -t -O "${#queue[@]}" queue < <(awk -v p="$pid" '$2 == p { print $1 }' <<<"$processes") ;;
        esac
      done
      [[ -n "$agent" ]] || continue

      dir="$(readlink "/proc/$agent/cwd" 2>/dev/null || true)"
      case "$kind" in
        codex) id="$(codex_session_id "$agent" || true)" ;;
        claude) id="$(claude_session_id "$agent" || true)" ;;
      esac
      if [[ -z "$id" ]]; then
        printf '%s:%s:%s\t%s\t%s\n' "$session" "$window" "$pane" "$dir" "$kind"
      elif [[ "$kind" == codex ]]; then
        printf '%s:%s:%s\t%s\tcodex resume %s\n' "$session" "$window" "$pane" "$dir" "$id"
      else
        printf '%s:%s:%s\t%s\tclaude --resume %s\n' "$session" "$window" "$pane" "$dir" "$id"
      fi
    done
}

normalize_resurrect_file() {
  local target tmp agents

  target="$(readlink -f "$last_link" 2>/dev/null || true)"
  [[ -n "$target" && -f "$target" ]] || return 0

  agents="$(mktemp)"
  agent_resume_commands > "$agents"
  tmp="$(mktemp "$target.XXXXXX")"
  awk -F '\t' -v OFS='\t' -v agents="$agents" '
    BEGIN {
      while ((getline line < agents) > 0) {
        split(line, field, "\t")
        agent_dir[field[1]] = field[2]
        agent_command[field[1]] = field[3]
      }
    }

    function filtered_session(session) {
      return session == "__workbench-park-terms" || session ~ /-terms-terms$/
    }

    ($1 == "pane" || $1 == "window") && filtered_session($2) {
      next
    }

    $1 == "grouped_session" && (filtered_session($2) || filtered_session($3)) {
      next
    }

    $1 == "state" && (filtered_session($2) || filtered_session($3)) {
      next
    }

    $1 == "pane" {
      pane_command = $10
      full_command = $11
      key = $2 ":" $3 ":" $6

      if (key in agent_command) {
        $11 = ":" agent_command[key]
        if (agent_dir[key] != "") {
          $8 = ":" agent_dir[key]
        }
      } else if (pane_command == "nvim" || full_command ~ /^:.*\/bin\/nvim([[:space:]]|$)/) {
        $11 = ":nvim ."
      } else if (pane_command ~ /^\.?codex/ || full_command ~ /^:.*\/bin\/codex(-raw)?([[:space:]]|$)/) {
        $11 = ":codex"
      } else if (pane_command ~ /^\.?claude/ || full_command ~ /^:.*\/bin\/claude([[:space:]]|$)/) {
        $11 = ":claude"
      }
    }

    { print }
  ' "$target" > "$tmp"
  mv -f "$tmp" "$target"
  rm -f "$agents"
}

save_workbench_state() {
  local tmp

  tmp="$(mktemp "$workbench_state.XXXXXX")"
  {
    printf 'version\t1\n'

    "$tmux_bin" list-windows -a -F 'window	#{session_name}	#{window_index}	#{window_id}	#{@task-name}	#{@task-root}	#{@agent-pane}	#{@editor-pane}	#{@focus-pane}	#{@parked-primary-pane}	#{@primary}	#{@park-session}	#{@park-window}	#{@parked-for}	#{@term-session}	#{@term-window}	#{@paired-terminal}	#{@workbench-session}	#{@workbench-window}	#{@term-root}	#{@agent-state}	#{@agent-summary}	#{@agent-updated}	#{@agent-session-file}	#{@agent-session-scan}	#{@agent-session-sig}	#{@agent-preview-file}	#{@agent-home-session}	#{@agent-manual-idle}' 2>/dev/null |
      awk -F '\t' -v OFS='\t' '
        function filtered_session(session) {
          return session == "__workbench-park-terms" || session ~ /-terms-terms$/
        }

        filtered_session($2) {
          next
        }

        {
          has_option = 0
          for (i = 5; i <= NF; i++) {
            if ($i != "") {
              has_option = 1
              break
            }
          }
          if (has_option) {
            print
          }
        }
      '

    "$tmux_bin" list-panes -a -F 'pane	#{session_name}	#{window_index}	#{pane_index}	#{pane_id}	#{@pane-role}	#{@workbench-window}' 2>/dev/null |
      awk -F '\t' -v OFS='\t' '
        function filtered_session(session) {
          return session == "__workbench-park-terms" || session ~ /-terms-terms$/
        }

        !filtered_session($2) && ($6 != "" || $7 != "") {
          print
        }
      '
  } > "$tmp"

  mv -f "$tmp" "$workbench_state"
}

normalize_resurrect_file
save_workbench_state
