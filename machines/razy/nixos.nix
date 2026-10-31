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

  # Patched daemons go to their service options, not an overlay: overlaying
  # networkmanager changes libnm and forces most of GNOME to build from source.
  # NetworkManager normally walks active devices through an asynchronous
  # DEACTIVATING -> DISCONNECTED -> UNMANAGED sequence before suspend. On
  # this Wi-Fi device that sequence crosses the sleep boundary: it resumes,
  # associates once, then the stale pre-sleep teardown disconnects it again.
  # Complete the suspend-only transition synchronously instead, then retire
  # NetworkManager's delay inhibitor when the SLEEP signal handler returns.
  # The power monitor moves any outstanding handles to its stale list, so a
  # late release remains safe without holding logind for its full five-second
  # timeout. The normal user-requested networking-off path is unchanged.
  razyNetworkManager = pkgs.networkmanager.overrideAttrs (oldAttrs: {
    postPatch =
      (oldAttrs.postPatch or "")
      + ''
        substituteInPlace src/core/nm-manager.c \
          --replace-fail \
            '    nm_device_notify_sleeping(device);' \
            '    nm_device_notify_sleeping(device);

if (suspending) {
    nm_device_set_unmanaged_by_flags(device,
                                     NM_UNMANAGED_MANAGER_DISABLED,
                                     NM_UNMAN_FLAG_OP_SET_UNMANAGED,
                                     reason);
    return;
}'

        substituteInPlace src/core/nm-power-monitor.c \
          --replace-fail \
            '        drop_inhibitor(self, FALSE);' \
            '        drop_inhibitor(self, TRUE);'
      '';
  });

  # nsncd deliberately exits when every worker is stuck, allowing systemd
  # to replace the unhealthy proxy. Upstream then joins those same blocked
  # workers before exiting; after resume that kept NSS unavailable for about
  # 4.2 seconds. Preserve the fail-fast design and let process exit terminate
  # the stale worker threads so Restart=always can restore NSS immediately.
  razyNsncd = pkgs.nsncd.overrideAttrs (oldAttrs: {
    postPatch =
      (oldAttrs.postPatch or "")
      + ''
        substituteInPlace src/main.rs \
          --replace-fail \
            '            let _ = handle.join();' \
            '            drop(handle);'
      '';
  });
