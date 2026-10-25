{
  config,
  lib,
  pkgs,
  ...
}: let
  inherit (lib) mkIf mkOption types;

  monitors = config.my.gnome.wallpaperMonitors;

  # Mountain pair from dharmx/walls (a curated r/unixporn collection; image
  # licences there are unspecified, so they are fetched, not vendored), shown
  # through Ghostty's translucent background and frosted by blur-my-shell
  # (see dconf.nix): a peak above clouds for light mode, blue twilight peaks
  # for dark mode.
  walls = "https://raw.githubusercontent.com/dharmx/walls/6bf4d733ebf2b484a37c17d742eb47e5139e6a14";
  sources = {
    "peaks-light.jpg" = pkgs.fetchurl {
      url = "${walls}/mountain/a_mountain_with_snow_and_clouds.jpg";
      hash = "sha256-I/ZV9UoxeGrbRGadYNkhFfa8g2ulSSbS0GeATN3x43M=";
    };
    "peaks-dark.jpg" = pkgs.fetchurl {
      url = "${walls}/mountain/a_snowy_mountain_tops_with_blue_sky.jpg";
      hash = "sha256-zjl0t/8LfXNTIsZtByM1Lczdwmm0tHZs50XPsaniy6I=";
    };
  };

  # The originals are 5-7k px wide; GNOME decodes the whole image into a GPU
  # texture, so ship 4K copies instead.
  mkZoomed = name: src:
    pkgs.runCommand name {nativeBuildInputs = [pkgs.imagemagick];} ''
      magick ${lib.escapeShellArg "${src}"} -resize '3840x>' -quality 92 $out
    '';

  # One canvas covering every monitor, each region filled with its own
  # centre-cropped copy of the photo, for picture-options = 'spanned'. A single
  # zoomed image would give a portrait monitor only a stretched middle strip.
  mkSpanned = name: src: let
    width = lib.foldl' lib.max 0 (map (m: m.x + m.width) monitors);
    height = lib.foldl' lib.max 0 (map (m: m.y + m.height) monitors);
    region = m: let
      size = "${toString m.width}x${toString m.height}";
    in ''\( ${lib.escapeShellArg "${src}"} -resize '${size}^' -gravity center -extent ${size} \) -gravity northwest -geometry +${toString m.x}+${toString m.y} -composite'';
  in
    pkgs.runCommand name {nativeBuildInputs = [pkgs.imagemagick];} ''
      magick -size ${toString width}x${toString height} xc:black \
        ${lib.concatMapStringsSep " \\\n        " region monitors} \
        -quality 92 $out
    '';

  mkWallpaper =
    if monitors == []
    then mkZoomed
    else mkSpanned;
in {
  options.my.gnome.wallpaperMonitors = mkOption {
    type = types.listOf (types.submodule {
      options = {
        x = mkOption {type = types.ints.unsigned;};
        y = mkOption {type = types.ints.unsigned;};
        width = mkOption {type = types.ints.positive;};
        height = mkOption {type = types.ints.positive;};
      };
    });
    default = [];
    example = [
      {
        x = 0;
        y = 0;
        width = 3840;
        height = 2160;
      }
    ];
    description = ''
      Monitor rectangles in GNOME's layout coordinates (physical pixels,
      after rotation). When set, wallpapers are composed per monitor and shown
      spanned; when empty, one image is zoomed onto every monitor.
    '';
  };

  config = {
    home.file =
      lib.mapAttrs' (name: src: lib.nameValuePair ".local/share/wallpapers/${name}" {source = mkWallpaper name src;})
      sources;

    dconf.settings."org/gnome/desktop/background".picture-options = mkIf (monitors != []) (lib.mkForce "spanned");
  };
}
