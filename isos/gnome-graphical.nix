{
  config,
  const,
  hostDefs,
  inputs,
  lib,
  modulesPath,
  pkgs,
  ...
}: let
  liveUsername = "nixos";
  # Reuse Spacy's public, pre-enrollment Home Manager context while adapting
  # paths and account ownership to the installation media's live user.
  liveHostname = "spacy";
  liveHostDef = hostDefs.spacy // {username = liveUsername;};
  installerFavorites = [
    "install-nixos-auto.desktop"
    "edit-install-config.desktop"
    "install-nixos-interactive.desktop"
    "org.gnome.Terminal.desktop"
    "org.gnome.Nautilus.desktop"
    "gparted.desktop"
  ];
  quotedFavorites = lib.concatMapStringsSep ", " (desktop: "'${desktop}'") installerFavorites;
in {
  imports = [
    "${toString modulesPath}/installer/cd-dvd/installation-cd-graphical-base.nix"
    inputs.home-manager.nixosModules.home-manager
    ../modules/profiles/nixos/gnome-desktop.nix
  ];

  services.desktopManager.gnome = {
    favoriteAppsOverride = lib.mkForce ''
      [org.gnome.shell]
      favorite-apps=[ ${quotedFavorites} ]
    '';
    extraGSettingsOverrides = ''
      [org.gnome.shell]
      welcome-dialog-last-shown-version='9999999999'
      [org.gnome.desktop.session]
      idle-delay=0
      [org.gnome.settings-daemon.plugins.power]
      sleep-inactive-ac-type='nothing'
      sleep-inactive-battery-type='nothing'
    '';
    extraGSettingsOverridePackages = [pkgs.gnome-settings-daemon];
  };

  services.displayManager = {
    autoLogin = {
      enable = true;
      user = liveUsername;
    };
    gdm.autoSuspend = false;
  };

  programs.zsh.enable = true;
  users.users.${liveUsername}.shell = pkgs.zsh;

  home-manager = {
    useGlobalPkgs = true;
    useUserPackages = true;
    extraSpecialArgs = {
      inherit inputs const hostDefs;
      hostname = liveHostname;
      hostDef = liveHostDef;
      username = liveUsername;
      adminAccess = {};
    };

    users.${liveUsername} = {
      config,
      lib,
      ...
    }: {
      imports = [../modules/profiles/home/minimal-gui.nix];

      # Match Spacy's display and user environment, with only the stateful or
      # installer-hostile behavior disabled for an ephemeral live session.
      my.display.profile = "gnome-default";
      services.syncthing.enable = lib.mkForce false;
      home.file."${config.xdg.configHome}/autostart/startup.desktop".enable = lib.mkForce false;

      dconf.settings = {
        "org/gnome/desktop/session".idle-delay = lib.mkForce (lib.hm.gvariant.mkUint32 0);
        "org/gnome/desktop/screensaver" = {
          idle-activation-enabled = lib.mkForce false;
          lock-enabled = lib.mkForce false;
        };
        "org/gnome/settings-daemon/plugins/power" = {
          sleep-inactive-ac-type = lib.mkForce "nothing";
          sleep-inactive-battery-type = lib.mkForce "nothing";
        };
        "org/gnome/shell".favorite-apps = lib.mkForce installerFavorites;
      };
    };
  };

  assertions = [
    {
      assertion = config.services.desktopManager.gnome.enable;
      message = "the graphical installer must keep the shared GNOME desktop enabled";
    }
    {
      assertion = config.services.displayManager.autoLogin.user == liveUsername;
      message = "the graphical installer must auto-login as the Home Manager live user";
    }
    {
      assertion = config.home-manager.users.${liveUsername}.my.display.profile == "gnome-default";
      message = "the graphical installer must use Spacy's gnome-default display profile";
    }
    {
      assertion = !config.home-manager.users.${liveUsername}.services.syncthing.enable;
      message = "the ephemeral graphical installer must not start Syncthing";
    }
    {
      assertion = config.home-manager.users.${liveUsername}.programs.neovim.enable;
      message = "the graphical installer must deploy Spacy's declarative Neovim closure";
    }
    {
      assertion = config.home-manager.users.${liveUsername}.xdg.configFile."nvim".enable;
      message = "the graphical installer must activate the store-backed Neovim configuration";
    }
    {
      assertion = !config.virtualisation.hypervGuest.enable;
      message = "the Quickemu installer must not force Hyper-V modules into its initrd";
    }
    {
      assertion = !config.services.xe-guest-utilities.enable;
      message = "the Quickemu installer must not start Xen guest utilities outside Xen";
    }
    {
      assertion = !config.boot.zfs.forceImportRoot;
      message = "the Btrfs installer must not force-import an unrelated ZFS root pool";
    }
  ];

  environment.etc."nix-config/live-profile.conf".text = ''
    nixos_profile=gnome-desktop
    home_profile=minimal-gui
    display_profile=gnome-default
  '';

  environment.systemPackages = with pkgs; [
    tmux
    git
    rsync
    curl
    wget
    disko
  ];
  # override installation-cd-base and enable wpa and sshd start at boot
  systemd.services.wpa_supplicant.wantedBy = lib.mkForce ["multi-user.target"];
  systemd.services.sshd.wantedBy = lib.mkForce ["multi-user.target"];

  # Match Omarchy's Zstd level; the ISO builder uses 1 MiB Squashfs blocks.
  isoImage.squashfsCompression = "zstd -Xcompression-level 19";
  isoImage.edition = "ncfg-offline";
}
