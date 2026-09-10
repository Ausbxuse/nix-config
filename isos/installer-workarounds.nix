{lib, ...}: {
  # Keep the live installer focused on storage/network setup. Some recent Intel
  # SOF/SoundWire audio stacks can spam the console or hold udev workers during
  # hardware probing, which can make unrelated installer steps fail at
  # `udevadm settle`.
  boot.kernelParams = lib.mkAfter [
    "quiet"
    "loglevel=1"
    "udev.log_level=3"
    "module_blacklist=snd_sof_pci_intel_tgl,snd_sof_pci_intel_mtl,snd_sof_pci_intel_lnl,snd_sof_pci_intel_ptl,snd_sof_intel_hda_common,snd_sof_intel_hda,snd_soc_sof_sdw,soundwire_intel"
  ];

  boot.blacklistedKernelModules = [
    "snd_sof_pci_intel_tgl"
    "snd_sof_pci_intel_mtl"
    "snd_sof_pci_intel_lnl"
    "snd_sof_pci_intel_ptl"
    "snd_sof_intel_hda_common"
    "snd_sof_intel_hda"
    "snd_soc_sof_sdw"
    "soundwire_intel"
  ];

  # The installer provisions Btrfs, and must not force-import an unrelated ZFS
  # root pool it happens to find on a machine being repaired or reinstalled.
  boot.zfs.forceImportRoot = false;

  # The upstream graphical image eagerly enables every x86 VM guest agent.
  # Hyper-V's forced initrd modules return ENODEV under QEMU, producing the
  # alarming (but otherwise harmless) "Failed to start Load Kernel Modules"
  # banner. Xen's xe-daemon similarly fails when /proc/xen is absent. Quickemu
  # already has its native QEMU guest support, so omit those two foreign guest
  # integrations from this image.
  virtualisation.hypervGuest.enable = lib.mkForce false;
  services.xe-guest-utilities.enable = lib.mkForce false;
}
