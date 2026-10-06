{pkgs, ...}: let
  zathura = pkgs.symlinkJoin {
    name = "zathura-with-desktop-environment";
    paths = [pkgs.zathura];
    nativeBuildInputs = [pkgs.makeWrapper];
    postBuild = ''
      wrapProgram "$out/bin/zathura" --run '
        # An already-running Yazi or editor may still have the environment
        # from before a desktop client attached to the restored tmux server.
        if [[ -n "''${TMUX_PANE:-}" && -z "''${SSH_CONNECTION:-}" ]] && command -v tmux >/dev/null 2>&1; then
          while IFS= read -r entry; do
            [[ "$entry" == *=* ]] || continue
            name="''${entry%%=*}"
            case "$name" in
              DISPLAY|WAYLAND_DISPLAY|XDG_CURRENT_DESKTOP|XDG_SESSION_DESKTOP|XDG_SESSION_TYPE|XAUTHORITY|DBUS_SESSION_BUS_ADDRESS)
                [[ -n "''${!name:-}" ]] || export "$entry"
                ;;
            esac
          done < <(tmux show-environment -t "$TMUX_PANE" 2>/dev/null)
        fi
      '
    '';
  };
in {
  programs = {
    zathura = {
      enable = true;
      package = zathura;
      extraConfig = builtins.readFile ./zathurarc;
    };
  };
}
