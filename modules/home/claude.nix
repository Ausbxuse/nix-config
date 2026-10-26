{
  config,
  inputs,
  lib,
  pkgs,
  ...
}: let
  system = pkgs.stdenv.hostPlatform.system;
  claudeCode = inputs.claude-code-nix.packages.${system}.default;
  json = pkgs.formats.json {};

  # Claude Code rewrites ~/.claude/settings.json itself (/config, model and
  # theme changes), so it cannot be a read-only home.file link. Instead these
  # keys are merged into it on every switch, leaving everything else as is
  # (tmux/default.nix registers its hooks the same way).
  settings = {
    statusLine = {
      type = "command";
      command = "$HOME/.claude/statusline";
    };
    # Auto mode as the default on every host, not only where it was opted
    # into once (that choice lives in the per-host ~/.claude.json).
    permissions.defaultMode = "auto";
  };
  mergeSettings = pkgs.writers.writePython3 "merge-claude-settings" {} ''
    import json
    import os
    import sys

    path, managed = sys.argv[1], json.loads(sys.argv[2])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path) as fh:
            current = json.load(fh)
    except FileNotFoundError:
        current = {}
    if not isinstance(current, dict):
        raise SystemExit(path + " is not a JSON object; refusing")


    def merge(into, keys):
        for key, value in keys.items():
            if isinstance(value, dict) and isinstance(into.get(key), dict):
                merge(into[key], value)
            else:
                into[key] = value


    merge(current, managed)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(current, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
  '';

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
  # scroll is impossible; ctrl+v stands in for it in the Transcript, and
  # ctrl+x v in Scroll, which shares focus with the prompt: ctrl+v there
  # swallowed chat:imagePaste, so images could not be pasted.
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
          "ctrl+x v" = "scroll:halfPageDown";
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

  home.activation.mergeClaudeSettings = lib.hm.dag.entryAfter ["writeBoundary"] ''
    run ${mergeSettings} ${lib.escapeShellArg "${config.home.homeDirectory}/.claude/settings.json"} \
      ${lib.escapeShellArg (builtins.toJSON settings)}
  '';

  # Status line showing the 5h and weekly usage limits ("statusLine" above).
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

  # Claude Code themes for the snappy terminal palettes. The -ansi bases make
  # text, accents and syntax highlighting (which always uses ANSI colours) come
  # from the terminal palette itself; only greys and backgrounds with no ANSI
  # equivalent are fixed per mode. settings.json selects "custom:snappy"; the
  # GNOME theme watcher (tmux/theme-watch.sh) copies the matching file over
  # ~/.claude/themes/snappy.json, which Claude Code reloads live.
  xdg.dataFile = let
    ansiAccents = {
      claude = "ansi:yellow";
      claudeShimmer = "ansi:yellowBright";
      suggestion = "ansi:blue";
      permission = "ansi:blue";
      permissionShimmer = "ansi:blueBright";
      remember = "ansi:magenta";
      success = "ansi:green";
      error = "ansi:red";
      warning = "ansi:yellow";
      warningShimmer = "ansi:yellowBright";
      merged = "ansi:magenta";
      planMode = "ansi:cyan";
      autoAccept = "ansi:magenta";
      bashBorder = "ansi:magenta";
      ide = "ansi:blue";
      fastMode = "ansi:yellow";
      fastModeShimmer = "ansi:yellowBright";
    };
    theme = base: overrides:
      json.generate "snappy-${base}.json" {
        name = "Snappy";
        inherit base;
        overrides = ansiAccents // overrides;
      };
  in {
    "claude-themes/snappy-dark.json".source = theme "dark-ansi" {
      inactive = "#868db0";
      inactiveShimmer = "#a9b1d6";
      subtle = "#414868";
      promptBorder = "#565f89";
      promptBorderShimmer = "#868db0";
      diffAdded = "#26392c";
      diffRemoved = "#45262d";
      diffAddedDimmed = "#232f2b";
      diffRemovedDimmed = "#33252c";
      diffAddedWord = "#3a5c3f";
      diffRemovedWord = "#733a46";
      userMessageBackground = "#262b3f";
      userMessageBackgroundHover = "#2d3349";
      bashMessageBackgroundColor = "#2b2840";
      memoryBackgroundColor = "#28293f";
      selectionBg = "#3c4e64";
    };
    "claude-themes/snappy-light.json".source = theme "light-ansi" {
      inactive = "#626B5E";
      inactiveShimmer = "#7C8576";
      subtle = "#B9C2AE";
      promptBorder = "#9AA38F";
      promptBorderShimmer = "#626B5E";
      diffAdded = "#D8E8CE";
      diffRemoved = "#F3DAD6";
      diffAddedDimmed = "#E3EBDA";
      diffRemovedDimmed = "#F2E4E1";
      diffAddedWord = "#B8D9A6";
      diffRemovedWord = "#EAB4AE";
      userMessageBackground = "#E3E7D6";
      userMessageBackgroundHover = "#DADFCB";
      bashMessageBackgroundColor = "#E8E2E6";
      memoryBackgroundColor = "#E6E2EA";
      selectionBg = "#D8E2CC";
    };
  };
}
