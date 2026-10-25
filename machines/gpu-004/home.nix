{lib, ...}: {
  # 27" 4K at ~70 cm: size 10 gives a 2.3 mm x-height (~0.19°), 225 columns.
  programs.ghostty.settings.font-size = lib.mkForce 10;

  # DP-4 (Dell S2725QC) on the left, HDMI-2 (MSI MP273U) rotated right beside it.
  my.gnome.wallpaperMonitors = [
    {
      x = 0;
      y = 0;
      width = 3840;
      height = 2160;
    }
    {
      x = 3840;
      y = 0;
      width = 2160;
      height = 3840;
    }
  ];
}
