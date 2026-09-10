{
  config,
  lib,
  pkgs,
  ...
}: let
  mkPinnedVimPlugin = {
    pname,
    owner,
    repo,
    rev,
    hash,
  }:
    pkgs.vimUtils.buildVimPlugin {
      inherit pname;
      version = "0-unstable-${builtins.substring 0 7 rev}";
      src = pkgs.fetchFromGitHub {
        inherit owner repo rev hash;
      };
    };

  bentoNvim = mkPinnedVimPlugin {
    pname = "bento.nvim";
    owner = "serhez";
    repo = "bento.nvim";
    rev = "aee8a542bc1839ff1764992ef2aa41c981d0bba4";
    hash = "sha256-4HM0Ewx7AJFiprIPGriv+mY0l/BnRL/xHy1ROJtcTK0=";
  };
  snappyNvim = mkPinnedVimPlugin {
    pname = "snappy.nvim";
    owner = "ausbxuse";
    repo = "snappy.nvim";
    rev = "137eaa6e98e8a6b3dd63548c2de4422007a9e689";
    hash = "sha256-yV66pkkfL6IFlqyH9IUNi5C9/Gc0Nn5WW3KQJtONLK0=";
  };
  symbolsNvim = mkPinnedVimPlugin {
    pname = "symbols.nvim";
    owner = "oskarrrrrrr";
    repo = "symbols.nvim";
    rev = "b22e987c69749adcf1342e8040312e9318083d4a";
    hash = "sha256-d70H2zsQb1KbbgWAYkr14xQ52lvhweYzGN6KURl4hj8=";
  };

  # The current nixpkgs Tree-sitter set does not yet contain the tmux grammar
  # used by this config, so retain the revision from nvim-pack-lock.json here.
  tmuxGrammar = pkgs.tree-sitter.buildGrammar {
    language = "tmux";
    version = "0.0.0+rev=75d1b99";
    src = pkgs.fetchFromGitHub {
      owner = "Freed-Wu";
      repo = "tree-sitter-tmux";
      rev = "75d1b995b0c23400ac8e49db757a2e0386f9fa8f";
      hash = "sha256-LdXPdijcsfPYIrbTMDIy46wqOaJfxwVBVpOVVfXrJIg=";
    };
  };
  tmuxGrammarPlugin = pkgs.neovimUtils.grammarToPlugin tmuxGrammar;
  treeSitterLockedSource = pkgs.fetchFromGitHub {
    owner = "nvim-treesitter";
    repo = "nvim-treesitter";
    rev = "4916d6592ede8c07973490d9322f187e07dfefac";
    hash = "sha256-PQR6tFt4lCrAZNQG7BLMD1IiCKja9wDS1S4laGJf/HE=";
  };
  tmuxQueries = pkgs.vimUtils.toVimPlugin (
    pkgs.runCommandLocal "nvim-treesitter-queries-tmux" {
      passthru.isTreesitterQuery = true;
    } ''
      mkdir -p "$out/queries"
      cp -R ${treeSitterLockedSource}/runtime/queries/tmux "$out/queries/tmux"
    ''
  );
  nvimTreesitter = (pkgs.vimPlugins.nvim-treesitter.withPlugins (grammars:
    with grammars; [
      bash
      c
      comment
      cpp
      diff
      html
      just
      lua
      luadoc
      markdown
      markdown_inline
      nix
      python
      query
      tsx
      typescript
      vim
      vimdoc
      yaml
      zsh
    ])).overrideAttrs (old: {
    passthru =
      (old.passthru or {})
      // {
        dependencies = (old.passthru.dependencies or []) ++ [tmuxGrammarPlugin tmuxQueries];
      };
  });

  optionalPlugin = plugin: {
    inherit plugin;
    optional = true;
  };
  declarativePlugins = map optionalPlugin [
    pkgs.vimPlugins.auto-session
    bentoNvim
    pkgs.vimPlugins.blink-cmp
    pkgs.vimPlugins.blink-copilot
    pkgs.vimPlugins.cellular-automaton-nvim
    pkgs.vimPlugins.conform-nvim
    pkgs.vimPlugins.copilot-lua
    pkgs.vimPlugins.dropbar-nvim
    pkgs.vimPlugins.fzf-lua
    pkgs.vimPlugins.gitsigns-nvim
    pkgs.vimPlugins.indent-blankline-nvim
    pkgs.vimPlugins.lazydev-nvim
    pkgs.vimPlugins.markdown-preview-nvim
    pkgs.vimPlugins.nvim-bqf
    pkgs.vimPlugins.nvim-colorizer-lua
    pkgs.vimPlugins.nvim-dap
    pkgs.vimPlugins.nvim-dap-ui
    pkgs.vimPlugins.nvim-nio
    nvimTreesitter
    pkgs.vimPlugins.nvim-treesitter-context
    pkgs.vimPlugins.nvim-treesitter-textobjects
    pkgs.vimPlugins.plenary-nvim
    snappyNvim
    symbolsNvim
    pkgs.vimPlugins.undotree
    pkgs.vimPlugins.vimtex
    pkgs.vimPlugins.yazi-nvim
    pkgs.vimPlugins.zk-nvim
  ];

  nvimRuntimePackages = with pkgs; [
    nodejs
    tree-sitter
    fd
    ripgrep
    pre-commit
    file
    gcc
    fzf
    yazi
    zk
    neovim-remote

    # Language servers used by the Neovim LSP config.
    basedpyright
    bash-language-server
    clang-tools
    harper
    lua-language-server
    marksman
    nil
    nixd
    tailwindcss-language-server
    taplo
    typos-lsp
    vscode-langservers-extracted
    yaml-language-server
  ];

  gnomeClipboard = pkgs.stdenv.mkDerivation {
    pname = "nvim-gnome-clipboard";
    version = "1";
    dontUnpack = true;
    nativeBuildInputs = [
      pkgs.makeWrapper
      pkgs.wrapGAppsHook3
      # Without this, its setup hook never collects typelibs from buildInputs,
      # so wrapGAppsHook3 has no GI_TYPELIB_PATH to inject and the script dies
      # with "Typelib file for namespace 'Gdk', version '3.0' not found".
      pkgs.gobject-introspection
    ];
    buildInputs = [
      pkgs.gjs
      pkgs.gtk3
    ];
    installPhase = ''
      install -Dm644 ${./gnome-clipboard.js} $out/share/nvim-gnome-clipboard/gnome-clipboard.js

      makeWrapper ${pkgs.gjs}/bin/gjs $out/bin/nvim-gnome-clipboard \
        --add-flags $out/share/nvim-gnome-clipboard/gnome-clipboard.js
    '';
  };
  nvimSpellDir = "${config.xdg.stateHome}/nvim/spell";
