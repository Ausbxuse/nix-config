{
  config,
  hostDef,
  lib,
  pkgs,
  inputs,
  ...
}: let
  installDef = hostDef.install or {};
in {
  boot.kernelPackages = lib.mkDefault pkgs.linuxPackages_latest;

  boot.loader = {
    systemd-boot.enable = false;
    efi = {
      # Default to a firmware-independent EFI install path. Hosts can opt back
      # into NVRAM boot entry management if they need it.
      canTouchEfiVariables = lib.mkDefault (installDef.canTouchEfiVariables or false);
      efiSysMountPoint = "/boot"; # ← use the same mount point here.
    };
    grub = {
      enable = true;
      efiSupport = true;
      efiInstallAsRemovable = lib.mkDefault (installDef.efiInstallAsRemovable or true);
      # os-prober pulls in dmraid-based probing that is fragile in installer
      # environments and unnecessary for single-OS installs. Opt in per host.
      useOSProber = lib.mkDefault (installDef.useOSProber or false);
      # Keep the EFI partition from filling up with old kernel/initrd copies.
      configurationLimit = 3;
      # enableCryptodisk= true;
      #efiInstallAsRemovable = true; # in case canTouchEfiVariables doesn't work for your system
      device = "nodev";
    };
  };

  # Activation mounts a fresh /run inside nixos-enter. The installer's outer
  # /mnt/run is hidden there, and nixos-enter's tmpfiles -E skips /run.
  # Seed dmraid's lock directory in that namespace before GRUB probes disks.
  system.activationScripts.grubInstallerLocks = lib.mkIf config.boot.loader.grub.useOSProber {
    deps = ["specialfs"];
    text = ''
      if [ "''${IN_NIXOS_ENTER:-}" = 1 ]; then
        install -d -m 0755 /run/lock
        install -d -m 0700 /run/lock/dmraid
      fi
    '';
  };
}
