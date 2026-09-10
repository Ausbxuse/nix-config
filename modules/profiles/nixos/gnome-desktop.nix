{pkgs, ...}: {
  # Reusable GNOME layer shared by normal minimal-GUI hosts and graphical
  # installation media. Host identity, boot loaders, and disk policy remain in
  # the surrounding profile so the live ISO cannot inherit target-host state.
  imports = [
    ../../nixos/gui/gnome.nix
    ../../nixos/keyd.nix
    ../../nixos/hardware/sound.nix
    ../../nixos/hardware/printing.nix
    ../../nixos/hardware/usb.nix
    ../../nixos/qol.nix
  ];

  environment.systemPackages = with pkgs; [
    openvpn
  ];

  i18n.inputMethod = {
    enable = true;
    type = "ibus";
    ibus.engines = with pkgs.ibus-engines; [
      libpinyin
      # Disabled: typing-booster-unwrapped caused ~25s GNOME login stalls here by
      # delaying IBus readiness, which also delayed org.gnome.Shell.Screencast,
      # xdg-desktop-portal, Ghostty startup, and hid GNOME screen recording.
      # typing-booster-unwrapped
    ];
  };
}
