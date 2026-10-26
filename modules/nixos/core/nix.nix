{
  config,
  lib,
  const,
  inputs,
  options,
  pkgs,
  username,
  ...
}: {
  nixpkgs = {
    config = {
      allowUnfree = true;
      allowUnfreePredicate = _: true;
    };
    overlays = [
      (_final: prev: {
        pythonPackagesExtensions =
          prev.pythonPackagesExtensions
          ++ [
            (_pyfinal: pyprev: {
              # reuse 6.2.0's sphinx man builder produces no pages, so the `man`
              # output is never created (pulled in by amberol). Drop it until fixed.
              reuse = pyprev.reuse.overridePythonAttrs (old: {
                outputs = lib.remove "man" old.outputs;
                sphinxBuilders = lib.remove "man" old.sphinxBuilders;
              });
            })
          ];
      })
    ];
  };

  # programs.sqlite only ships in channel tarballs; nix-index (home profile) covers this.
  programs.command-not-found.enable = false;

  programs.nix-ld.enable = true;
  # programs.nix-ld.libraries = with pkgs; [
  #   # Add any missing dynamic libraries for unpackaged programs
  #   # here, NOT in environment.systemPackages
  #   glibc
  #   glib
  #   libGL
  #   zlib
  #   fontconfig
  #   xorg.libX11
  #   libxkbcommon
  #   freetype
  #   dbus
  # ];

  nix.settings = {
    experimental-features = ["nix-command" "flakes"];
    # given the users in this list the right to specify additional substituters via:
    #    1. `nixConfig.substituters` in `flake.nix`
    #    2. command line args `--options substituters http://xxx`
    trusted-users = [username];

    substituters = (const.nix.extraSubstituters or []) ++ [
      "https://cache.nixos.org"
      "https://cache.nixos-cuda.org"
      "https://ausbxuse.cachix.org"
      "https://nix-community.cachix.org"
      # cache mirror located in China
      #"https://mirror.sjtu.edu.cn/nix-channels/store"
      #"https://mirrors.ustc.edu.cn/nix-channels/store"
      #"https://mirrors.tuna.tsinghua.edu.cn/nix-channels/store"
      # "https://cache.nixos.org"
    ];
    trusted-public-keys = (const.nix.extraTrustedPublicKeys or []) ++ [
      "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
      "nix-community.cachix.org-1:mB9FSh9qf2dCimDSUo8Zy7bkq5CX+/rkCWyvRCYg3Fs="
      "cache.nixos-cuda.org:74DUi4Ye579gUqzH4ziL9IyiJBlDpMRn9MBN8oNan9M="
    ];
  };
  nix.gc = {
    automatic = true;
    dates = "weekly";
    options = "--delete-older-than 30d";
  };
  nix.optimise.automatic = true;
  nix.settings.auto-optimise-store = true;
}
