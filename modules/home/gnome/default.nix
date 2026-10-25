{
  config,
  lib,
  hostDef,
  pkgs,
  ...
}: let
  isGenericLinux = !(hostDef.nixos.enable or false);
  loupePackage = config.lib.nixGL.wrap pkgs.loupe;
  # GNOME normally disables User Themes for the unlock-dialog session mode.
  # Re-enabling it after authentication reloads the complete Shell stylesheet
  # and invalidates the full actor tree.  On razy that consumed about 500 ms
  # of CPU and delayed the first desktop frame on every resume.  Snappy has
  # unlock-dialog styling, so keep this data-only extension active while
  # locked and avoid the redundant default-theme -> Snappy transition.
  gnomeShellExtensionsWithPersistentUserTheme = pkgs.gnome-shell-extensions.overrideAttrs (oldAttrs: {
    postInstall =
      (oldAttrs.postInstall or "")
      + ''
        substituteInPlace \
          "$out/share/gnome-shell/extensions/user-theme@gnome-shell-extensions.gcampax.github.com/metadata.json" \
          --replace-fail \
            '"shell-version": [' \
            '"session-modes": ["user", "unlock-dialog"], "shell-version": ['
      '';
  });
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
  # wl-copy/wl-paste through xclip on GNOME, so copying from Claude, Codex or
  # other terminal tools never maps the wl-clipboard helper window that Forge
  # auto-splits around (see wl-shim/). Higher priority than wl-clipboard's own
  # binaries in the profile; elsewhere the shims defer to the real tools.
  wlClipboardGnomeShim = (lib.hiPrio (pkgs.runCommand "wl-clipboard-gnome-shim" {} ''
    mkdir -p $out/bin
    for tool in wl-copy wl-paste; do
      substitute ${./wl-shim}/$tool $out/bin/$tool \
        --replace-fail @wlclipboard@ ${pkgs.wl-clipboard} \
        --replace-fail @xclip@ ${pkgs.xclip} \
        ${"$"}([ $tool = wl-copy ] && echo --replace-fail @python@ ${pkgs.python3}/bin/python3)
      chmod +x $out/bin/$tool
    done
  ''));
