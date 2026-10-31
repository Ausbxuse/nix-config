# Always-on laptop server: never sleeps, unlocks LUKS through the TPM, keeps an
# initrd SSH rescue path, and exports fan/temperature/mic-loudness metrics.
#
# One-time setup per host (see docs/server-hosts.md):
#   sudo install -d -m 700 /etc/secrets/initrd
#   sudo ssh-keygen -t ed25519 -N "" -f /etc/secrets/initrd/ssh_host_ed25519_key
#   sudo systemd-cryptenroll --tpm2-device=auto --tpm2-pcrs=7 /dev/disk/by-partlabel/disk-main-luks
{
  config,
  lib,
  pkgs,
  hostname,
  username,
  adminAccess,
  ...
}: let
  cfg = config.my.server;
  micDir = "/var/lib/server-mic";
  micSample = pkgs.writers.writePython3 "server-mic-sample" {} ''
    import array
    import math
    import os
    import subprocess
    import sys
    import time


    def auto_device():
        # /proc/asound/pcm: "01-10: DMIC Raw : DMIC Raw : capture 1".
        # Prefer the built-in mic array; card 0 is often HDMI-only.
        found = []
        with open("/proc/asound/pcm") as f:
            for line in f:
                if "capture" in line:
                    card, dev = line.split(":")[0].split("-")
                    found.append((card, dev, "DMIC" in line))
        if not found:
            sys.exit("no ALSA capture device")
        card, dev, _ = max(found, key=lambda d: d[2])
        return f"plughw:{int(card)},{int(dev)}"


    device, out = sys.argv[1], sys.argv[2]
    if device == "auto":
        device = auto_device()
    rate, seconds = 16000, 2
    proc = subprocess.run(
        ["arecord", "-q", "-D", device, "-f", "S16_LE", "-c", "1",
         "-r", str(rate), "-d", str(seconds), "-t", "raw"],
        capture_output=True,
    )
    samples = array.array("h")
    samples.frombytes(proc.stdout[:len(proc.stdout) // 2 * 2])

    lines = [f"server_mic_up {int(proc.returncode == 0 and len(samples) > 0)}"]
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode(errors="replace"))
    if samples:
        rms = math.sqrt(sum(s * s for s in samples) / len(samples)) / 32768
        peak = max(abs(s) for s in samples) / 32768
        lines += [
            f"server_mic_rms_dbfs {20 * math.log10(max(rms, 1e-10)):.2f}",
            f"server_mic_peak_dbfs {20 * math.log10(max(peak, 1e-10)):.2f}",
        ]
    lines.append(f"server_mic_last_sample_timestamp_seconds {time.time():.0f}")

    tmp = out + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, out)
  '';
  serverStatus = pkgs.writeShellApplication {
    name = "server-status";
    runtimeInputs = [pkgs.lm_sensors pkgs.coreutils pkgs.gnugrep];
    text = ''
      sensors
      echo "Microphone (dBFS, 0 = clipping, about -90 = silence):"
      grep -v '^#' ${micDir}/mic.prom 2>/dev/null || echo "  no sample yet"
    '';
  };