in {
  # Keep the deployed editor configuration inside the Home Manager closure.
  # Hosts must not depend on this repository existing at one particular path.
  xdg.configFile."nvim".source = ./nvim;

  xdg.desktopEntries.nvim = {
    name = "Neovim";
    genericName = "Text Editor";
    comment = "Edit text files in Neovim";
    exec = "nvim %F";
    icon = "nvim";
    terminal = true;
    type = "Application";
    categories = ["Utility" "TextEditor"];
    mimeType = [
      "application/x-shellscript"
      "text/markdown"
      "text/plain"
      "text/x-nix"
      "text/x-python"
      "text/x-typst"
    ];
  };

  home.packages = [gnomeClipboard] ++ nvimRuntimePackages;

  # `~/.config/nvim` is deliberately store-backed.  Keep the personal word
  # list in XDG state instead, where `zg` and `:spellgood` can update it.
  # Seed both files once without replacing words learned by the user later.
  home.activation.initializeNvimSpellfile = lib.hm.dag.entryAfter ["writeBoundary"] ''
    run ${pkgs.coreutils}/bin/mkdir -p ${lib.escapeShellArg nvimSpellDir}
    if [[ ! -e ${lib.escapeShellArg "${nvimSpellDir}/en.utf-8.add"} ]]; then
      run ${pkgs.coreutils}/bin/install -m 0600 \
        ${./nvim/spell/en.utf-8.add} \
        ${lib.escapeShellArg "${nvimSpellDir}/en.utf-8.add"}
    fi
    if [[ ! -e ${lib.escapeShellArg "${nvimSpellDir}/en.utf-8.add.spl"} ]]; then
      run ${pkgs.coreutils}/bin/install -m 0600 \
        ${./nvim/spell/en.utf-8.add.spl} \
        ${lib.escapeShellArg "${nvimSpellDir}/en.utf-8.add.spl"}
    fi
  '';

  programs.neovim = {
    enable = true;
    defaultEditor = true;
    sideloadInitLua = true;
    withPython3 = true;
    withRuby = true;
    viAlias = true;
    vimAlias = true;
    plugins = declarativePlugins;
    extraPackages = nvimRuntimePackages;
    extraWrapperArgs = [
      "--set"
      "NVIM_NIX_PACKAGED"
      "1"
      "--set"
      "NVIM_LUVIT_META_PATH"
      "${pkgs.vimPlugins.luvit-meta}/library"
    ];
    # extraWrapperArgs = with pkgs; [
    #   # LIBRARY_PATH is used by gcc before compilation to search directories
    #   # containing static and shared libraries that need to be linked to your program.
    #   "--suffix"
    #   "LIBRARY_PATH"
    #   ":"
    #   "${lib.makeLibraryPath [stdenv.cc.cc zlib]}"
    #
    #   # PKG_CONFIG_PATH is used by pkg-config before compilation to search directories
    #   # containing .pc files that describe the libraries that need to be linked to your program.
    #   "--suffix"
    #   "PKG_CONFIG_PATH"
    #   ":"
    #   "${lib.makeSearchPathOutput "dev" "lib/pkgconfig" [stdenv.cc.cc zlib]}"
    # ];
  };
}
