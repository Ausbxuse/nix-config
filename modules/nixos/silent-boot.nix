{
  config,
  lib,
  ...
}: {
  boot.kernelParams = lib.optionals config.boot.plymouth.enable [
    "quiet"
    "splash"
    "vga=current"
    "rd.systemd.show_status=false"
    "rd.udev.log_level=3"
    "udev.log_priority=3"
  ];
  boot.plymouth.enable = true;
  boot.plymouth.theme = "breeze";
  # boot.consoleLogLevel = 0;
  # boot.initrd.verbose = false;
}
