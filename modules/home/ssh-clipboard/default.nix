# Paste images from the desktop into Claude Code running in a remote tmux.
#
# Claude Code reads clipboard images by running `xclip -selection clipboard
# -t image/png -o` on the machine it runs on, which over SSH is the remote with
# an empty clipboard. The desktop serves its clipboard on a user socket, ssh
# forwards that socket to the remote (programs.ssh in modules/nixos/core), and
# the xclip below answers reads from it while the pane is viewed over SSH.
{
  lib,
  pkgs,
  ...
}: let
  serve = pkgs.writeShellScript "ssh-clipboard-serve" ''
    # One connection: a single line naming the target (TARGETS, image/png,
    # text/plain, ...); reply with that clipboard content.
    IFS= read -r target || exit 0
    [[ "$target" =~ ^[A-Za-z0-9._+/=\;-]+$ ]] || exit 0
    exec ${pkgs.coreutils}/bin/timeout 5 ${pkgs.xclip}/bin/xclip -selection clipboard -t "$target" -o 2>/dev/null
  '';

  xclip = lib.hiPrio (pkgs.writeShellScriptBin "xclip" ''
    real=${pkgs.xclip}/bin/xclip
    sock="/run/user/$(id -u)/ssh-clipboard.sock"

    # True when whoever is looking at this pane is connected over SSH.
    viewed_over_ssh() {
      if [[ -n "''${TMUX:-}" ]]; then
        local pid
        pid="$(tmux list-clients -t "''${TMUX_PANE:-}" -F '#{client_activity} #{client_pid}' 2>/dev/null |
          sort -rn | head -n1 | cut -d' ' -f2)"
        [[ -n "$pid" ]] && tr '\0' '\n' <"/proc/$pid/environ" 2>/dev/null | grep -q '^SSH_CONNECTION='
      else
        [[ -n "''${SSH_CONNECTION:-}" ]]
      fi
    }

    selection=primary target=UTF8_STRING out=0
    args=("$@")
    while (($#)); do
      case "$1" in
        -selection|-sel) selection="$2"; shift ;;
        -t|-target) target="$2"; shift ;;
        -o|-out) out=1 ;;
      esac
      shift
    done

    if ((out)) && [[ "$selection" == c* && -S "$sock" ]] && viewed_over_ssh; then
      tmp="$(mktemp)"
      trap 'rm -f "$tmp"' EXIT
      printf '%s\n' "$target" | ${pkgs.socat}/bin/socat -t5 - "UNIX-CONNECT:$sock" >"$tmp" 2>/dev/null
      # Empty means the desktop has no such target; fail like xclip does.
      [[ -s "$tmp" ]] || exit 1
      cat "$tmp"
      exit 0
    fi

    exec "$real" "''${args[@]}"
  '');
in {
  home.packages = [xclip];

  systemd.user.sockets.ssh-clipboard = lib.mkIf pkgs.stdenv.hostPlatform.isLinux {
    Unit.Description = "Desktop clipboard for SSH sessions";
    Socket = {
      ListenStream = "%t/ssh-clipboard-serve.sock";
      SocketMode = "0600";
      Accept = true;
    };
    Install.WantedBy = ["sockets.target"];
  };

  systemd.user.services."ssh-clipboard@" = lib.mkIf pkgs.stdenv.hostPlatform.isLinux {
    Unit.Description = "Serve one desktop clipboard read";
    Service = {
      ExecStart = "${serve}";
      StandardInput = "socket";
      StandardOutput = "socket";
      StandardError = "journal";
    };
  };
}
