{
  inputs,
  pkgs,
  ...
}: let
  system = pkgs.stdenv.hostPlatform.system;
  claudeCode = inputs.claude-code-nix.packages.${system}.default;
  json = pkgs.formats.json {};

  # Keys our tmux root table (modules/home/tmux) swallows before Claude Code
  # ever sees them, so nothing below may use them:
  #   - ctrl+f            prefix (programs.tmux.shortcut = "f")
  #   - alt+<letter>      pane/window management, the whole letter space
  #   - alt+space,
  #     alt+enter,
  #     alt+; alt+' alt+0
  #   - ctrl+shift+{q,w,e,r,t,h,j,k,l}
  #   - ctrl+\  ctrl+alt+y
  # Plain ctrl+<lowercase letter> reaches Claude fine (tmux 3.6 keeps the
  # ctrl+shift variants distinct), except for the prefix itself.
  #
  # Claude Code hardcodes ctrl+c / ctrl+d / ctrl+m, so vi's ctrl+d half-page
  # scroll is impossible; ctrl+v stands in for it.
  keybindings = {
    "$schema" = "https://www.schemastore.org/claude-code-keybindings.json";
    "$docs" = "https://code.claude.com/docs/en/keybindings";

    bindings = [
      {
        context = "Global";
        bindings = {
          # ctrl+x is Claude's own chord prefix, and readline's, so it is safe
          # to hang a leader off it. Needed because every alt+<letter> default
          # is unreachable inside tmux.
          "ctrl+x d" = "app:cycleDiffBase";
          "ctrl+x n" = "app:toggleDiffNoiseFilter";
          "ctrl+x s" = "app:toggleTerminal";
        };
      }

      {
        context = "Chat";
        bindings = {
          # Rescue the dead alt+ defaults (meta+p/o/t/w). The originals stay
          # bound for when Claude runs outside tmux.
          "ctrl+x m" = "chat:modelPicker";
          "ctrl+x f" = "chat:fastMode";
          "ctrl+x t" = "chat:thinkingToggle";
          "ctrl+x w" = "chat:workflowKeywordToggle";

          # readline/vi-insert reflexes; the arrows keep working.
          "ctrl+p" = "history:previous";
          "ctrl+n" = "history:next";
        };
      }

      {
        # Fullscreen scrollable view: upstream binds no vi keys here at all,
        # only pageup/pagedown/ctrl+home/ctrl+end.
        #
        # Bare letters must NOT go here: the chat input shares focus with this
        # view, so plain space/j/k/g/f/b got swallowed while typing. That is
        # why upstream uses no unmodified keys in this context. For vi keys
        # incl. G and gg, open the Transcript (ctrl+o) instead.
        # Do not bind escape here either: it is chat:cancel, and with
        # editorMode = "vim" it is also insert->normal.
        context = "Scroll";
        bindings = {
          # vi g/G on the leader, since bare letters are unsafe here and
          # ctrl+shift+<letter> collapses to ctrl+<letter> (tmux runs with
          # extended-keys off, so no CSI-u reaches us). ctrl+end / ctrl+home
          # stay bound as fallbacks.
          "ctrl+x g" = "scroll:top";
          "ctrl+x shift+g" = "scroll:bottom";
          "end" = "scroll:bottom";
          "home" = "scroll:top";
          "ctrl+e" = "scroll:lineDown";
          "ctrl+y" = "scroll:lineUp";
          "ctrl+u" = "scroll:halfPageUp";
          "ctrl+v" = "scroll:halfPageDown";
          "ctrl+b" = "scroll:fullPageUp";
        };
      }

      {
        context = "Transcript";
        bindings = {
          # f/b like less, since ctrl+f is the tmux prefix.
          "f" = "scroll:fullPageDown";
          "ctrl+v" = "scroll:halfPageDown";
          "ctrl+x g" = "scroll:top"; # same leader as in Scroll
          "ctrl+x shift+g" = "scroll:bottom";
          "ctrl+e" = "scroll:lineDown"; # displaces transcript:toggleShowAll
          "ctrl+y" = "scroll:lineUp";
          "a" = "transcript:toggleShowAll"; # new home for it
        };
      }

      {
        context = "Confirmation";
        bindings = {
          "j" = "confirm:next";
          "k" = "confirm:previous";
        };
      }

      {
        context = "DiffDialog";
        bindings = {
          "h" = "diff:previousSource";
          "l" = "diff:nextSource";
          "q" = "diff:dismiss";
        };
      }

      {
        context = "Tabs";
        bindings = {
          "h" = "tabs:previous";
          "l" = "tabs:next";
        };
      }

      {
        context = "Attachments";
        bindings = {
          "h" = "attachments:previous";
          "l" = "attachments:next";
        };
      }

      {
        context = "ModelPicker";
        bindings = {
          "h" = "modelPicker:decreaseEffort";
          "l" = "modelPicker:increaseEffort";
        };
      }

      {
        context = "Footer";
        bindings = {
          "h" = "footer:previous";
          "l" = "footer:next";
        };
      }

      {
        context = "Select";
        bindings = {
          "q" = "select:cancel";
        };
      }
    ];
  };
in {
  home.packages = [claudeCode];

  home.file.".claude/keybindings.json" = {
    source = json.generate "claude-keybindings.json" keybindings;
    force = true;
  };

  # Global (user-level) instructions, applied to every project. The symlink is
  # read-only, so Claude's in-app memory edits (the # shortcut, /memory) fail
  # against user memory once this is deployed — deliberate: edit
  # claude-global.md here and switch instead, so every machine stays in sync.
  home.file.".claude/CLAUDE.md" = {
    source = ./claude-global.md;
    force = true;
  };

  # Status line showing the 5h and weekly usage limits. Wired up by hand in
  # ~/.claude/settings.json ("statusLine".command -> this path) rather than
  # here: Claude Code writes settings.json itself (/config, model changes), so a
  # read-only home-manager symlink there would break it.
  home.file.".claude/statusline" = {
    # -SE (combined; a Linux shebang takes only one argument) skips site
    # initialisation and env-var handling, cutting ~4.5ms off a ~19ms render.
    text = "#!${pkgs.python3}/bin/python3 -SE\n" + builtins.readFile ./claude-statusline.py;
    executable = true;
    # The live path has ended up a plain file before (hand-deployed edits);
    # let activation replace it rather than abort with "existing file is in
    # the way".
    force = true;
  };
}
