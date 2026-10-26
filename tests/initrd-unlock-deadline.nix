{pkgs, ...}: {
  name = "initrd-unlock-deadline";

  defaults = {
    imports = [../modules/nixos/initrd-unlock-deadline.nix];
    boot.initrd.systemd.timers.initrd-unlock-deadline.timerConfig.OnActiveSec = "15s";
  };

  nodes = {
    unlocked = {};

    locked = {lib, ...}: {
      boot.initrd.systemd.timers.initrd-unlock-deadline.timerConfig.OnActiveSec = lib.mkForce "5s";
      # Exercise the actual initrd transaction while root cannot mount.
      boot.initrd.systemd.services.hold-root-locked = {
        wantedBy = ["initrd-root-fs.target"];
        before = ["sysroot.mount"];
        unitConfig.DefaultDependencies = false;
        serviceConfig = {
          Type = "oneshot";
          ExecStart = "${pkgs.coreutils}/bin/sleep infinity";
        };
      };
    };
  };

  testScript = ''
    start_all()
    locked.wait_for_console_text("Power off an unattended boot with unavailable root", timeout=60)
    locked.wait_for_shutdown()
    unlocked.wait_for_unit("multi-user.target")
    unlocked.succeed("sleep 20")
    unlocked.succeed("test ! -e /etc/initrd-release")
    unlocked.fail("systemctl is-active --quiet initrd-unlock-deadline.timer")
    unlocked.fail("systemctl is-active --quiet initrd-unlock-deadline.service")
  '';
}
