{
  config,
  lib,
  pkgs,
  const,
  hostname,
  hostDefs,
  inputs,
  ...
}: let
  watt = pkgs.writeShellApplication {
    name = "watt";
    runtimeInputs = with pkgs; [
      gawk
    ];
    text = builtins.readFile ../../../scripts/watt.sh;
  };
in {
  home.packages = with pkgs; [
    trash-cli
    ueberzugpp
    bat
    nyancat
    ripgrep
    fd
    eza
    btop
    chafa
    timg
    fastfetch
    sdcv
    wl-clipboard
    xsel
    gdu
    watt
  ];

  programs.fzf = {
    enable = true;
    enableZshIntegration = false;
    fileWidget.command = "fd --exclude .git -H --max-depth 10 -t f -t l";
    changeDirWidget.command = "fd --exclude .git -H --max-depth 12 -t d";
    defaultOptions = ["--reverse"];
  };

  programs.zoxide = {
    enable = true;
    enableZshIntegration = false;
  };

  programs.carapace = {
    enable = true;
    enableZshIntegration = false;
    ignoreCase = true;
  };

  programs.atuin = {
    enable = true;
    enableZshIntegration = true;
    flags = ["--disable-up-arrow"];
    settings = {
      auto_sync = false;
      update_check = false;
    };
  };

  programs.yazi = {
    enable = true;
    shellWrapperName = "y";
    enableZshIntegration = true;
    settings = {
      log = {
        enabled = false;
      };
      opener = {
        edit = [
          {
            run = ''nvim "$@"'';
            block = true;
            for = "unix";
          }
        ];
        image = [
          {
            run = ''xdg-open "$1"'';
            block = true;
            for = "unix";
          }
        ];
        open = [
          {
            run = ''xdg-open "$1"'';
            orphan = true;
            for = "unix";
          }
        ];
        video = [
          {
            run = ''xdg-open "$1"'';
            block = true;
            for = "unix";
          }
        ];
        pdf = [
          {
            run = ''zathura "$@"'';
            orphan = true;
            for = "unix";
          }
        ];
      };
      open = {
        rules = [
          {
            mime = "text/*";
            use = "edit";
          }
          {
            mime = "application/json";
            use = "edit";
          }
          {
            mime = "application/xml";
            use = "edit";
          }
          {
            mime = "image/*";
            use = "image";
          }
          {
            mime = "video/*";
            use = "video";
          }
          {
            mime = "application/pdf";
            use = "pdf";
          }
          {
            url = "*";
            use = "open";
          }
        ];
      };
      mgr = {
        linemode = "mtime";
        show_symlink = true;
        scrolloff = 5;
        sort_sensitive = true;
        show_hidden = true;
        sort_by = "mtime";
        sort_dir_first = true;
        sort_reverse = true;
      };
      preview = {
        # Avoid overwhelming Ghostty's Wayland connection when moving quickly
        # across images; Yazi's 30 ms default can enqueue previews too fast.
        image_delay = 100;
      };
      plugin = {
        prepend_previewers = [
          {
            mime = "image/svg+xml";
            run = "code";
          }
          {
            url = "*.svg";
            run = "code";
          }
        ];
        prepend_preloaders = [
          {
            mime = "image/svg+xml";
            run = "noop";
          }
          {
            url = "*.svg";
            run = "noop";
          }
        ];
      };
    };
  };

  programs.git = {
    enable = true;
    settings = lib.mkMerge [
      (lib.optionalAttrs (const ? name) {
        user.name = const.name;
      })
      (lib.optionalAttrs (const ? email) {
        user.email = const.email;
      })
      {
        commit.gpgsign = true;
        gpg.format = "ssh";
        merge.tool = "nvim-conflict";
        mergetool = {
          prompt = false;
          "nvim-conflict" = {
            cmd = ''NVIM_MERGETOOL=1 nvim "$MERGED" -c only'';
            trustExitCode = false;
          };
        };
        user.signingkey = "~/.ssh/id_ed25519.pub";
      }
    ];
  };

  programs.delta = {
    enable = true;
    enableGitIntegration = true;
    options = {
      # decorations = {
      #   commit-decoration-style = "bold yellow box ul";
      #   file-decoration-style = "none";
      #   file-style = "bold yellow ul";
      # };
      side-by-side = true;
      features = "decorations";
      whitespace-error-style = "22 reverse";
    };
  };

  home.activation.compileZshCompdump = lib.hm.dag.entryAfter ["writeBoundary"] ''
    dotdir=${config.programs.zsh.dotDir}
    ${pkgs.zsh}/bin/zsh -fc '
      autoload -U compinit
      compinit -d "'"$dotdir"'"/.zcompdump -C
      if [[ -s "'"$dotdir"'"/.zcompdump ]]; then
        zcompile "'"$dotdir"'"/.zcompdump
      fi
    ' >/dev/null 2>&1 || true
  '';

  programs.zsh = {
    enable = true;
    dotDir = "${config.home.homeDirectory}/.config/zsh";
    enableCompletion = true;
    # autosuggestion.enable = false;
    # syntaxHighlighting.enable = false;
    # syntaxHighlighting.styles = {
    #   path = "fg=magenta,bold";
    # };
    historySubstringSearch.enable = false;
    shellAliases = {
      ncdu = "ncdu --color dark -e -q -L";
      cp = "cp -iv";
      mv = "mv -i";
      rm = "trash-put";
      mkd = "mkdir -pv";
      yt = "youtube-dl --add-metadata -i";
      yta = "yt -x -f bestaudio/best";
      ffmpeg = "ffmpeg -hide_banner";
      gpu = "nvidia-offload";
      # ls="eza --long --git --color=always --no-filesize --icons=always --no-user --no-time --no-permissions --sort=date" ;
      l = "eza --git --color=always --no-filesize --icons=always --no-user --no-time --no-permissions --sort=modified --reverse --group-directories-first";
      ls = "eza --long --git --color=always --icons=always --sort=modified --reverse --group-directories-first";
      grep = "grep --color=auto";
      diff = "diff --color=auto";
      ccat = "highlight --out-format=ansi";
      s = "sdcv -c -u 'WordNet® 3.0 (En-En)'";
      ga = "git commit -a";
      sdn = "sudo shutdown -h now";
      f = "$FILE";
      e = "$EDITOR";
      v = "$EDITOR";
      o = "xdg-open";
      #ref="shortcuts >/dev/null; source ${config.xdg.configHome}/shortcutrc ; source ${config.xdg.configHome}/zshnameddirrc" ;
      weath = "less -S ${config.xdg.cacheHome}/weatherreport";
      # tmux = "tmux -f ${config.xdg.configHome}/tmux/tmux.conf attach -t main || tmux -f ${config.xdg.configHome}/tmux/tmux.conf new-session -s main";
      cf = "cd $HOME/.config && ls -a";
      cac = "cd ${config.xdg.cacheHome} && ls -a";
      D = "cd $HOME/Downloads && ls -a";
      d = "cd $HOME/Documents && ls -a";
      dt = "cd $HOME/.local/share && ls -a";
      h = "cd $HOME && ls -a";
      m = "cd $HOME/Media/Music && ls -a";
      mn = "cd /mnt && ls -a";
      sc = "cd $HOME/.local/bin && ls -a";
      src = "cd $HOME/src/public && ls -a";
      vv = "cd $HOME/Media/Videos && ls -a";
    };
    history.size = 10000000;
    history.save = 10000000;
    history.path = "${config.xdg.dataHome}/zsh/history";
    history.extended = true;
    autocd = true;
    defaultKeymap = "viins";
    completionInit = ''
      zstyle ':completion:*' matcher-list "" 'm:{a-zA-Z}={A-Za-z}' 'r:|[._-]=* r:|=*' 'l:|=* r:|=*'
      export KEYTIMEOUT=1
    '';

    initContent = ''
      ${builtins.readFile ./zshrc}
      ${lib.optionalString config.programs.zsh.enable ''
        if [[ $options[zle] = on ]]; then
          printf '\e[6 q'
          autoload -U compinit
          zmodload zsh/complist
          compinit -d ${config.programs.zsh.dotDir}/.zcompdump -C
          _comp_options+=(globdots)

          source ${pkgs.zsh-autosuggestions}/share/zsh-autosuggestions/zsh-autosuggestions.zsh
          source <(${pkgs.fzf}/bin/fzf --zsh)
          source ${inputs.zsh-better-prompt.packages.${pkgs.stdenv.hostPlatform.system}.default}/share/better-prompt/better-prompt.zsh
          source ${pkgs.zsh-fast-syntax-highlighting}/share/zsh/plugins/fast-syntax-highlighting/fast-syntax-highlighting.plugin.zsh
          source ${pkgs.zsh-fzf-tab}/share/fzf-tab/fzf-tab.plugin.zsh
          eval "$(${pkgs.zoxide}/bin/zoxide init zsh)"
          eval "$(${pkgs.direnv}/bin/direnv hook zsh)"

          if (( $+functions[_zsh_autosuggest_bind_widgets] )); then
            _zsh_autosuggest_bind_widgets
          fi
        fi
      ''}
      ${lib.optionalString ((hostDefs.${hostname}.visibility or "private") == "private" && lib.hasAttrByPath ["sops" "secrets" "anthropic"] config && lib.hasAttrByPath ["sops" "secrets" "gemini"] config) ''
        export ANTHROPIC_API_KEY="$(cat ${config.sops.secrets.anthropic.path})"
        export GEMINI_API_KEY="$(cat ${config.sops.secrets.gemini.path})"
      ''}
    '';

    profileExtra = ''
      if [ -e ~/.nix-profile/etc/profile.d/nix.sh ]; then
          . ~/.nix-profile/etc/profile.d/nix.sh
      elif [ -e /etc/profile.d/nix.sh ]; then
          . /etc/profile.d/nix.sh
      fi
    '';
    plugins = [
      {
        name = "zsh-history-substring-search";
        src = pkgs.zsh-history-substring-search;
        file = "share/zsh-history-substring-search/zsh-history-substring-search.zsh";
      }

      {
        name = "zsh-nix-shell";
        src = pkgs.zsh-nix-shell;
        file = "share/zsh-nix-shell/nix-shell.plugin.zsh";
      }
    ];
  };
}
