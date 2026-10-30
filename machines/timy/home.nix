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
