{
  config,
  lib,
  pkgs,
  ...
}: let
  # Home Manager's plugin loader uses blocking run-shell commands. Continuum's
  # background restore also inherits their output pipe, delaying the first
  # client until all saved panes have been restored. Keep plugin order, but
  # start the whole chain as one tmux background job after loading the config.
  initializeTmuxPlugins = pkgs.writeShellScript "initialize-tmux-plugins" ''
    set -eu

    theme_cache="''${XDG_CACHE_HOME:-$HOME/.cache}/tmux/theme"
    theme=dark
    if [[ -r "$theme_cache" ]]; then
      IFS= read -r theme < "$theme_cache" || true
    fi
    case "$theme" in
      light|dark) ;;
      *) theme=dark ;;
    esac
    tmux set-environment -g TMUX_COLOR_SCHEME "$theme"
    tmux source-file "$HOME/.config/tmux/theme-$theme.conf"

    ${pkgs.tmuxPlugins.sensible.rtp}
    ${pkgs.tmuxPlugins.resurrect.rtp}
    ${pkgs.tmuxPlugins.continuum.rtp}
    # Custom bindings take precedence over plugin defaults (notably C-r).
    tmux source-file ${./tmux.conf}
    tmux set-option -g @tmux-plugins-ready 1
  '';
  continuumSaveScript = "${builtins.dirOf pkgs.tmuxPlugins.continuum.rtp}/scripts/continuum_save.sh";

  # Registers claude-notify.sh for the lifecycle hooks it handles, merging
  # into ~/.claude/settings.json without disturbing anything else there (the
  # file stays writable: Claude Code edits it itself, so no home.file link —
  # same reasoning as codex.nix's configureCodexNotify).
  configureClaudeHooks =
    pkgs.writers.writePython3 "configure-claude-hooks" {} ''
      import json
      import os
      import sys

      path = sys.argv[1]
      cmd = sys.argv[2]

      # A fresh Home Manager account has no ~/.claude directory yet.  This
      # activation runs after writeBoundary, but the managed Claude files are
      # linked later, so make the writable settings parent explicitly.
      parent = os.path.dirname(path)
      if parent:
          os.makedirs(parent, exist_ok=True)

      # async on PreToolUse so state pushes never add latency to tool calls;
      # the rest fire at most once per turn and stay synchronous so their
      # ordering is guaranteed.
      events = {
          "SessionStart": {},
          "UserPromptSubmit": {},
          "PreToolUse": {"async": True},
          "Notification": {},
          "Stop": {},
          "StopFailure": {},
          "SessionEnd": {},
      }

      try:
          with open(path) as fh:
              settings = json.load(fh)
      except FileNotFoundError:
          settings = {}
      if not isinstance(settings, dict):
          raise SystemExit(path + " is not a JSON object; refusing")

      hooks = settings.setdefault("hooks", {})
      for event, extra in events.items():
          groups = hooks.get(event)
          if not isinstance(groups, list):
              groups = []
          # Strip any previous registration of this script, then re-add
          # canonically, so reruns and option changes stay idempotent.
          for group in groups:
              if isinstance(group, dict) \
                      and isinstance(group.get("hooks"), list):
                  group["hooks"] = [
                      h for h in group["hooks"]
                      if not (isinstance(h, dict)
                              and cmd in str(h.get("command", "")))
                  ]
          groups = [
              g for g in groups
              if not (isinstance(g, dict) and g.get("hooks") == [])
          ]
          entry = {"type": "command", "command": cmd}
          entry.update(extra)
          groups.append({"hooks": [entry]})
          hooks[event] = groups

      tmp = path + ".tmp"
      with open(tmp, "w") as fh:
          json.dump(settings, fh, indent=2)
          fh.write("\n")
      os.replace(tmp, path)
    '';
