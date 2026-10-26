# https://github.com/fufexan/dotfiles/blob/483680e/system/programs/steam.nix
{pkgs, ...}: let
  javaRuntime = jdk: pkgs.callPackage ../../pkgs/java-runtime.nix {inherit jdk;};
  manageSteamXwaylandKeyRepeat = pkgs.writeShellApplication {
    name = "manage-steam-xwayland-key-repeat";
    runtimeInputs = with pkgs; [
      coreutils
      inotify-tools
      procps
      xset
    ];
    text = ''
      steam_dir="$HOME/.steam"
      steam_pid_file="$steam_dir/steam.pid"
      uid="$(id -u)"
      repeat_disabled=false

      restore_repeat() {
        if "$repeat_disabled"; then
          timeout 2 xset r on || true
          repeat_disabled=false
        fi
      }
      trap restore_repeat EXIT
      trap 'exit 0' INT TERM

      # Bash handles signals immediately while its wait builtin is running.
      # Waiting on a foreground inotifywait instead can defer the exit trap.
      run_interruptibly() {
        "$@" &
        wait "$!"
      }

      live_steam_pid() {
        local comm pid

        [[ -r "$steam_pid_file" ]] || return 1
        IFS= read -r pid < "$steam_pid_file"
        [[ "$pid" =~ ^[0-9]+$ && -r "/proc/$pid/comm" ]] || return 1
        IFS= read -r comm < "/proc/$pid/comm"
        [[ "$comm" = steam ]] || return 1
        printf '%s\n' "$pid"
      }

      monitor_steam() {
        local comm pid="$1"

        while [[ -r "/proc/$pid/comm" ]]; do
          IFS= read -r comm < "/proc/$pid/comm" || break
          [[ "$comm" = steam ]] || break

          if pgrep --uid "$uid" --full '[/]reaper SteamLaunch AppId=[0-9]+' >/dev/null; then
            if ! "$repeat_disabled" && xset r off; then
              repeat_disabled=true
            fi
          elif "$repeat_disabled"; then
            restore_repeat
          fi
          run_interruptibly sleep 1
        done

        restore_repeat
      }

      while true; do
        if pid="$(live_steam_pid)"; then
          monitor_steam "$pid"
          continue
        fi

        # Steam leaves a stale steam.pid behind when it exits. Sleep in the
        # kernel until it creates or rewrites that file instead of spawning
        # pgrep four times per second for the entire graphical session.
        if [[ -d "$steam_dir" ]]; then
          run_interruptibly inotifywait --quiet \
            --event close_write,create,moved_to \
            "$steam_dir" >/dev/null || run_interruptibly sleep 1
        else
          run_interruptibly sleep 30
        fi
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
      TimeoutStopSec = 5;
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
