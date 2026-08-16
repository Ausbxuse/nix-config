{
  config,
  lib,
  inputs,
  hostDef,
  pkgs,
  ...
}: let
  useNixGL =
    pkgs.stdenv.hostPlatform.isx86_64
    && !(hostDef.nixos.enable or false);
  ghosttyBasePackage =
    if useNixGL
    then config.lib.nixGL.wrap pkgs.ghostty
    else pkgs.ghostty;
  ghosttyPackage =
    if useNixGL
    then
      pkgs.symlinkJoin {
        name = "ghostty-x11-${pkgs.ghostty.version}";
        paths = [ghosttyBasePackage];
        inherit (pkgs.ghostty) meta;
        nativeBuildInputs = [pkgs.makeWrapper];
        postBuild = ''
          # GTK/Wayland exits on EAGAIN while Ghostty processes Kitty image
          # previews. XWayland avoids that fatal display-flush path.
          wrapProgram "$out/bin/ghostty" --set GDK_BACKEND x11
        '';
      }
    else ghosttyBasePackage;
in {
  xdg.configFile."ghostty/shaders/cursor_warp.glsl".source = ./cursor_warp.glsl;

  targets.genericLinux = lib.mkIf useNixGL {
    enable = lib.mkDefault true;
    nixGL = {
      packages = import inputs.nixgl {inherit pkgs;};
      defaultWrapper = "mesa";
      installScripts = ["mesa"];
    };
  };

  programs = {
    ghostty = {
      enable = true;
      package = ghosttyPackage;
      themes = {
        snappy = {
          palette = [
            "0=#2c3144"
            "1=#dd7a84"
            "2=#8ac48a"
            "3=#d5ad63"
            "4=#6fb0f4"
            "5=#a487eb"
            "6=#62bcc6"
            "7=#c9d1e2"

            "8=#5d6784"
            "9=#e88c95"
            "10=#9dd29d"
            "11=#dfbd78"
            "12=#88bfff"
            "13=#b79ef5"
            "14=#7bcdd6"
            "15=#edf1f9"
          ];

          background = "#181a24";
          foreground = "#c8d0e0";
          cursor-color = "#c8d0e0";
          cursor-text = "#1f2435";
          # cursor-invert-fg-bg = true;
          selection-background = "#283044";
          selection-foreground = "#dce4f2";
        };
        snappy_light = {
          palette = [
            "0=#5A6275" # black / neutral ink
            "1=#C95A6A" # red
            "2=#4F956C" # green
            "3=#BA7F26" # yellow
            "4=#2B7FD6" # blue
            "5=#A463D8" # magenta
            "6=#2F98AB" # cyan
            "7=#6D7588" # white / dark neutral

            "8=#98A1B5" # bright black / muted gutter
            "9=#DB7080" # bright red
            "10=#66A981" # bright green
            "11=#CF953E" # bright yellow
            "12=#4A97E8" # bright blue
            "13=#B57BE5" # bright magenta
            "14=#4DAFBE" # bright cyan
            "15=#202533" # bright white / main ink
          ];

          background = "#F5EFE4"; # warm paper
          foreground = "#202533"; # main ink
          cursor-color = "#202533";
          cursor-text = "#F5EFE4";

          selection-background = "#D9E4F5"; # day highlight layer
          selection-foreground = "#202533";
        };
      };
      settings = {
        theme = "dark:snappy,light:snappy_light";
        # custom-shader = "shaders/cursor_warp.glsl";

        font-family = "Maple Mono NF";
        font-size = lib.mkDefault 12;
        font-thicken = true;
        font-feature = [
          "+ss01"
          "+ss02"
          "+zero"
        ];

        shell-integration-features = "no-cursor,no-sudo,title";
        bell-features = "no-system,no-audio,no-attention,no-title,no-border";
        cursor-style-blink = false;
        adjust-cursor-thickness = 2;
        cursor-style = "bar";

        # gtk-adwaita = false;
        resize-overlay = "never";
        clipboard-read = "allow";
        clipboard-write = "allow";
        clipboard-paste-protection = false;
        gtk-single-instance = false;

        window-decoration = false;
        # background-opacity = 1;
        background-opacity = 0.74;
        link-url = true;
      };
    };
  };
}
