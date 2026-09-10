{
  config,
  lib,
  pkgs,
  const,
  ...
}: let
  razyAlsaUcmConf = pkgs.alsa-ucm-conf.overrideAttrs (oldAttrs: {
    postInstall =
      (oldAttrs.postInstall or "")
      + ''
        target="$out/share/alsa/ucm2/codecs/rt721+rt1320/init.conf"
        if [ ! -e "$target" ]; then
          install -Dm444 \
            ${./alsa-ucm-conf/codecs/rt721+rt1320/init.conf} \
            "$target"
        fi
      '';
  });
  razyAlsaUcmDir = "${razyAlsaUcmConf}/share/alsa/ucm2";
in {
  imports = [
    ../../modules/nixos/hardware/tlp-laptop.nix
    ../../modules/nixos/hardware/rotate-sensor.nix
    ../../modules/nixos/ollama-agent.nix
  ];

  # alsa-ucm-conf 1.2.16.1 recognizes this combined SoundWire layout but
  # omits the initializer it tries to import, so WirePlumber falls back to
  # the jack PCM and exposes no microphone. Remove this override once the
  # combined initializer is available upstream.
  environment.sessionVariables.ALSA_CONFIG_UCM2 = razyAlsaUcmDir;
  systemd.user.services.wireplumber.environment.ALSA_CONFIG_UCM2 = razyAlsaUcmDir;

  services.xserver.videoDrivers = ["modesetting" "nvidia"];
  # Firmware fan-status methods repeatedly reference missing RCFS. Keep these
  # errors in the journal without printing them during the resume VT handoff.
  # Critical kernel messages still reach the console; this is cosmetic only.
  boot.consoleLogLevel = 3;
  boot.kernelParams = [
    # # This platform can hang before resume from s2idle. Prefer S3/deep sleep.
    # "mem_sleep_default=deep"
    # Hide the blinking VT cursor during the resume handoff back to GNOME.
    "xe.enable_dpcd_backlight=1"
    "vt.global_cursor_default=0"
  ];
  # hardware.nvidia.prime = {
  #   sync.enable = lib.mkForce true;
  #   offload.enable = lib.mkForce false;
  #   offload.enableOffloadCmd = lib.mkForce false;
  # }; # already set in nvidia.nix

  # Try NVIDIA 595+ suspend notifiers to avoid the old nvidia-sleep.sh path,
  # which switches to VT 63 during suspend/resume. Revert these two lines to
  # go back to the previous driver/service behavior:
  #   hardware.nvidia.package = lib.mkForce config.boot.kernelPackages.nvidiaPackages.new_feature;
  #   hardware.nvidia.powerManagement.kernelSuspendNotifier = lib.mkForce false;
  hardware.nvidia.package = lib.mkForce config.boot.kernelPackages.nvidiaPackages.latest;
  hardware.nvidia.powerManagement.enable = lib.mkForce true;
  hardware.nvidia.powerManagement.finegrained = lib.mkForce true;
  hardware.nvidia.powerManagement.kernelSuspendNotifier = lib.mkForce true;
  hardware.nvidia.dynamicBoost.enable = true;

  # thermald 2.5.12 restricts Panther Lake model 0xcc to adaptive mode,
  # but this firmware exposes no INT3400 adaptive data vault. Do not bypass
  # that safety check into thermald's unsupported generic engine. The laptop
  # keeps its firmware/EC controls, kernel thermal zones, intel_pstate, and TLP.
  services.thermald.enable = lib.mkForce false;

  # Avoid long suspend/resume cycles when a manually-started Ollama model is
  # still resident in NVIDIA VRAM.
  systemd.services.razy-unload-ollama-before-sleep = lib.mkIf config.home-manager.users.${const.username}.services.ollama-agent.enable {
    description = "Unload Ollama GPU models before system sleep";
    before = ["sleep.target"];
    wantedBy = ["sleep.target"];
    path = with pkgs; [
      curl
      jq
    ];
    serviceConfig = {
      Type = "oneshot";
      TimeoutStartSec = "20s";
    };
    script = ''
      set -eu

      host=127.0.0.1:11434

      models="$(
        curl -fsS --max-time 2 "http://$host/api/ps" \
          | jq -r '.models[]?.name' \
          || true
      )"

      if [ -z "$models" ]; then
        exit 0
      fi

      printf '%s\n' "$models" | while IFS= read -r model; do
        [ -n "$model" ] || continue
        jq -nc --arg model "$model" '{model: $model, prompt: "", keep_alive: 0}' \
          | curl -fsS --max-time 15 -X POST "http://$host/api/generate" \
              -H 'Content-Type: application/json' \
              --data-binary @- \
              >/dev/null \
          || true
      done
    '';
  };

  # GNOME 50 can retain its handle-lid-switch inhibitor after undocking,
  # even when Mutter reports HasExternalMonitor=false. Keep lid policy in
  # logind and the root-owned docking service below instead. Desktop apps
  # retain their normal sleep/delay inhibitors, but cannot take over the lid.
  environment.etc."polkit-1/rules.d/05-razy-lid.rules".text = ''
    polkit.addRule(function(action, subject) {
      if (subject.user === "root") return;

      // Polkit also checks permissions which imply lid inhibition. Carry
      // the denial through those checks; direct key handling stays allowed.
      var impliedLidPermission =
        action.lookup("polkit.result") === "no" &&
        ["org.freedesktop.login1.inhibit-handle-power-key",
         "org.freedesktop.login1.inhibit-handle-suspend-key",
         "org.freedesktop.login1.inhibit-handle-reboot-key"].indexOf(action.id) !== -1;
      if (action.id === "org.freedesktop.login1.inhibit-handle-lid-switch" ||
          impliedLidPermission) {
        return polkit.Result.NO;
      }
    });
  '';
  # Only the docking service should suppress lid sleep, including when
  # logind detects a dock or multiple displays while running on battery.
  environment.etc."systemd/logind.conf.d/50-razy-lid.conf".text = ''
    [Login]
    HandleLidSwitch=suspend
    HandleLidSwitchExternalPower=suspend
    HandleLidSwitchDocked=suspend
  '';

  # Keep a closed-lid dock usable, but only while it is actually powered and
  # driving an external display.  The inhibitor is removed immediately when
  # either condition goes away, preserving the normal portable lid behavior.
  systemd.services.razy-inhibit-lid-suspend-while-docked = {
    description = "Inhibit lid suspend while on AC with an external monitor";
    wantedBy = ["multi-user.target"];
    after = ["systemd-logind.service"];
    path = with pkgs; [
      coreutils
      systemd
    ];
    serviceConfig = {
      Type = "simple";
      Restart = "always";
      RestartSec = "5s";
    };
    script = ''
      set -eu

      ac_power_connected() {
        for type in /sys/class/power_supply/*/type; do
          [ -r "$type" ] || continue
          [ "$(cat "$type")" = Mains ] || continue
          online="$(dirname "$type")/online"
          [ -r "$online" ] && [ "$(cat "$online")" = 1 ] && return 0
        done
        return 1
      }

      external_monitor_connected() {
        for status in /sys/class/drm/*/status; do
          [ -r "$status" ] || continue
          connector="$(basename "$(dirname "$status")")"
          case "$connector" in
            *-eDP-*|*-LVDS-*|*-DSI-*) continue ;;
          esac
          [ "$(cat "$status")" = connected ] && return 0
        done
        return 1
      }

      inhibitor_pid=""
      stop_inhibitor() {
        if [ -n "$inhibitor_pid" ]; then
          kill "$inhibitor_pid" 2>/dev/null || true
          wait "$inhibitor_pid" 2>/dev/null || true
          inhibitor_pid=""
        fi
      }
      trap stop_inhibitor EXIT INT TERM

      while true; do
        if ac_power_connected && external_monitor_connected; then
          if [ -z "$inhibitor_pid" ] || ! kill -0 "$inhibitor_pid" 2>/dev/null; then
            inhibitor_pid=""
            systemd-inhibit \
              --what=handle-lid-switch \
              --mode=block \
              --who="razy docked lid policy" \
              --why="AC power and an external monitor are connected" \
              sleep infinity &
            inhibitor_pid=$!
          fi
        else
          stop_inhibitor
        fi
        sleep 2
      done
    '';
  };

  # Force mutter to use the NVIDIA GPU as primary renderer on Wayland.
  # Without this, mutter picks Intel (card0) and does a cross-GPU copy to
  # NVIDIA for HDMI output, causing periodic cursor lag.

  # Battery-friendly profile: offload rendering to iGPU, use dGPU on demand.
  hardware.nvidia.prime = {
    sync.enable = lib.mkForce false;
    offload.enable = lib.mkForce true;
    offload.enableOffloadCmd = lib.mkForce true;
  };

  # Select at boot from the grub menu.
  specialisation.docked.configuration = {
    # # Let mutter pick the default (Intel) primary GPU in offload mode.
    # TODO: make it only do so for external monitor. make internal monitor still rendered by intel

    services.udev.extraRules = ''
      SUBSYSTEM=="drm", ENV{DEVTYPE}=="drm_minor", ENV{DEVNAME}=="/dev/dri/card[0-9]", SUBSYSTEMS=="pci", ATTRS{vendor}=="0x10de", TAG+="mutter-device-preferred-primary"
    '';
  };

  hardware.firmware = with pkgs; [
    linux-firmware
    sof-firmware
  ];

  systemd.tmpfiles.rules = [
    "d /var/lib/gdm/.config 0755 gdm gdm -"
  ];

  system.activationScripts.razy-gdm-monitors = lib.stringAfter ["users" "groups"] ''
    install -d -m 0755 -o gdm -g gdm /var/lib/gdm/.config
    install -m 0644 -o gdm -g gdm ${./gdm-monitors.xml} /var/lib/gdm/.config/monitors.xml
  '';

  hardware.openrazer.enable = true;
  hardware.openrazer.users = [const.username];
  users.users.${const.username}.linger = true;

  networking.firewall.interfaces.tailscale0.allowedUDPPortRanges = [
    {
      from = 60000;
      to = 61000;
    }
  ];

  environment.systemPackages = with pkgs; [
    mosh
    openrazer-daemon
    razergenie
  ];
}
