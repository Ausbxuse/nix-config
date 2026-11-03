# https://search.nixos.org/options and in the NixOS manual (`nixos-help`).
{
  lib,
  username,
  ...
}: {
  imports = [
    ./kernel.nix
    ../../modules/nixos/hardware/rotate-sensor.nix
    ../../modules/nixos/server.nix
    ../../modules/nixos/build-server.nix
  ];

  # Stays at home as a server: a fixed zone instead of guessing it from
  # location, and no printers or Bluetooth devices to talk to.
  services.automatic-timezoned.enable = lib.mkForce false;
  time.timeZone = "America/Los_Angeles";
  services.printing.enable = lib.mkForce false;
  hardware.bluetooth.enable = lib.mkForce false;

  # Start the user services (Valkrie, agent hub, tmux) at boot; nobody logs in.
  users.users.${username}.linger = true;

  # gpu-004 is a shared work machine: timy connects to it (voice tunnel, hub
  # federation), and it may reach only Questions (Open WebUI, behind its
  # login) on 7880, the same one exception the headscale policy makes.
  # tailscale0 is trusted here, so this holds if that policy ever loosens.
  # Only new connections are dropped, so replies to timy's own still pass.
  # (-I puts each rule first: the accept ends up above the drop.)
  networking.firewall.extraCommands = ''
    iptables -I nixos-fw -i tailscale0 -s 100.64.0.8 -m conntrack --ctstate NEW -j DROP
    iptables -I nixos-fw -i tailscale0 -s 100.64.0.8 -p tcp --dport 7880 -j nixos-fw-accept
    ip6tables -I nixos-fw -i tailscale0 -s fd7a:115c:a1e0::8 -m conntrack --ctstate NEW -j DROP
    ip6tables -I nixos-fw -i tailscale0 -s fd7a:115c:a1e0::8 -p tcp --dport 7880 -j nixos-fw-accept
  '';
}
