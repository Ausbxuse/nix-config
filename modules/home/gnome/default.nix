{
  config,
  lib,
  hostDef,
  pkgs,
  ...
}: let
  isGenericLinux = !(hostDef.nixos.enable or false);
  genericLinuxUserExtensions = with pkgs.gnomeExtensions; [
    astra-monitor
    blur-my-shell
    bluetooth-battery-meter
    caffeine
    color-picker
    forge
    gsconnect
    media-controls
    night-light-slider-updated
    no-overview
    status-area-horizontal-spacing
    unite
  ];
  genericLinuxDistroExtensionNames = [
    "drive-menu@gnome-shell-extensions.gcampax.github.com"
    "screenshot-window-sizer@gnome-shell-extensions.gcampax.github.com"
    "user-theme@gnome-shell-extensions.gcampax.github.com"
    "workspace-indicator@gnome-shell-extensions.gcampax.github.com"
  ];
in {
  imports = [
    ./dconf.nix
  ];

  systemd.user.services.gnome-resume-background = {
    Unit = {
      Description = "Refresh GNOME wallpaper after system resume";
      After = ["graphical-session.target"];
      PartOf = ["graphical-session.target"];
    };
    Service = {
      ExecStart = "%h/.local/bin/gnome/refresh-background-on-resume.sh";
      Restart = "always";
      RestartSec = 1;
    };
    Install = {
      WantedBy = ["graphical-session.target"];
    };
  };

  systemd.user.services.tmux-theme-watch = {
    Unit = {
      Description = "Reload tmux theme on GNOME color-scheme changes";
      After = ["graphical-session.target"];
      PartOf = ["graphical-session.target"];
    };
    Service = {
      ExecStart = "%h/.local/bin/tmux/theme-watch.sh";
      Restart = "always";
      RestartSec = 1;
    };
    Install = {
      WantedBy = ["graphical-session.target"];
    };
  };

  systemd.user.services.gnome-monitor-profile = {
    Unit = {
      Description = "Apply GNOME monitor profile";
      After = ["graphical-session.target"];
      PartOf = ["graphical-session.target"];
    };
    Service = {
      Type = "oneshot";
      ExecStart = "%h/.local/bin/gnome/apply-monitor-profile.sh";
    };
    Install = {
      WantedBy = ["graphical-session.target"];
    };
  };

  home.file.".local/bin/tmux/theme-watch.sh" = {
    source = ../tmux/theme-watch.sh;
    executable = true;
  };

  home.file.".local/bin/gnome/refresh-background-on-resume.sh" = {
    text = ''
      #!/usr/bin/env bash
      set -euo pipefail

      wait_for_gnome() {
        local i delay

        delay=0.05
        for i in $(seq 1 8); do
          if ${pkgs.glib}/bin/gdbus call --session \
            --dest org.gnome.Shell \
            --object-path /org/gnome/Shell \
            --method org.freedesktop.DBus.Peer.Ping >/dev/null 2>&1; then
            if ${pkgs.gnome-monitor-config}/bin/gnome-monitor-config list >/dev/null 2>&1; then
              return 0
            fi
          fi
          sleep "$delay"
          delay=$(awk "BEGIN { printf \"%.2f\", $delay * 2 }")
        done
      }

      refresh_background() {
        local bg_uri bg_dark_uri bg_options ss_uri ss_options tmp_options

        # Mutter/GNOME can come back from s2idle with a solid-colour fallback
        # instead of the wallpaper texture. Re-applying the wallpaper settings
        # from the user session forces the background actor to rebuild.
        wait_for_gnome || true

        bg_uri="$(${pkgs.glib}/bin/gsettings get org.gnome.desktop.background picture-uri | tr -d "'")"
        bg_dark_uri="$(${pkgs.glib}/bin/gsettings get org.gnome.desktop.background picture-uri-dark | tr -d "'")"
        bg_options="$(${pkgs.glib}/bin/gsettings get org.gnome.desktop.background picture-options | tr -d "'")"
        ss_uri="$(${pkgs.glib}/bin/gsettings get org.gnome.desktop.screensaver picture-uri | tr -d "'")"
        ss_options="$(${pkgs.glib}/bin/gsettings get org.gnome.desktop.screensaver picture-options | tr -d "'")"

        if [ -z "$bg_uri" ]; then
          return 0
        fi

        tmp_options="centered"
        if [ "$bg_options" = "centered" ]; then
          tmp_options="zoom"
        fi

        ${pkgs.glib}/bin/gsettings set org.gnome.desktop.background picture-options "$tmp_options" >/dev/null 2>&1 || true
        ${pkgs.glib}/bin/gsettings set org.gnome.desktop.background picture-uri "$bg_uri" >/dev/null 2>&1 || true
        if [ -n "$bg_dark_uri" ]; then
          ${pkgs.glib}/bin/gsettings set org.gnome.desktop.background picture-uri-dark "$bg_dark_uri" >/dev/null 2>&1 || true
        fi
        ${pkgs.glib}/bin/gsettings set org.gnome.desktop.background picture-options "$bg_options" >/dev/null 2>&1 || true

        if [ -n "$ss_uri" ]; then
          ${pkgs.glib}/bin/gsettings set org.gnome.desktop.screensaver picture-uri "$ss_uri" >/dev/null 2>&1 || true
        fi
        if [ -n "$ss_options" ]; then
          ${pkgs.glib}/bin/gsettings set org.gnome.desktop.screensaver picture-options "$ss_options" >/dev/null 2>&1 || true
        fi
      }

      refresh_background

      ${pkgs.dbus}/bin/dbus-monitor --system \
        "type='signal',interface='org.freedesktop.login1.Manager',member='PrepareForSleep'" |
        while IFS= read -r line; do
          case "$line" in
            *"boolean false"*)
              refresh_background
              ;;
          esac
        done
    '';
    executable = true;
  };

  home.file.".local/bin/gnome/apply-monitor-profile.sh" = {
    text = ''
      #!/usr/bin/env bash
      set -eu

      # Wait for GNOME display stack to settle after login/resume.
      sleep 2

      GMC='${pkgs.gnome-monitor-config}/bin/gnome-monitor-config'

      monitors="$($GMC list 2>/dev/null || true)"
      if ! printf '%s\n' "$monitors" | grep -Fq 'Monitor [ eDP-1 ] ON'; then
        exit 0
      fi
      if ! printf '%s\n' "$monitors" | grep -Fq 'Monitor [ HDMI-1 ] ON'; then
        exit 0
      fi

      $GMC set --logical-layout-mode \
        -L -M eDP-1 -m '2560x1600@240.000' -x 0 -y 0 -s 2 -t normal \
        -L -M HDMI-1 -m '3840x2160@143.982' -x 1280 -y 0 -s 2 -t normal -p
    '';
    executable = true;
  };

  home.activation.installGnomeShellExtensions = lib.mkIf isGenericLinux (lib.hm.dag.entryAfter ["writeBoundary"] ''
    target_dir="${config.home.homeDirectory}/.local/share/gnome-shell/extensions"

    run mkdir -p "$target_dir"
    ${lib.concatMapStringsSep "\n" (pkg: ''
      for extension in "${pkg}/share/gnome-shell/extensions"/*; do
        [ -e "$extension" ] || continue
        name="$(${pkgs.coreutils}/bin/basename "$extension")"
        if [ -e "$target_dir/$name" ]; then
          run chmod -R u+w "$target_dir/$name"
        fi
        run rm -rf "$target_dir/$name"
        run cp -aL "$extension" "$target_dir/$name"
      done
    '') genericLinuxUserExtensions}

    ${lib.concatMapStringsSep "\n" (name: ''
      if [ -e "$target_dir/${name}" ]; then
        run chmod -R u+w "$target_dir/${name}"
        run rm -rf "$target_dir/${name}"
      fi
    '') genericLinuxDistroExtensionNames}
  '');

  home.packages = with pkgs;
    [
    capitaine-cursors
    # pinentry
    gnome-monitor-config
    networkmanager-openvpn
    # gnome-extension-manager
    gnome-tweaks
    gnomeExtensions.astra-monitor
    # gnomeExtensions.colortint
    gnomeExtensions.media-controls
    # gnomeExtensions.gnome-bedtime
    gnomeExtensions.night-light-slider-updated
    # gnomeExtensions.hide-cursor
    gnomeExtensions.unite
    gnomeExtensions.blur-my-shell
    gnomeExtensions.forge
    gnomeExtensions.gsconnect
    gnomeExtensions.caffeine
    # gnomeExtensions.coverflow-alt-tab
    gnomeExtensions.color-picker
    gnomeExtensions.bluetooth-battery-meter
    # gnomeExtensions.just-perfection
    gnomeExtensions.status-area-horizontal-spacing
    gnomeExtensions.no-overview
    gnomeExtensions.another-window-session-manager
    amberol
    powertop
    xiccd
    moreutils
    loupe
    mpv
  ]
  ++ lib.optionals (!isGenericLinux) [
    gnome-shell-extensions
  ];
}
