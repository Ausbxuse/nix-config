# TLP-based power management for laptops.
#
# Use this instead of auto-cpufreq when you need fine-grained AC/battery
# tuning.  Importing this module disables auto-cpufreq and
# power-profiles-daemon to avoid conflicts.
{lib, ...}: {
  # mkForce needed: GNOME and qol.nix both set these; TLP conflicts with both.
  services.power-profiles-daemon.enable = lib.mkForce false;
  services.auto-cpufreq.enable = lib.mkForce false;

  services.thermald.enable = true;
  # Avoid stacking PowerTOP auto-tuning on top of TLP. Both touch USB runtime
  # PM, which can cause idle-wake issues with USB receivers.
  powerManagement.powertop.enable = false;

  networking.networkmanager.wifi.powersave = true;
  hardware.bluetooth.powerOnBoot = false;

  services.tlp = {
    enable = true;
    settings = {
      CPU_SCALING_GOVERNOR_ON_AC = "performance";
      CPU_SCALING_GOVERNOR_ON_BAT = "powersave";
      CPU_ENERGY_PERF_POLICY_ON_AC = "balance_performance";
      CPU_ENERGY_PERF_POLICY_ON_BAT = "balance_power";
      CPU_BOOST_ON_AC = 1;
      CPU_BOOST_ON_BAT = 0;
      PLATFORM_PROFILE_ON_AC = "balanced";
      PLATFORM_PROFILE_ON_BAT = "low-power";

      RUNTIME_PM_ON_AC = "on";
      RUNTIME_PM_ON_BAT = "auto";
      PCIE_ASPM_ON_AC = "default";
      PCIE_ASPM_ON_BAT = "powersupersave";

      WIFI_PWR_ON_AC = "off";
      WIFI_PWR_ON_BAT = "on";
      USB_AUTOSUSPEND = 1;
      # Logitech receiver for the PRO X mouse. Leaving it on autosuspend causes
      # the first movement after idle to wake the receiver before the pointer
      # starts moving.
      USB_DENYLIST = "046d:c547";
      SOUND_POWER_SAVE_ON_BAT = 1;
    };
  };
}
