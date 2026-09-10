{
  ...
}: {
  # Keep the prebuilt portable closure bootable when its physical NVMe disk is
  # replaced by a VM disk. Quickemu presents the target through virtio-blk;
  # without these drivers in stage 1, systemd waits forever for the LUKS
  # partition and eventually drops into emergency mode.
  boot.initrd.availableKernelModules = [
    "virtio_pci"
    "virtio_blk"
    "virtio_scsi"
  ];

  imports = [
    ./minimal-gui.nix
    ../../nixos/grub.nix
    ../../nixos/silent-boot.nix
    ../../nixos/vm.nix
    ../../nixos/gui/gaming.nix
  ];
}