in {
  imports = [
    ../../modules/nixos/hardware/tlp-laptop.nix
    ../../modules/nixos/initrd-unlock-deadline.nix
    ../../modules/nixos/hardware/rotate-sensor.nix
    ../../modules/nixos/ollama-agent.nix
    ../../modules/nixos/build-client.nix
  ];

  networking.networkmanager.package = razyNetworkManager;
  services.nscd.package = razyNsncd;

  nixpkgs.overlays = [
    (_final: prev: {
      # auto-cpufreq collects fan RPM only for its status output. Its direct
      # psutil reads bypass libsensors and repeatedly invoke the two broken BIOS
      # fan-status methods, so omit that optional telemetry on this machine.
      auto-cpufreq = prev.auto-cpufreq.overrideAttrs (oldAttrs: {
        postPatch =
          (oldAttrs.postPatch or "")
          + ''
            substituteInPlace auto_cpufreq/core.py \
              --replace-fail \
                'current_fans = list(psutil.sensors_fans())' \
                'current_fans = []'
            substituteInPlace auto_cpufreq/modules/system_info.py \
              --replace-fail \
                'fans = psutil.sensors_fans()' \
                'fans = {}'
          '';
      });

      # GNOME Shell keeps final PAM messages visible for at least two seconds
      # and blocks the successful unlock transition until that queue drains.
      # Preserve the delay for authentication errors, but discard stale
      # messages once authentication has already succeeded.
      gnome-shell = prev.gnome-shell.overrideAttrs (oldAttrs: {
        postPatch =
          (oldAttrs.postPatch or "")
          + ''
            substituteInPlace js/gdm/authPrompt.js \
              --replace-fail \
                '    finish(onComplete) {' \
                '    finish(onComplete) {
                    // PAM messages normally remain queued for at least two seconds. Once
                    // authentication has succeeded, do not hold the unlocked desktop
                    // behind a message that is no longer actionable.
                    if (this.verificationStatus === AuthPromptStatus.VERIFICATION_SUCCEEDED)
                        this._userVerifier.finishMessageQueue();
            '
          '';
      });
    })
  ];

  # alsa-ucm-conf 1.2.16.1 recognizes this combined SoundWire layout but
  # omits the initializer it tries to import, so WirePlumber falls back to
  # the jack PCM and exposes no microphone. Remove this override once the
  # combined initializer is available upstream.
  environment.sessionVariables.ALSA_CONFIG_UCM2 = razyAlsaUcmDir;
  systemd.user.services.wireplumber.environment.ALSA_CONFIG_UCM2 = razyAlsaUcmDir;

  services.xserver.videoDrivers = ["modesetting" "nvidia"];

  # Make the internal panel available before requesting the LUKS password.
  # A text prompt also avoids animating the firmware framebuffer throughout
  # an unattended encrypted boot, before normal laptop power policy starts.
  boot.initrd.kernelModules = ["xe"];
  boot.plymouth.enable = lib.mkForce false;

  # Windows selected through GRUB must remain the target of its update reboots.
  boot.loader.grub.default = "saved";

  # Tailscale reconnects as networking becomes available. Waiting for Wi-Fi
  # here previously added a full minute to boots without a usable connection.
  systemd.services.NetworkManager-wait-online.enable = lib.mkForce false;

  # BIOS 4.01 still exposes two INTC10D6 fan-status devices whose _FST
  # methods call the missing HEC.RCFS method.  Keep the devices bound for
  # firmware/kernel thermal control, but prevent libsensors clients (notably
  # the tmux CPU status helper) from reading the broken fan1_input attributes.
  environment.etc."sensors.d/razy-broken-acpi-fans.conf".text = ''
    chip "acpi_fan-isa-*"
        ignore fan1
  '';
  # nsncd workers performing NSS lookups can remain blocked across a long
  # suspend. PAM, logrotate, fwupd and NetworkManager then all wait on the same
  # stale worker pool for several seconds after resume. Replace the pool in a
  # system-sleep post hook, before systemd thaws the user slice. The nsncd
  # overlay above makes stopping a wedged instance non-blocking.
  environment.etc."systemd/system-sleep/razy-refresh-nsncd".source =
    pkgs.writeShellScript "razy-refresh-nsncd-after-resume" ''
      if [ "$1" = post ]; then
        ${pkgs.systemd}/bin/systemctl restart nscd.service
      fi
    '';
  # Firmware fan-status methods repeatedly reference missing RCFS. Keep these
  # errors in the journal without printing them during the resume VT handoff.
  # Critical kernel messages still reach the console; this is cosmetic only.
  boot.consoleLogLevel = 3;
  boot.kernelParams = [
    # # This platform can hang before resume from s2idle. Prefer S3/deep sleep.
    # "mem_sleep_default=deep"
    # Panther Lake Panel Replay can leave Samsung OLED eDP panels black or
    # visually stale while the rest of resume has already completed. Disable
    # only Panel Replay; ordinary PSR remains available for idle display power
    # savings, unlike the broader xe.enable_psr=0 workaround.
    "xe.enable_panel_replay=0"
    # Hide the blinking VT cursor during the resume handoff back to GNOME.
    "xe.enable_dpcd_backlight=1"
    "vt.global_cursor_default=0"
  ];
  # Preserve all VRAM across sleep, including allocations from offloaded apps
  # and the docked GNOME renderer. Without preservation, resume produced Xid 13
  # shader faults and display flip timeouts. Use the open driver's kernel
  # notifier while retaining fine-grained runtime power management on battery.
  hardware.nvidia.package = lib.mkForce config.boot.kernelPackages.nvidiaPackages.latest;
  hardware.nvidia.powerManagement.enable = lib.mkForce true;
  hardware.nvidia.powerManagement.finegrained = lib.mkForce true;
  hardware.nvidia.powerManagement.kernelSuspendNotifier = lib.mkForce true;
  # Use the encrypted disk for the potentially large temporary VRAM snapshot.
  hardware.nvidia.moduleParams.nvidia.NVreg_TemporaryFilePath = "/var/tmp";
  hardware.nvidia.dynamicBoost.enable = true;

  # thermald 2.5.12 restricts Panther Lake model 0xcc to adaptive mode,
  # but this firmware exposes no INT3400 adaptive data vault. Do not bypass
  # that safety check into thermald's unsupported generic engine. The laptop
  # keeps its firmware/EC controls, kernel thermal zones, intel_pstate, and TLP.
  services.thermald.enable = lib.mkForce false;

  # Suspend profiling shows that tlp-sleep spends about six seconds saving
  # rfkill/drive-bay state and applying AHCI settings. This machine has only
  # NVMe storage, no drive bay, and no WWAN, so let the kernel suspend those
  # devices directly. The main TLP service remains enabled and continues to
  # apply the normal AC/battery profile while the machine is awake.
  systemd.services.tlp-sleep.wantedBy = lib.mkForce [];

  # powerManagement has no commands configured on this host, but its empty
  # sleep-actions shell was delayed alongside tlp-sleep on every battery
  # suspend. Do not put an empty unit on the sleep transaction's critical path.
  systemd.services.sleep-actions.wantedBy = lib.mkForce [];

  # A calendar timer missed during suspend fires as soon as the machine wakes.
  # updatedb then scanned the home files for 22 seconds alongside GNOME's unlock
  # path. Count only awake time instead: check once the machine has been usable
  # for 30 minutes, then after each further day of accumulated awake time.
  services.locate.interval = lib.mkForce "never";
  systemd.timers.update-locatedb = {
    description = "Update locate database during established awake time";
    wantedBy = ["timers.target"];
    timerConfig = {
      OnActiveSec = "30min";
      OnUnitInactiveSec = "1d";
      AccuracySec = "15min";
      Persistent = false;
    };
  };
  systemd.services.update-locatedb.serviceConfig = {
    CPUSchedulingPolicy = "idle";
    ExecCondition = pkgs.writeShellScript "razy-locatedb-update-due" ''
      database=/var/cache/locatedb

      ${pkgs.systemd}/bin/systemd-ac-power || exit 1
      if [ -e "$database" ] \
        && ${pkgs.findutils}/bin/find "$database" -mmin -1440 -print -quit \
          | ${pkgs.gnugrep}/bin/grep -q .; then
        exit 1
      fi
    '';
  };

  # The weekly Btrfs calendar timer has the same resume catch-up behavior. Poll
  # for due work using awake time, then consult a persistent success marker so
  # reboots do not cause extra scrubs. Run the multi-terabyte scan only on AC;
  # a skipped battery check is retried after another day of accumulated uptime.
  systemd.timers."btrfs-scrub--".timerConfig = {
    OnCalendar = lib.mkForce [];
    OnActiveSec = "30min";
    OnUnitInactiveSec = "1d";
    AccuracySec = lib.mkForce "15min";
    Persistent = lib.mkForce false;
  };
  systemd.services."btrfs-scrub--".serviceConfig = {
    CPUSchedulingPolicy = "idle";
    ExecCondition = pkgs.writeShellScript "razy-btrfs-scrub-due" ''
      stamp=/var/lib/razy-maintenance/btrfs-scrub-root.last-success

      ${pkgs.systemd}/bin/systemd-ac-power || exit 1
      if [ -e "$stamp" ] \
        && ${pkgs.findutils}/bin/find "$stamp" -mmin -10080 -print -quit \
          | ${pkgs.gnugrep}/bin/grep -q .; then
        exit 1
      fi
    '';
    ExecStopPost = pkgs.writeShellScript "razy-record-btrfs-scrub" ''
      stamp=/var/lib/razy-maintenance/btrfs-scrub-root.last-success

      if [ "$SERVICE_RESULT" = success ] \
        && ${pkgs.btrfs-progs}/bin/btrfs scrub status / \
          | ${pkgs.gnugrep}/bin/grep -q '^Status:[[:space:]]*finished$'; then
        ${pkgs.coreutils}/bin/touch "$stamp"
      fi
      exit 0
    '';
  };

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

  # Battery-friendly profile: render on Intel, use NVIDIA on demand.
  hardware.nvidia.prime = {
    sync.enable = lib.mkForce false;
    offload.enable = lib.mkForce true;
    offload.enableOffloadCmd = lib.mkForce true;
  };

  # Select at boot from the grub menu.
  specialisation.docked.configuration = {
    # Prefer NVIDIA only in the docked profile to avoid cross-GPU copies and
    # cursor lag on HDMI. The base profile leaves the Intel renderer preferred.
    services.udev.extraRules = ''
      SUBSYSTEM=="drm", ENV{DEVTYPE}=="drm_minor", ENV{DEVNAME}=="/dev/dri/card[0-9]", SUBSYSTEMS=="pci", ATTRS{vendor}=="0x10de", TAG+="mutter-device-preferred-primary"
    '';
  };

  hardware.firmware = with pkgs; [
    linux-firmware
    sof-firmware
  ];

  systemd.tmpfiles.rules = [
    "d /var/lib/razy-maintenance 0755 root root -"
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
