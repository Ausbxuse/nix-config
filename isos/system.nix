{
  pkgs,
  lib,
  ...
}: {
  nixpkgs.config.allowUnfree = true;

  nix.settings = {
    experimental-features = ["nix-command" "flakes"];
  };

  i18n.defaultLocale = "en_US.UTF-8";

  hardware.graphics = {
    enable = true;
    enable32Bit = pkgs.stdenv.hostPlatform.isx86_64;
    extraPackages32 = lib.optionals pkgs.stdenv.hostPlatform.isx86_64 (with pkgs; [libvdpau-va-gl libva-vdpau-driver]);
  };

  networking.firewall = {
    enable = false;
  };

  system.stateVersion = "24.05";
}