in {
  options.my.server = {
    micDevice = lib.mkOption {
      type = lib.types.str;
      default = "auto";
      description = "ALSA capture device sampled for ambient loudness (`arecord -L`), or `auto` for the built-in mic.";
    };
    batteryChargeLimit = lib.mkOption {
      type = lib.types.ints.between 20 100;
      default = 60;
      description = "Charge level the battery stops at (charge_control_end_threshold); ASUS calls 60 its maximum-lifespan mode.";
    };
    initrdNetworkModules = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      # Common onboard and USB ethernet adapters; initrd Wi-Fi is not supported.
      default = ["r8169" "e1000e" "igc" "r8152" "ax88179_178a" "cdc_ether" "cdc_ncm" "usbnet"];
      description = "Kernel modules loaded in the initrd so the SSH rescue path has a network.";
    };
  };

  config = {
    # Keep this server reachable even with the lid closed or on battery.
    services.logind.settings.Login = {
      HandleLidSwitch = "ignore";
      HandleLidSwitchExternalPower = "ignore";
      HandleLidSwitchDocked = "ignore";
      HandleSuspendKey = "ignore";
      HandleHibernateKey = "ignore";
      IdleAction = "ignore";
    };

    systemd.sleep.settings.Sleep = {
      AllowSuspend = false;
      AllowHibernation = false;
      AllowHybridSleep = false;
      AllowSuspendThenHibernate = false;
    };

    # Plugged in around the clock: hold the battery at a storage charge
    # instead of 100 %. The desktop's `duo bat-limit` only ran at login,
    # which a headless server never reaches. Writing the threshold raises a
    # change event, so only write when it differs or the rule feeds itself.
    services.udev.extraRules = let
      limit = toString cfg.batteryChargeLimit;
    in ''
      ACTION=="add|change", SUBSYSTEM=="power_supply", KERNEL=="BAT*", ATTR{charge_control_end_threshold}!="${limit}", ATTR{charge_control_end_threshold}="${limit}"
    '';

    # Wi-Fi power save delays packets to an idle station, which shows up as
    # laggy SSH and tailnet replies.
    networking.networkmanager.wifi.powersave = false;

    # GDM suspends the login screen after 20 minutes by default.
    services.displayManager.gdm.autoSuspend = false;

    home-manager.users.${username}.dconf.settings."org/gnome/settings-daemon/plugins/power" = {
      sleep-inactive-ac-type = lib.mkForce "nothing";
      sleep-inactive-battery-type = lib.mkForce "nothing";
    };

    # Keep tailnet access available after reboot without a temporary Nix shell.
    services.tailscale = {
      enable = true;
      openFirewall = true;
      # Self-hosted headscale. Applied by tailscaled-autoconnect once an
      # authKeyFile is set; until then run `tailscale up` with this flag once.
      extraUpFlags = ["--login-server=https://vpn.zhenyuzhao.com"];
    };

    # Unattended unlock: systemd-cryptsetup tries the enrolled TPM2 token first
    # and falls back to the passphrase prompt (or initrd SSH) when it refuses.
    security.tpm2.enable = true;
    boot.initrd.systemd.tpm2.enable = true;
    boot.initrd.luks.devices.crypted.crypttabExtraOpts = ["tpm2-device=auto"];

    # Rescue path when the TPM refuses (firmware/BIOS update): wired DHCP, then
    # `ssh -p 2222 root@<host>-initrd` lands straight in the passphrase prompt.
    boot.initrd.availableKernelModules = cfg.initrdNetworkModules;
    boot.initrd.network = {
      enable = true;
      ssh = {
        enable = true;
        port = 2222;
        authorizedKeys = adminAccess.authorizedKeys or [];
        hostKeys = ["/etc/secrets/initrd/ssh_host_ed25519_key"];
      };
    };
    boot.initrd.systemd.network = {
      enable = true;
      networks."10-wired" = {
        matchConfig.Type = "ether";
        networkConfig.DHCP = "yes";
        dhcpV4Config.Hostname = "${hostname}-initrd";
      };
    };
    boot.initrd.systemd.users.root.shell = "/bin/systemd-tty-ask-password-agent";

    # The bootloader install copies this key into the initrd secrets and fails
    # if it is missing. Activation also runs inside nixos-install before the
    # bootloader, so new hosts get one; an existing host moving onto this
    # module via `boot` still needs the one-time ssh-keygen above.
    system.activationScripts.initrdSshHostKey.text = ''
      if [ ! -f /etc/secrets/initrd/ssh_host_ed25519_key ]; then
        install -d -m 0700 /etc/secrets/initrd
        ${pkgs.openssh}/bin/ssh-keygen -q -t ed25519 -N "" -C "${hostname}-initrd" \
          -f /etc/secrets/initrd/ssh_host_ed25519_key
      fi
    '';

    # Fans and temperatures (hwmon, thermal_zone) plus the mic textfile metrics,
    # reachable only over the tailnet: curl http://<host>:9100/metrics
    services.prometheus.exporters.node = {
      enable = true;
      enabledCollectors = ["hwmon" "thermal_zone" "textfile"];
      extraFlags = ["--collector.textfile.directory=${micDir}"];
    };
    networking.firewall.interfaces.tailscale0.allowedTCPPorts = [config.services.prometheus.exporters.node.port];

    users.groups.server-mic = {};
    users.users.server-mic = {
      isSystemUser = true;
      group = "server-mic";
      extraGroups = ["audio"];
    };

    systemd.services.server-mic = {
      description = "Sample ambient microphone loudness";
      path = [pkgs.alsa-utils];
      serviceConfig = {
        Type = "oneshot";
        User = "server-mic";
        StateDirectory = "server-mic";
        StateDirectoryMode = "0755";
        ExecStart = "${micSample} ${lib.escapeShellArg cfg.micDevice} ${micDir}/mic.prom";
      };
    };
    systemd.timers.server-mic = {
      wantedBy = ["timers.target"];
      timerConfig = {
        OnBootSec = "1min";
        OnUnitActiveSec = "30s";
        AccuracySec = "1s";
      };
    };

    # Read fan RPM/temperatures, find busy processes, and inspect SSD health.
    environment.systemPackages = with pkgs; [
      serverStatus
      lm_sensors
      btop
      smartmontools
      alsa-utils
    ];
  };
}
