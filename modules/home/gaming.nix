# https://github.com/fufexan/dotfiles/blob/483680e/system/programs/steam.nix
{pkgs, ...}: let
  javaRuntime = jdk: pkgs.callPackage ../../pkgs/java-runtime.nix {inherit jdk;};
  manageSteamXwaylandKeyRepeat = pkgs.writeShellApplication {
    name = "manage-steam-xwayland-key-repeat";
    runtimeInputs = with pkgs; [
      coreutils
      procps
      xset
    ];
    text = ''
      repeat_disabled=false

      restore_repeat() {
        if "$repeat_disabled"; then
          xset r on || true
        fi
      }
      trap restore_repeat EXIT INT TERM

      while true; do
        if pgrep --uid "$(id -u)" --full '[/]reaper SteamLaunch AppId=[0-9]+' >/dev/null; then
          if ! "$repeat_disabled" && xset r off; then
            repeat_disabled=true
          fi
        elif "$repeat_disabled"; then
          xset r on || true
          repeat_disabled=false
        fi
        sleep 0.25
      done
    '';
  };
in {
  imports = [
    ./minecraft
  ];

  # Proton games run through XWayland. Disable its synthetic repeat events
  # while a game is active so a delayed key release cannot queue movement
  # inputs, then restore repeat when the game exits.
  systemd.user.services.manage-steam-xwayland-key-repeat = {
    Unit = {
      Description = "Manage XWayland key repeat for Steam games";
      After = ["graphical-session.target"];
      PartOf = ["graphical-session.target"];
    };
    Service = {
      ExecStart = "${manageSteamXwaylandKeyRepeat}/bin/manage-steam-xwayland-key-repeat";
      Restart = "always";
      RestartSec = 1;
    };
    Install.WantedBy = ["graphical-session.target"];
  };

  home.packages = with pkgs; [
    # osu-lazer-bin
    gamescope # SteamOS session compositing window manager
    (prismlauncher.override {
      # Keep every supported Java version without bundling SDK module archives.
      jdks = [
        (javaRuntime jdk25)
        (javaRuntime jdk21)
        (javaRuntime jdk17)
        jre8
      ];
    })
    winetricks # A script to install DLLs needed to work around problems in Wine
  ];
}
