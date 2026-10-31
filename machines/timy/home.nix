{
  pkgs,
  config,
  lib,
  ...
}: {
  imports = [
    ./dconf.nix
  ];

  home.packages = with pkgs; [
    xournalpp
    wl-clipboard
  ];

  programs.tmux.extraConfig = lib.mkAfter ''
    set -g status-right-length 120
  '';

  # Prefix the shared status line with the Duo screen mode instead of
  # replacing it, so timy keeps the shared segments as they change.
  xdg.configFile = let
    withDuo = theme:
      pkgs.writeText "tmux-theme-${theme}-timy.conf" (
        builtins.replaceStrings
        ["#(~/.local/bin/scripts/statusbar/cpu)"]
        ["#(~/.local/bin/duo status 2>/dev/null)#(~/.local/bin/scripts/statusbar/cpu)"]
        (builtins.readFile ../../modules/home/tmux/theme-${theme}.conf)
      );
  in {
    "tmux/theme-dark.conf".source = lib.mkForce (withDuo "dark");
    "tmux/theme-light.conf".source = lib.mkForce (withDuo "light");
    # The Valkrie units (below) were written by hand before; take them over.
    "systemd/user/valkrie-sync.service".force = true;
    "systemd/user/default.target.wants/valkrie-sync.service".force = true;
    "systemd/user/valkrie-voice-tunnel.service".force = true;
    "systemd/user/default.target.wants/valkrie-voice-tunnel.service".force = true;
  };

  # Valkrie's hub and LESLEY's voice tunnel to gpu-004. The binary stays at
  # ~/.local/state/valkrie/pkg so valkrie's nix/user-install.sh can roll out a
  # new build; the installers leave these units alone once they are links.
  systemd.user.services.valkrie-sync = {
    Unit = {
      Description = "Valkrie sync hub (Tailscale)";
      After = ["network-online.target"];
    };
    Service = {
      ExecStart = "%h/.local/state/valkrie/pkg/bin/valkrie-sync --port 7878";
      # tailscale (who is calling), wl-copy and notify-send (text sent here).
      # TMUX_TMPDIR puts LESLEY's window on the user's own tmux server.
      Environment = [
        "PATH=/run/wrappers/bin:/etc/profiles/per-user/%u/bin:/run/current-system/sw/bin"
        "TMUX_TMPDIR=%t"
      ];
      Restart = "always";
      RestartSec = 10;
    };
    Install.WantedBy = ["default.target"];
  };
  systemd.user.services.valkrie-voice-tunnel = {
    Unit.Description = "LESLEY's voice on zhenyu@100.64.0.8, at localhost:8799";
    Service = {
      ExecStart = "${pkgs.openssh}/bin/ssh -N -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -L 127.0.0.1:8799:127.0.0.1:8799 zhenyu@100.64.0.8";
      Restart = "always";
      RestartSec = 5;
    };
    Install.WantedBy = ["default.target"];
  };

  # nixpkgs.config.allowUnfree = true;

  home.file."${config.home.homeDirectory}/.local/bin/startup" = {
    text = ''
      #!/usr/bin/env bash

      $HOME/.local/bin/duo set-displays
      $HOME/.local/bin/duo watch-displays &>/dev/null 2>&1 &
      $HOME/.local/bin/duo watch-rotation &>/dev/null 2>&1 &
      $HOME/.local/bin/duo watch-backlight &>/dev/null 2>&1 &
      $HOME/.local/bin/duo bat-limit &>/dev/null 2>&1 &

      tmux new-session -Ad -s main

      $HOME/.local/bin/setlight

      dbus-monitor --session "type='signal',interface='org.gnome.ScreenSaver'" |
              while read x; do
                      case "$x" in
                      *"boolean true"*) echo SCREEN_LOCKED ;;
                      *"boolean false"*) $HOME/.local/bin/duo set-displays &>/dev/null 2>&1 ;;
                      esac
              done &
      sct 3700
    '';
    executable = true;
  };
}
