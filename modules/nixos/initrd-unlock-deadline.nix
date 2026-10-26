{
  config,
  lib,
  ...
}: {
  # Lid handling and the normal power policy are unavailable while root is
  # locked. Keep an unattended boot from leaving the laptop awake indefinitely.
  boot.initrd.systemd.timers.initrd-unlock-deadline = {
    description = "Limit time waiting for the root filesystem";
    wantedBy = ["initrd.target"];
    before = ["initrd-switch-root.target"];
    conflicts = ["initrd-switch-root.target"];
    unitConfig = {
      DefaultDependencies = false;
      # Keep the deadline in emergency mode; cancel it on successful handover.
      IgnoreOnIsolate = true;
    };
    timerConfig = {
      OnActiveSec = lib.mkDefault "3min";
      AccuracySec = "1s";
    };
  };

  boot.initrd.systemd.services.initrd-unlock-deadline = {
    description = "Power off an unattended boot with unavailable root";
    unitConfig = {
      DefaultDependencies = false;
      ConditionPathExists = "/etc/initrd-release";
      ConditionPathIsMountPoint = "!/sysroot";
    };
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${config.boot.initrd.systemd.package}/bin/systemctl --no-block poweroff";
    };
  };

  # Bound the shutdown too if an initrd device or service refuses to stop.
  boot.initrd.systemd.targets.poweroff.unitConfig.JobTimeoutSec = "30s";
}
