# https://search.nixos.org/options and in the NixOS manual (`nixos-help`).
{
  lib,
  pkgs,
  ...
}: {
  imports = [
    ../../modules/nixos/hardware/tlp-laptop.nix
    ../../modules/nixos/server.nix
  ];

  services.xserver.videoDrivers = ["modesetting"];

  # With the current MUX setting, the internal panel is not reachable through
  # the Intel path alone. Keep a low-power NVIDIA profile instead of an
  # Intel-only profile that boots to a black screen.
  specialisation.nonvidia.configuration = {
    # Avoid GRUB's fallback to the Nix store symlink mtime for specialisations.
    boot.loader.grub.configurationName = lib.mkDefault "nonvidia";

    my.hardware.nvidia.enable = lib.mkForce true;
    services.xserver.videoDrivers = lib.mkForce ["nvidia"];
  };

  environment.systemPackages = with pkgs; [
    sof-firmware
  ];
}