in {
  programs.tmux = {
    enable = true;
  };

  home.activation.configureClaudeHooks = lib.hm.dag.entryAfter ["writeBoundary"] ''
    ${configureClaudeHooks} \
      "${config.home.homeDirectory}/.claude/settings.json" \
      "${config.home.homeDirectory}/.local/bin/tmux/claude-notify.sh"
  '';

  home.packages = with pkgs; [
    libnotify
    lm_sensors
    wl-clipboard
    xsel
  ];

  # Start the same server that interactive terminals use. Continuum's generated
  # unit used /tmp at login, before GNOME imported TMUX_TMPDIR into systemd, so
  # the first terminal started a second server and repeated the entire restore.
  # Home Manager's secureSocket setting supplies the terminal environment.
  systemd.user.services.tmux = lib.mkIf pkgs.stdenv.hostPlatform.isLinux {
    Unit = {
      Description = "tmux default session (detached)";
      Documentation = "man:tmux(1)";
    };
    Service = {
      Type = "forking";
      Environment = "TMUX_TMPDIR=%t";
      ExecStart = "${config.programs.tmux.package}/bin/tmux new-session -d";
      ExecStop = [
        "${builtins.dirOf pkgs.tmuxPlugins.resurrect.rtp}/scripts/save.sh"
        "${config.programs.tmux.package}/bin/tmux kill-server"
      ];
      KillMode = "process";
    };
    Install.WantedBy = ["default.target"];
  };

  systemd.user.services.tmux-battery-time = {
    Unit = {
      Description = "Update cached tmux battery time estimate";
    };

    Service = {
      ExecStart = "%h/.local/bin/tmux/battery-time-daemon.sh";
      Environment = "TMUX_BATTERY_TIME_INTERVAL=300";
      Restart = "always";
      RestartSec = 10;
    };

    Install = {
      WantedBy = ["default.target"];
    };
  };

  xdg.configFile."tmux/theme-dark.conf".source = ./theme-dark.conf;
  xdg.configFile."tmux/theme-light.conf".source = ./theme-light.conf;
  home.file.".local/bin/tmux/workbench.sh" = {
    source = ./workbench.sh;
    executable = true;
  };
  home.file.".local/bin/tmux/claude-notify.sh" = {
    source = ./claude-notify.sh;
    executable = true;
  };
  home.file.".local/bin/tmux/windowizer.sh" = {
    source = ./windowizer.sh;
    executable = true;
  };
  home.file.".local/bin/tmux/dwm.sh" = {
    source = ./dwm.sh;
    executable = true;
  };
  home.file.".local/bin/tmux/pane-manager.sh" = {
    source = ./pane-manager.sh;
    executable = true;
  };
  home.file.".local/bin/tmux/resurrect-workbench-save.sh" = {
    source = ./resurrect-workbench-save.sh;
    executable = true;
  };
  home.file.".local/bin/tmux/resurrect-workbench-restore.sh" = {
    source = ./resurrect-workbench-restore.sh;
    executable = true;
  };
  home.file.".local/bin/tmux/client-origin.sh" = {
    source = ./client-origin.sh;
    executable = true;
  };
  home.file.".local/bin/agent-resurrect" = {
    source = ./agent-resurrect.sh;
    executable = true;
  };
  home.file.".local/bin/tmux/sessionizer.sh" = {
    executable = true;
    text = ''
      #!/usr/bin/env bash
      set -euo pipefail

      exec "$HOME/.local/bin/tmux/windowizer.sh" "$@"
    '';
  };
  home.file.".local/bin/tmux/copy-to-clipboard.sh" = {
    executable = true;
    text = ''
      #!/usr/bin/env bash
      set -euo pipefail

      tmp="$(mktemp)"
      trap 'rm -f "$tmp"' EXIT
      cat > "$tmp"

      if tmux load-buffer -w "$tmp" 2>/dev/null; then
        exit 0
      fi

      if [[ -s "$tmp" ]]; then
        tmux load-buffer "$tmp" 2>/dev/null || true
      fi

      if [[ -n "''${DISPLAY:-}" ]] && command -v xsel >/dev/null 2>&1; then
        xsel --clipboard --input < "$tmp" && exit 0
      fi

      if [[ -n "''${DISPLAY:-}" ]] && command -v xclip >/dev/null 2>&1; then
        xclip -selection clipboard -in < "$tmp" && exit 0
      fi

      if [[ -t 1 ]]; then
        ${pkgs.perl}/bin/perl -MMIME::Base64=encode_base64 -0777 -ne '
          print "\e]52;c;", encode_base64($_, ""), "\a"
        ' < "$tmp"
      fi
    '';
  };
  home.file.".local/bin/tmux/history-edit.sh" = {
    executable = true;
    text = ''
      #!/usr/bin/env bash
      set -euo pipefail

      target="''${1:-}"
      if [[ -z "$target" ]]; then
        target="$(tmux display -p '#{pane_id}')"
      fi

      tmp="$(mktemp --suffix=.tmux-history)"
      trap 'rm -f "$tmp"' EXIT

      tmux capture-pane -pJ -S - -E - -t "$target" > "$tmp"
      if ! grep -q '[^[:space:]]' "$tmp"; then
        exit 0
      fi

      nvim \
        -R \
        -c 'setlocal buftype=nowrite bufhidden=wipe noswapfile nomodifiable nowrap filetype=tmux-history' \
        -c 'nnoremap <buffer> q <cmd>quit<cr>' \
        -c 'xnoremap <buffer> y "+y<cmd>quit<cr>' \
        "$tmp"
    '';
  };
  home.file.".local/bin/tmux/history-copy.sh" = {
    executable = true;
    text = ''
      #!/usr/bin/env bash
      set -euo pipefail
      exec ~/.local/bin/tmux/history-edit.sh "$@"
    '';
  };
  home.file.".local/bin/tmux/codex-notify.sh" = {
    executable = true;
    text = ''
      #!/usr/bin/env bash
      set -euo pipefail

      payload="''${1:-}"
      if [[ -z "$payload" && ! -t 0 ]]; then
        payload="$(cat || true)"
      fi

      # Classify the event, not words in the assistant's message or tool output.
      event="$(printf '%s' "$payload" | ${pkgs.jq}/bin/jq -r '.type // empty' 2>/dev/null || true)"
      [[ -n "$event" ]] || event="$payload"
      summary="needs attention"
      state="waiting"
      case "''${event,,}" in
        task_started | agent-turn-start | turn-start | turn_start)
          state="running"
          summary="working"
          ;;
        task_complete | agent-turn-complete | turn-complete | turn_complete)
          state="done"
          summary="finished"
          ;;
        turn_aborted | task_aborted | canceled | cancelled | interrupted)
          state="blocked"
          summary="canceled"
          ;;
        error | agent-turn-error | turn-error | failed | failure)
          state="blocked"
          summary="failed"
          ;;
      esac

      if [[ -n "''${TMUX_PANE:-}" ]] && command -v tmux >/dev/null 2>&1; then
        TMUX_WORKBENCH_TARGET_PANE="$TMUX_PANE" \
          "$HOME/.local/bin/tmux/workbench.sh" state "$state" "$summary" 2>/dev/null || true

        [[ "$state" == waiting || "$state" == blocked ]] || exit 0
        pane_tty="$(tmux display -p -t "$TMUX_PANE" '#{pane_tty}' 2>/dev/null || true)"
        if [[ -n "$pane_tty" && -w "$pane_tty" ]]; then
          printf '\a' > "$pane_tty"
          exit 0
        fi
      fi

      [[ "$state" == waiting || "$state" == blocked ]] && printf '\a'
      exit 0
    '';
  };
  home.file.".local/bin/tmux/agent-workflow.sh" = {
    executable = true;
    text = ''
      #!/usr/bin/env bash
      set -euo pipefail

      main_session="''${TMUX_AGENT_MAIN_SESSION:-main}"
      cmd="''${1:-}"

      current_session="$(tmux display -p '#{session_name}')"
      window_id="$(tmux display -p '#{window_id}')"

      project_name_for_path() {
        local path repo name

        path="$(tmux display -p '#{pane_current_path}')"
        repo=""
        if command -v git >/dev/null 2>&1; then
          repo="$(git -C "$path" rev-parse --show-toplevel 2>/dev/null || true)"
        fi

        name="$(basename "''${repo:-$path}")"
        printf '%s\n' "''${name//[.:]/_}"
      }

      unique_window_name() {
        local base candidate existing id n

        base="$1"
        candidate="$base"
        n=2

        while true; do
          existing=false
          while IFS='|' read -r id name; do
            if [[ "$id" != "$window_id" && "$name" == "$candidate" ]]; then
              existing=true
              break
            fi
          done < <(tmux list-windows -t "$main_session" -F '#{window_id}|#{window_name}' 2>/dev/null || true)

          if [[ "$existing" == false ]]; then
            printf '%s\n' "$candidate"
            return 0
          fi

          candidate="$base-$n"
          n=$((n + 1))
        done
      }

      ensure_main_session() {
        if ! tmux has-session -t "$main_session" 2>/dev/null; then
          tmux new-session -d -s "$main_session"
        fi
      }

      session_has_window() {
        tmux list-windows -t "$1" -F '#{window_id}' 2>/dev/null | grep -Fxq "$window_id"
      }

      find_linked_session() {
        local home_session
        home_session="$(tmux show-window-option -v @agent-home-session 2>/dev/null || true)"
        if [[ -n "$home_session" && "$home_session" != "$main_session" ]] && session_has_window "$home_session"; then
          printf '%s\n' "$home_session"
          return 0
        fi

        tmux list-sessions -F '#{session_name}' |
          while IFS= read -r session; do
            [[ "$session" == "$main_session" ]] && continue
            if session_has_window "$session"; then
              printf '%s\n' "$session"
              return 0
            fi
          done
      }

      case "$cmd" in
        link)
          if [[ "$current_session" == "$main_session" ]]; then
            exit 0
          fi

          ensure_main_session
          tmux set-window-option -q @agent-home-session "$current_session"
          tmux rename-window "$(unique_window_name "$(project_name_for_path)")"

          if ! session_has_window "$main_session"; then
            tmux link-window -d -s "$window_id" -t "$main_session:"
          fi

          tmux switch-client -t "$main_session"
          tmux select-window -t "$window_id"
          ;;
        jump)
          target_session="$(find_linked_session | head -n 1)"
          if [[ -z "''${target_session:-}" ]]; then
            exit 0
          fi

          tmux switch-client -t "$target_session"
          tmux select-window -t "$window_id"
          ;;
        unlink)
          if [[ "$current_session" != "$main_session" ]]; then
            exit 0
          fi

          target_session="$(find_linked_session | head -n 1)"
          if [[ -z "''${target_session:-}" ]]; then
            exit 0
          fi

          tmux unlink-window -t "$main_session:$window_id"
          ;;
        *)
          exit 2
          ;;
      esac
    '';
  };
  home.file.".local/bin/tmux/last-command.sh" = {
    executable = true;
    text = ''
      #!/usr/bin/env bash
      set -euo pipefail

      cmd="''${1:-copy}"
      max_lines="''${TMUX_LAST_COMMAND_LINES:-5000}"
      max_bytes="''${TMUX_LAST_COMMAND_BYTES:-1048576}"
      clipboard_max_bytes="''${TMUX_LAST_COMMAND_CLIPBOARD_BYTES:-262144}"

      fail() {
        printf '%s\n' "$1" >&2
        exit 1
      }

      [[ "$max_lines" =~ ^[0-9]+$ ]] || fail "TMUX_LAST_COMMAND_LINES must be a non-negative integer"
      [[ "$max_bytes" =~ ^[0-9]+$ ]] || fail "TMUX_LAST_COMMAND_BYTES must be a non-negative integer"
      [[ "$clipboard_max_bytes" =~ ^[0-9]+$ ]] || fail "TMUX_LAST_COMMAND_CLIPBOARD_BYTES must be a non-negative integer"

      log_dir_for_pane() {
        local dir

        dir="$(tmux display -p -t "$1" '#{@command-log-dir}' 2>/dev/null || true)"
        if dir_has_completed_command "$dir"; then
          printf '%s\n' "$dir"
          return 0
        fi

        dir="$(stable_log_dir_for_pane "$1" 2>/dev/null || true)"
        if dir_has_completed_command "$dir"; then
          printf '%s\n' "$dir"
          return 0
        fi

        dir="$(legacy_log_dir_for_pane "$1")"
        if dir_has_completed_command "$dir"; then
          printf '%s\n' "$dir"
          return 0
        fi

        dir="$(tmux display -p -t "$1" '#{@command-log-dir}' 2>/dev/null || true)"
        [[ -n "$dir" ]] && printf '%s\n' "$dir" && return 0

        stable_log_dir_for_pane "$1" 2>/dev/null || legacy_log_dir_for_pane "$1"
      }

      stable_log_dir_for_pane() {
        local key raw

        raw="$(tmux display -p -t "$1" '#{session_name}:#{window_index}:#{pane_index}' 2>/dev/null || true)"
        [[ -n "$raw" ]] || return 1

        key="$(printf '%s' "$raw" | tr -c '[:alnum:]_.:-' '_')"
        printf '%s\n' "''${XDG_STATE_HOME:-$HOME/.local/state}/tmux-command-log/by-position/$key"
      }

      legacy_log_dir_for_pane() {
        local pane="$1"

        printf '%s\n' "''${XDG_STATE_HOME:-$HOME/.local/state}/tmux-command-log/''${pane#%}"
      }

      pane_has_completed_command() {
        local dir
        dir="$(log_dir_for_pane "$1")"
        dir_has_completed_command "$dir"
      }

      dir_has_completed_command() {
        local dir="$1"

        [[ -n "$dir" && -d "$dir" ]] || return 1

        for file in output.log cmd cwd start end status; do
          [[ -f "$dir/$file" ]] || return 1
        done
      }

      resolve_source_pane() {
        local current

        if [[ -n "''${TMUX_LAST_COMMAND_SOURCE:-}" ]]; then
          printf '%s\n' "$TMUX_LAST_COMMAND_SOURCE"
          return 0
        fi

        current="$(tmux display -p '#{pane_id}')"
        if pane_has_completed_command "$current"; then
          printf '%s\n' "$current"
          return 0
        fi

        fail "No completed command captured for current pane $current"
      }

      source_pane="$(resolve_source_pane)"
      log_dir="$(log_dir_for_pane "$source_pane")"

      [[ -n "$log_dir" && -d "$log_dir" ]] || fail "No command log for $source_pane"

      for file in output.log cmd cwd start end status; do
        [[ -f "$log_dir/$file" ]] || fail "No completed command captured for $source_pane"
      done

      log_file="$log_dir/output.log"
      command_text="$(cat "$log_dir/cmd")"
      cwd="$(cat "$log_dir/cwd")"
      start="$(cat "$log_dir/start")"
      end="$(cat "$log_dir/end")"
      status="$(cat "$log_dir/status")"

      [[ "$start" =~ ^[0-9]+$ && "$end" =~ ^[0-9]+$ && "$end" -ge "$start" ]] ||
        fail "Invalid command log offsets for $source_pane"

      compact_terminal_output() {
        ${pkgs.perl}/bin/perl -0777 -ne '
          use Encode qw(decode);
          binmode STDOUT, ":encoding(UTF-8)";

          my $s = decode("UTF-8", $_);
          my @lines = ("");
          my ($row, $col) = (0, 0);

          sub ensure_row {
            my ($target) = @_;
            $target = 0 if $target < 0;
            push @lines, "" while $#lines < $target;
          }

          sub set_cursor {
            my ($target_row, $target_col) = @_;
            $row = $target_row < 0 ? 0 : $target_row;
            $col = $target_col < 0 ? 0 : $target_col;
            ensure_row($row);
          }

          sub put_char {
            my ($ch) = @_;
            ensure_row($row);
            my $line = $lines[$row];
            my $len = length($line);
            $line .= " " x ($col - $len) if $col > $len;
            substr($line, $col, 1) = $ch;
            $lines[$row] = $line;
            $col++;
          }

          sub numeric_params {
            my ($params) = @_;
            return $params =~ /(\d+)/g;
          }

          sub handle_csi {
            my ($params, $cmd) = @_;
            my @nums = numeric_params($params);
            my $n = @nums ? $nums[0] : 1;

            if ($cmd eq "A") { set_cursor($row - $n, $col); return; }
            if ($cmd eq "B") { set_cursor($row + $n, $col); return; }
            if ($cmd eq "C") { set_cursor($row, $col + $n); return; }
            if ($cmd eq "D") { set_cursor($row, $col - $n); return; }
            if ($cmd eq "E") { set_cursor($row + $n, 0); return; }
            if ($cmd eq "F") { set_cursor($row - $n, 0); return; }
            if ($cmd eq "G") { set_cursor($row, $n - 1); return; }
            if ($cmd eq "H" || $cmd eq "f") {
              my $target_row = @nums >= 1 ? $nums[0] - 1 : 0;
              my $target_col = @nums >= 2 ? $nums[1] - 1 : 0;
              set_cursor($target_row, $target_col);
              return;
            }

            ensure_row($row);
            if ($cmd eq "K") {
              if ($params =~ /2/) {
                $lines[$row] = "";
              } elsif ($params =~ /1/) {
                my $line = $lines[$row];
                substr($line, 0, $col + 1) = " " x ($col + 1);
                $lines[$row] = $line;
              } else {
                substr($lines[$row], $col) = "";
              }
              return;
            }

            if ($cmd eq "J") {
              if ($params =~ /[23]/) {
                @lines = ("");
                set_cursor(0, 0);
              } elsif ($params =~ /1/) {
                for my $r (0 .. $row - 1) {
                  $lines[$r] = "";
                }
                my $line = $lines[$row];
                substr($line, 0, $col + 1) = " " x ($col + 1);
                $lines[$row] = $line;
              } else {
                substr($lines[$row], $col) = "";
                splice @lines, $row + 1 if @lines > $row + 1;
              }
            }
          }

          my $i = 0;
          while ($i < length($s)) {
            my $ch = substr($s, $i, 1);

            if ($ch eq "\e") {
              if (substr($s, $i, 2) eq "\e]") {
                my $bel = index($s, "\a", $i + 2);
                my $st = index($s, "\e\\", $i + 2);
                if ($bel >= 0 && ($st < 0 || $bel < $st)) {
                  $i = $bel + 1;
                } elsif ($st >= 0) {
                  $i = $st + 2;
                } else {
                  last;
                }
                next;
              }

              if (substr($s, $i, 2) eq "\e[") {
                my $j = $i + 2;
                $j++ while $j < length($s) && substr($s, $j, 1) !~ /[\x40-\x7e]/;
                last if $j >= length($s);
                handle_csi(substr($s, $i + 2, $j - $i - 2), substr($s, $j, 1));
                $i = $j + 1;
                next;
              }

              $i += 2;
              next;
            }

            if ($ch eq "\r") {
              $col = 0;
            } elsif ($ch eq "\n") {
              set_cursor($row + 1, 0);
            } elsif ($ch eq "\b") {
              set_cursor($row, $col - 1);
            } elsif ($ch eq "\t") {
              put_char(" ") for 1 .. (8 - ($col % 8));
            } elsif (ord($ch) >= 32 && ord($ch) != 127) {
              put_char($ch);
            }

            $i++;
          }

          for (@lines) {
            s/[ \t]+$//;
          }
          pop @lines while @lines && $lines[-1] eq "";
          print join("\n", @lines), "\n" if @lines;
        '
      }

      capture_log() {
        local bytes raw_start raw_bytes truncated

        if [[ "$end" -gt "$start" ]]; then
          bytes="$((end - start))"
          raw_start="$start"
          raw_bytes="$bytes"
          truncated=false

          if [[ "$max_bytes" != "0" && "$bytes" -gt "$max_bytes" ]]; then
            raw_start="$((end - max_bytes))"
            raw_bytes="$max_bytes"
            truncated=true
          fi

          {
            if [[ "$truncated" == true ]]; then
              printf '[last-command output truncated to last %s bytes before terminal redraw compaction]\n' "$max_bytes"
            fi
            tail -c +"$((raw_start + 1))" "$log_file" |
              head -c "$raw_bytes" |
              compact_terminal_output
          } |
            if [[ "$max_lines" == "0" ]]; then cat; else tail -n "$max_lines"; fi
        fi
      }

      copy_to_system_clipboard() {
        if [[ -n "''${WAYLAND_DISPLAY:-}" ]] && command -v wl-copy >/dev/null 2>&1; then
          ${pkgs.coreutils}/bin/timeout 2s wl-copy < "$tmp" 2>/dev/null && return 0
        fi

        if [[ -n "''${DISPLAY:-}" ]] && command -v xsel >/dev/null 2>&1; then
          ${pkgs.coreutils}/bin/timeout 2s xsel --clipboard --input < "$tmp" 2>/dev/null && return 0
        fi

        if [[ -n "''${DISPLAY:-}" ]] && command -v xclip >/dev/null 2>&1; then
          ${pkgs.coreutils}/bin/timeout 2s xclip -selection clipboard -in < "$tmp" 2>/dev/null && return 0
        fi

        return 1
      }

      load_copy_buffer() {
        local bytes

        bytes="$(wc -c < "$tmp" | tr -d ' ')"
        tmux load-buffer "$tmp"

        if [[ "$clipboard_max_bytes" == "0" || "$bytes" -le "$clipboard_max_bytes" ]]; then
          tmux load-buffer -w "$tmp"
          return 0
        fi

        copy_to_system_clipboard || true
      }

      tmp="$(mktemp)"
      output_tmp="$(mktemp)"
      log_output_tmp="$(mktemp)"
      current_buffer_tmp="$(mktemp)"
      trap 'rm -f "$tmp" "$output_tmp" "$log_output_tmp" "$current_buffer_tmp"' EXIT

      capture_log > "$log_output_tmp"
      if ! grep -q '[^[:space:]]' "$log_output_tmp"; then
        fail "Last command produced no output"
      fi
      cat "$log_output_tmp" > "$output_tmp"

      {
        printf 'Last command from tmux pane %s\n\n' "$source_pane"
        printf 'cwd: %s\n' "$cwd"
        printf 'exit status: %s\n\n' "$status"
        printf '```sh\n'
        printf '$ %s\n' "$command_text"
        printf '```\n\n'
        printf 'Output'
        if [[ "$max_lines" != "0" ]]; then
          printf ' (last %s lines)' "$max_lines"
        fi
        printf ':\n\n```text\n'
        cat "$output_tmp"
        printf '\n```\n'
      } > "$tmp"

      already_copied=false
      if tmux save-buffer "$current_buffer_tmp" 2>/dev/null && cmp -s "$tmp" "$current_buffer_tmp"; then
        already_copied=true
      else
        load_copy_buffer
      fi

      case "$cmd" in
        copy)
          ;;
        paste-last-pane)
          tmux paste-buffer
          ;;
        *)
          printf 'usage: %s {copy|paste-last-pane}\n' "$0" >&2
          exit 2
          ;;
      esac
    '';
  };

  programs.tmux = {
    extraConfig =
      ''
        set -g @resurrect-dir "$HOME/.local/share/tmux/resurrect"
        set -g @resurrect-hook-post-save-all '$HOME/.local/bin/tmux/resurrect-workbench-save.sh'
        set -g @resurrect-hook-pre-restore-pane-processes '$HOME/.local/bin/tmux/resurrect-workbench-restore.sh'
        set -g @resurrect-hook-post-restore-all '$HOME/.local/bin/tmux/workbench.sh recover-restored'
        set -g @resurrect-capture-pane-contents 'on'
        # The save hook rewrites agent panes to `codex resume <id>` /
        # `claude --resume <id>`; plain names replay that command verbatim.
        set -g @resurrect-processes 'codex claude'
        # A prompt can already be in use while background restoration runs.
        set -g @resurrect-never-overwrite 'on'

        set -g @continuum-restore 'on'
        set -g @continuum-save-interval '5'
        set -g @continuum-save-script '${continuumSaveScript}'
        set -g @continuum-boot 'on'
        set -g @continuum-boot-options 'ghostty'

        set -g status-interval 2
        # Keep the window list fixed when the left/right segments change width.
        set -g status-justify absolute-centre
        set -g status-position top
        set -g status-style 'bg=default'
        set -g status-left-length 50
        set -g status-right-length 48
        set -g status-bg 'default'
        set -g status-keys emacs
        # Show a theme immediately; the background initializer applies the
        # cached selection before loading Continuum. Reloads preserve it and
        # its autosave command, while the watcher handles GNOME theme changes.
        if -F '#{!=:#{@tmux-plugins-initialized},1}' \
          'source-file ~/.config/tmux/theme-dark.conf'
      ''
      + builtins.readFile ./tmux.conf
      + ''

        # Reloads (including GNOME theme changes) must not initialize plugins or
        # restore sessions again during Continuum's first-ten-seconds window.
        if -F '#{!=:#{@tmux-plugins-initialized},1}' {
          set -g @tmux-plugins-initialized 1
          run-shell -b '${initializeTmuxPlugins} >/dev/null 2>&1'
        }
      '';
    shortcut = lib.mkDefault "f";
    keyMode = "vi";
    baseIndex = 1;
    escapeTime = 20;
    historyLimit = 30000;
    aggressiveResize = true;
    focusEvents = true;
    clock24 = true;
    terminal = "tmux-256color";
    mouse = true;
  };
}
