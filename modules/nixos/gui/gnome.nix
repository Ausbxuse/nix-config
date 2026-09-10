{
  lib,
  pkgs,
  ...
}: let
  gdmScaling = pkgs.writeTextFile {
    name = "gdm-scaling-dconf";
    destination = "/dconf/gdm-custom";
    text = ''
      [org/gnome/mutter]
      experimental-features=['scale-monitor-framebuffer','xwayland-native-scaling']
    '';
  };

  gdmScalingDb = pkgs.stdenv.mkDerivation {
    name = "gdm-scaling-dconf-db";
    buildCommand = ''
      ${pkgs.dconf}/bin/dconf compile $out ${gdmScaling}/dconf
    '';
  };
in {
  services.xserver.enable = true;
  services.desktopManager.gnome.enable = true;
  # Screen reading is unused on these desktops and on the live installer.
  # Keep client libraries required by applications, without the speech daemon
  # and its MBROLA voice databases.
  services.orca.enable = lib.mkForce false;
  services.speechd.enable = lib.mkForce false;
  services.displayManager.gdm.enable = true;
  services.displayManager.gdm.settings = {
    Theme = {
      cursorTheme = "capitaine-cursors-white";
    };
  };
  services.libinput.enable = true;
  # TODO: separate hyprland
  # programs.hyprland.enable = true;
  # programs.iio-hyprland.enable = true;

  services.gnome.tinysparql.enable = true;
  # services.gnome.tracker.enable = true;
  services.gnome.sushi.enable = true;

  programs.dconf.profiles.gdm = lib.mkForce (pkgs.stdenv.mkDerivation {
    name = "dconf-gdm-profile";
    buildCommand = ''
      if [ "$(head -n 1 ${pkgs.gdm}/share/dconf/profile/gdm)" != "user-db:user" ]; then
        echo "Unexpected GDM dconf profile format"
        exit 1
      fi

      {
        echo "user-db:user"
        echo "file-db:${gdmScalingDb}"
        tail -n +2 ${pkgs.gdm}/share/dconf/profile/gdm
      } > "$out"
    '';
  });

  environment.systemPackages = with pkgs; [
    capitaine-cursors
    gnome-connections
    xsel
  ];
  environment.gnome.excludePackages = with pkgs; [
    mutter
    baobab # disk usage analyzer
    cheese # photo booth
    epiphany # web browser
    simple-scan # document scanner
    totem # video player
    yelp # help viewer
    evince # document viewer
    file-roller # archive manager
    geary # email client
    seahorse # password manager
    gedit # text editor

    # these should be self explanatory
    gnome-calculator
    gnome-calendar
    gnome-characters # input methods have it
    gnome-clocks
    gnome-contacts
    gnome-font-viewer
    gnome-logs
    gnome-maps
    gnome-music
    gnome-photos
    # gnome-screenshot
    gnome-system-monitor
    gnome-weather
    gnome-disk-utility
  ];

  fonts = {
    fontDir.enable = true;
    packages = with pkgs; [
      noto-fonts-emoji-blob-bin
      source-han-sans
      wqy_zenhei # Need by steam for Chinese
    ];
  };

  programs.ssh = {
    startAgent = false; # we want gpg-agent, not ssh-agent
    enableAskPassword = false; # don’t ever set SSH_ASKPASS
    askPassword = "";
  };

  services.interception-tools = {
    enable = true;
    plugins = [
      pkgs.interception-tools-plugins.dual-function-keys
    ];
    udevmonConfig = ''
      - JOB: "${pkgs.interception-tools}/bin/intercept -g $DEVNODE \
               | ${pkgs.interception-tools-plugins.dual-function-keys}/bin/dual-function-keys -c /etc/dual-function-keys.yaml \
               | ${pkgs.interception-tools}/bin/uinput -d $DEVNODE"
        DEVICE:
          EVENTS:
            EV_KEY: [KEY_CAPSLOCK]
    '';
  };

  environment.etc."dual-function-keys.yaml".text = ''
    MAPPINGS:
      - KEY: KEY_CAPSLOCK
        TAP: KEY_ESC
        HOLD: KEY_LEFTCTRL
  '';
}
