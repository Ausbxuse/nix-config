# https://search.nixos.org/options and in the NixOS manual (`nixos-help`).
{lib, ...}: {
  imports = [
    ./kernel.nix
    ../../modules/nixos/hardware/rotate-sensor.nix
    ../../modules/nixos/server.nix
  ];

  # Stays at home as a server: a fixed zone instead of guessing it from
  # location, and no printers or Bluetooth devices to talk to.
  services.automatic-timezoned.enable = lib.mkForce false;
  time.timeZone = "America/Los_Angeles";
  services.printing.enable = lib.mkForce false;
  hardware.bluetooth.enable = lib.mkForce false;
}