in {
  imports = [
    ./dconf.nix
    ./wallpaper.nix
  ];

  # Ubuntu's session bus starts before Nix adds its profile to XDG_DATA_DIRS.
  # Export Loupe's activation service where that already-running bus can find it.
  xdg.dataFile."dbus-1/services/org.gnome.Loupe.service" = lib.mkIf isGenericLinux {
    source = "${loupePackage}/share/dbus-1/services/org.gnome.Loupe.service";
  };

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

  # The tmux server outlives GNOME logins, and mutter writes a fresh Xwayland
  # cookie file each login. Point tmux at the new display and give the cookie
  # files older panes still reference the current key, so long-running agents
  # keep X11 clipboard access (image paste) across a re-login.
  systemd.user.services.display-env-sync = {
    Unit = {
      Description = "Sync the login's display environment into tmux";
      After = ["graphical-session.target"];
      PartOf = ["graphical-session.target"];
    };
    Service = {
      Type = "oneshot";
      ExecStart = toString (pkgs.writeShellScript "display-env-sync" ''
        set -u
        session_env=$(${pkgs.systemd}/bin/systemctl --user show-environment)
        value() { printf '%s\n' "$session_env" | ${pkgs.gnused}/bin/sed -n "s/^$1=//p"; }
        tmux=${pkgs.tmux}/bin/tmux
        for var in DISPLAY XAUTHORITY WAYLAND_DISPLAY; do
          [ -n "$(value "$var")" ] && $tmux set-environment -g "$var" "$(value "$var")" 2>/dev/null
        done
        current=$(value XAUTHORITY)
        [ -f "$current" ] || exit 0
        for stale in "''${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"/.mutter-Xwaylandauth.*; do
          [ -f "$stale" ] && [ "$stale" != "$current" ] || continue
          ${pkgs.xorg.xauth}/bin/xauth -f "$current" nlist | ${pkgs.xorg.xauth}/bin/xauth -f "$stale" nmerge - 2>/dev/null
        done
        exit 0
      '');
    };
    Install = {
      WantedBy = ["graphical-session.target"];
    };
  };

  # GNOME has no wlr-data-control, so wl-copy/wl-paste map a tiny helper
  # window (app_id "wl-clipboard") to take focus; Forge tiled it on every copy
  # or paste, making the layout flash. Forge rewrites windows.json itself, so
  # merge a float rule into it instead of owning the file.
  home.activation.forgeFloatWlClipboard = lib.hm.dag.entryAfter ["writeBoundary"] ''
    run ${pkgs.python3}/bin/python3 - "$HOME/.config/forge/config/windows.json" <<'EOF'
    import json, os, sys
    path = sys.argv[1]
    if not os.path.exists(path):
        sys.exit(0)  # Forge writes its defaults on first start; rerun later.
    with open(path) as f:
        config = json.load(f)
    rule = {"wmClass": "wl-clipboard", "mode": "float"}
    if rule not in config.setdefault("overrides", []):
        config["overrides"].append(rule)
        with open(path + ".tmp", "w") as f:
            json.dump(config, f, indent=4)
        os.replace(path + ".tmp", path)
        print("added")
    EOF
    # Forge caches overrides; bumping this key makes it reload them live.
    key=/org/gnome/shell/extensions/forge/window-overrides-reload-trigger
    count=$(${pkgs.dconf}/bin/dconf read $key | ${pkgs.gawk}/bin/awk '{print $NF}')
    run ${pkgs.dconf}/bin/dconf write $key "uint32 $(( (''${count:-0} + 1) % 1000000 ))"
  '';

  # blur-my-shell pipelines. The defaults stay because the dash uses them; the
  # extra light pipeline frosts the top bar pale in light mode (the default
  # darkens the wallpaper to 0.6, which turned the light bar grey under its
  # dark text). The theme watcher (tmux/theme-watch.sh) switches the panel
  # between pipeline_default and pipeline_panel_light.
  home.activation.blurMyShellPipelines = lib.hm.dag.entryAfter ["dconfSettings"] (let
    blur = id: "<{'type': <'native_static_gaussian_blur'>, 'id': <'${id}'>, 'params': <{'unscaled_radius': <30>, 'brightness': <0.6>}>}>";
    pipelines = "{"
      + "'pipeline_default': {'name': <'Default'>, 'effects': <[${blur "effect_000000000000"}]>}, "
      + "'pipeline_default_rounded': {'name': <'Default rounded'>, 'effects': <[${blur "effect_000000000001"}, "
      + "<{'type': <'corner'>, 'id': <'effect_000000000002'>, 'params': <{'radius': <24>}>}>]>}, "
      + "'pipeline_panel_light': {'name': <'Panel light'>, 'effects': <["
      + "<{'type': <'native_static_gaussian_blur'>, 'id': <'effect_panel_light_blur'>, 'params': <{'unscaled_radius': <30>, 'brightness': <1.0>}>}>, "
      + "<{'type': <'luminosity'>, 'id': <'effect_panel_light_tone'>, 'params': <{'contrast': <0.3>, 'contrast_center': <1.0>, 'saturation_multiplicator': <0.6>}>}>]>}"
      + "}";
  in ''
    run ${pkgs.dconf}/bin/dconf write /org/gnome/shell/extensions/blur-my-shell/pipelines ${lib.escapeShellArg pipelines}
  '');

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
    wlClipboardGnomeShim
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
    loupePackage
    # Nix graphics libraries need the configured nixGL runtime on Ubuntu.
    # The wrapper also updates mpv.desktop, so xdg-open uses the same runtime.
    (config.lib.nixGL.wrap mpv)
  ]
  ++ lib.optionals (!isGenericLinux) [
    gnomeShellExtensionsWithPersistentUserTheme
  ];
}
