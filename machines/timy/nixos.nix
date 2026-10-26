# https://search.nixos.org/options and in the NixOS manual (`nixos-help`).
{...}: {
  imports = [
    ./kernel.nix
    ../../modules/nixos/hardware/rotate-sensor.nix
    ../../modules/nixos/server.nix
  ];
}
