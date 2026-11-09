{
  config,
  lib,
  pkgs,
  ...
}: let
  defaultLocalModel = "qwen3.6:27b";
in {
  imports = [
    ../../modules/home/gnome-tweaks.nix
    ../../modules/home/codex-local.nix
    ../../modules/home/ollama-agent.nix
  ];

  my.codexLocal = {
    enable = false;
    localModel = defaultLocalModel;
    models = [defaultLocalModel];
  };

  home.packages = [pkgs.wechat];

  # LESLEY's model (Valkrie on timy): Gemma 4 26B, always loaded. Only on the
  # tailnet address, timy's one way into razy (the Headscale policy). Two
  # conversations cached at once, her window and a call, 96k each; her
  # lesley.env on timy compacts at 88k, under Ollama's cut. contextLength is
  # per conversation (Ollama allocates it times parallel): at 128k each, Gemma,
  # its draft model and the vision encoder left about 140 MB of the 24 GB free,
  # and a picture needs 293 MB, so every picture crashed the runner.
  services.ollama-agent = {
    enable = true;
    host = "100.64.0.1";
    port = 11434;
    contextLength = 98304;
    flashAttention = true;
    kvCacheType = "q8_0";
    parallel = 2;
    maxLoadedModels = 1;
    modelsDir = "${config.home.homeDirectory}/.local/state/ollama/models";
    idleKeepAlive = "-1";
    autoStart = true;
    # (Its preloader loads only when docked; the warm-up below always does.)
    preloadModel = "";
    stopOnBattery = false;
  };

  # At boot, wait for Tailscale to give razy its tailnet address (the only
  # one Ollama listens on; before it, the bind fails), then load Gemma as soon
  # as the server answers, so her first reply after a reboot is quick too.
  systemd.user.services.ollama = let
    cfg = config.services.ollama-agent;
    tailnet = pkgs.writeShellScript "ollama-wait-for-tailnet" ''
      for _ in $(${pkgs.coreutils}/bin/seq 300); do
        ${pkgs.iproute2}/bin/ip -4 -o addr show | ${pkgs.gnugrep}/bin/grep -q " ${cfg.host}/" && exit 0
        ${pkgs.coreutils}/bin/sleep 1
      done
      echo "no ${cfg.host} after 5 minutes" >&2
      exit 1
    '';
    warm = pkgs.writeShellScript "ollama-warm-gemma" ''
      url=http://${cfg.host}:${toString cfg.port}
      for _ in $(${pkgs.coreutils}/bin/seq 30); do
        ${pkgs.coreutils}/bin/sleep 2
        ${pkgs.curl}/bin/curl -s -m 2 "$url/" >/dev/null && break
      done
      exec ${pkgs.curl}/bin/curl -s -m 120 "$url/api/generate" \
        -d '{"model":"gemma4:26b","keep_alive":-1}' >/dev/null
    '';
  in {
    Unit.Description = lib.mkForce "Ollama (Gemma 4 for LESLEY on timy)";
    Service = {
      ExecStartPre = "${tailnet}";
      ExecStartPost = "${warm}";
      TimeoutStartSec = 480;
      RestartSec = lib.mkForce 10;
    };
  };

  # Server mode on AC (see razy-inhibit-lid-suspend-while-docked): never
  # suspend when idle while plugged in; on battery, as everywhere else.
  dconf.settings."org/gnome/settings-daemon/plugins/power".sleep-inactive-ac-type = lib.mkForce "nothing";

  programs.firefox.profiles.betterfox.extraConfig = lib.mkAfter ''
    user_pref("browser.uidensity", 1);
  '';

  # razy's vital signs in Valkrie's drawer, like the other machines'. timy reads those over SSH,
  # but may not reach razy, so razy sends its own to timy's server every 20 seconds: the lines
  # of READ in valkrie's desktop/src/machines.rs (keep the two alike), nothing else.
  systemd.user.services.valkrie-vitals = {
    Unit.Description = "Valkrie: razy's vital signs, sent to timy's drawer";
    Service = {
      ExecStart = pkgs.writeShellScript "valkrie-vitals" ''
        export PATH=${lib.makeBinPath [pkgs.coreutils pkgs.gawk pkgs.curl]}:/run/current-system/sw/bin
        while :; do
          {
            awk '/^MemTotal:/{t=$2} /^MemAvailable:/{a=$2} END{print "M",t,a}' /proc/meminfo
            df -P -B1 "$HOME" | awk 'NR==2{print "D",$2,$4}'
            awk '{print "L",$1,$2,$3}' /proc/loadavg
            for h in /sys/class/hwmon/hwmon*; do
              n=$(cat "$h/name" 2>/dev/null)
              for t in "$h"/temp*_input; do [ -r "$t" ] && echo "T $n $(cat "$t")"; done
            done
            for b in /sys/class/power_supply/BAT*; do [ -r "$b/capacity" ] && echo "B $(cat "$b/capacity") $(cat "$b/status" 2>/dev/null)"; done
            command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi --query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits | sed 's/^/G /'
          } | curl -s -m 10 -o /dev/null --data-binary @- http://100.64.0.4:7878/machines/report || true
          sleep 20
        done
      '';
      Restart = "always";
      RestartSec = 20;
    };
    Install.WantedBy = ["default.target"];
  };

  # What's sent to razy from your phone (Valkrie's Send), into ~/Downloads/Valkrie, a text onto
  # the clipboard too: straight from the phone through Tailscale (Taildrop; razy's operator is you,
  # see nixos.nix), and what waited on timy's Valkrie server while razy was off (its /inbox, asked
  # every 20 seconds). It listens on nothing, and timy can't push here.
  systemd.user.services.valkrie-receive = {
    Unit.Description = "Valkrie: what's sent to razy, into ~/Downloads/Valkrie";
    Service = {
      ExecStart = pkgs.writeShellScript "valkrie-receive" ''
        export PATH=${lib.makeBinPath [pkgs.coreutils pkgs.findutils pkgs.gnugrep pkgs.curl pkgs.jq pkgs.wl-clipboard pkgs.libnotify]}:/run/current-system/sw/bin
        d="$HOME/Downloads/Valkrie"
        hub=http://100.64.0.4:7878
        mkdir -p "$d"
        # A file that's come: a text ("Text from <who> <when>.txt", as the phone and timy name
        # one) onto the clipboard, and a notification either way.
        took() {
          n=$(basename "$1")
          case $n in
            "Text from "*.txt)
              who=''${n#Text from }
              who=''${who%% *}
              for kv in $(systemctl --user show-environment | grep -E '^(WAYLAND_DISPLAY|DISPLAY)='); do export "$kv"; done
              if wl-copy --type 'text/plain;charset=utf-8' < "$1" >/dev/null 2>&1; then
                notify-send -a Valkrie "Text from $who" "Copied: $(head -c 80 "$1")"
              else
                notify-send -a Valkrie "Text from $who" "$(head -c 80 "$1")"
              fi
              ;;
            *) notify-send -a Valkrie "$n from your phone" "Saved to $1" ;;
          esac
        }
        # Straight from the phone: Tailscale holds it until it's collected.
        (
          while :; do
            before=$(ls -A "$d" | sort)
            tailscale file get --wait --conflict=rename "$d" || sleep 10
            comm -13 <(echo "$before") <(ls -A "$d" | sort) | while IFS= read -r n; do took "$d/$n"; done
          done
        ) &
        # Sent through timy while razy was off.
        while :; do
          curl -sf -m 10 "$hub/inbox" | jq -c '.items[]?' | while IFS= read -r it; do
            id=$(jq -r .id <<<"$it")
            if [ "$(jq -r .kind <<<"$it")" = text ]; then
              p="$d/Text from $(jq -r .from <<<"$it") $(date '+%Y-%m-%d %H%M%S').txt"
              jq -j .text <<<"$it" > "$p"
            else
              name=$(jq -r .name <<<"$it")
              p="$d/$name"
              k=2
              case $name in ?*.*) stem=''${name%.*} ext=.''${name##*.} ;; *) stem=$name ext= ;; esac
              while [ -e "$p" ]; do p="$d/$stem ($k)$ext"; k=$((k+1)); done
              curl -sf -m 3600 -o "$p.part" "$hub/inbox/$id" || { rm -f "$p.part"; continue; }
              want=$(jq -r '.sha256 // empty' <<<"$it")
              if [ -n "$want" ] && [ "$(sha256sum < "$p.part" | cut -d' ' -f1)" != "$want" ]; then rm -f "$p.part"; continue; fi
              mv "$p.part" "$p"
            fi
            curl -sf -m 10 -X DELETE "$hub/inbox/$id" >/dev/null
            took "$p"
          done
          sleep 20
        done
      '';
      Restart = "always";
      RestartSec = 20;
    };
    Install.WantedBy = ["default.target"];
  };
}
