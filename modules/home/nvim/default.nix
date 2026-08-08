{
  config,
  lib,
  pkgs,
  ...
}: let
  nvimPath = "${config.home.homeDirectory}/src/public/nix-config/modules/home/nvim/nvim";
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
in {
  xdg.configFile."nvim".source = config.lib.file.mkOutOfStoreSymlink nvimPath;

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

  home.packages = with pkgs; [
    gnomeClipboard
    # lua51Packages.luarocks-nix
    # fortune
    nodejs
    tree-sitter
    # inotify-tools
    fd
    ripgrep
    pre-commit
    file
    # jdk
    # cargo
    gcc

    # Language servers used by the Neovim LSP config.
    astro-language-server
    basedpyright
    bash-language-server
    clang-tools
    harper
    ltex-ls-plus
    lua-language-server
    marksman
    nil
    nixd
    tailwindcss-language-server
    taplo
    tinymist
    typos-lsp
    vscode-langservers-extracted
    yaml-language-server
  ];

  programs.neovim = {
    enable = true;
    defaultEditor = true;
    sideloadInitLua = true;
    withPython3 = true;
    withRuby = true;
    viAlias = true;
    vimAlias = true;
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
