# Laptop servers (timy, uni)

`modules/nixos/server.nix` turns a laptop into an always-on server: no suspend
or hibernate (lid, keys, GNOME idle, GDM login screen), TPM2 LUKS unlock with
an initrd SSH rescue path, and fan/temperature/microphone metrics on the
tailnet.

## One-time setup on each host

Run these on the host itself, after its first rebuild with the module.
The rebuild needs the initrd SSH host key to exist, so create it first:

```sh
sudo install -d -m 700 /etc/secrets/initrd
sudo ssh-keygen -t ed25519 -N "" -f /etc/secrets/initrd/ssh_host_ed25519_key
sudo nixos-rebuild boot --flake .#<host>   # or: sudo just sys
```

Enroll the TPM (BIOS: TPM/PTT/fTPM enabled). The passphrase slot stays as the
fallback:

```sh
sudo systemd-cryptenroll --tpm2-device=auto --tpm2-pcrs=7 /dev/disk/by-partlabel/disk-main-luks
```

Then:

- Tailscale admin console → Machines → the host → **Disable key expiry**.
- Wi-Fi must connect before login: `nmcli -f connection.permissions,802-11-wireless-security.psk-flags con show "<SSID>"`
  should show empty permissions and `psk-flags: 0` (system-wide secret).
- Reboot once while you are physically there and confirm it reaches GDM
  without a passphrase prompt.

## What PCR 7 means

PCR 7 records the Secure Boot state. With Secure Boot off (GRUB here), the
TPM releases the key to any boot of this machine: a pulled SSD stays
encrypted, but a stolen laptop boots to the login screen.

## When the TPM refuses (firmware/BIOS update)

Boot stops at the passphrase prompt before Wi-Fi and Tailscale exist. The
initrd brings up **wired** ethernet only (onboard or USB adapter; Wi-Fi is
not available that early) with DHCP hostname `<host>-initrd`, and runs SSH on
port 2222. Reach it from another machine on the same LAN, for example via
the other server:

```sh
ssh -J uni -p 2222 root@timy-initrd   # lands directly in the passphrase prompt
```

After unlocking, re-enroll so the next reboot is unattended again:

```sh
sudo systemd-cryptenroll --wipe-slot=tpm2 --tpm2-device=auto --tpm2-pcrs=7 /dev/disk/by-partlabel/disk-main-luks
```

## Monitoring

- `server-status` over SSH: `sensors` output plus the latest microphone sample.
- Prometheus node exporter, tailnet only: `curl http://<host>:9100/metrics`
  - fans: `node_hwmon_fan_rpm`
  - temperatures: `node_hwmon_temp_celsius`, `node_thermal_zone_temp`
  - microphone: `server_mic_rms_dbfs`, `server_mic_peak_dbfs`, `server_mic_up`
    (2 s sample every 30 s; about -50 dBFS is a quiet room, 0 is clipping)

The mic device defaults to `auto` (built-in DMIC, else first capture device).
Override with `my.server.micDevice = "plughw:1,10";` after checking `arecord -l`.
