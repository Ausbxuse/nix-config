# https://search.nixos.org/options and in the NixOS manual (`nixos-help`).
{
  inputs,
  options,
  pkgs,
  lib,
  hostname,
  hostDefs,
  ...
}: {
  imports = [
    inputs.disko.nixosModules.disko
    ./env.nix
    ./nix.nix
    ./user.nix
    ./bootloader.nix
  ];
  system.stateVersion = "24.05";

  networking.timeServers = options.networking.timeServers.default ++ ["pool.ntp.org"];
  services.automatic-timezoned.enable = true;
  services.geoclue2 = {
    enable = true;
    enableDemoAgent = lib.mkForce true; # replaces gnome's geoclue demo agent
    enableWifi = true;
  };
  i18n.defaultLocale = "en_US.UTF-8";

  services = {
    openssh = {
      enable = true;
      # settings.PasswordAuthentication = true;
      # Let a new login replace the clipboard socket a dropped one left behind.
      settings.StreamLocalBindUnlink = true;
    };
  };

  # Hand this desktop's clipboard to our own hosts (see modules/home/ssh-clipboard)
  # so images paste into Claude Code there. Only NixOS hosts this repo manages:
  # root on any host that receives the socket can read the clipboard, so
  # machines administered by others (gpu-004) and bare tailnet IPs are excluded.
  programs.ssh.extraConfig = let
    ownHosts = lib.attrNames (lib.filterAttrs (_: def: def.nixos.enable or false) hostDefs);
  in ''
    Host ${lib.concatStringsSep " " ownHosts}
      RemoteForward /run/user/%i/ssh-clipboard.sock /run/user/%i/ssh-clipboard-serve.sock
  '';

  environment.localBinInPath = true;
  environment.systemPackages = with pkgs; [
    # ESSENTIALs
    cachix
    gcc
    rsync
    gnupg
    pciutils
    wirelesstools
    iw
    neovim
    gdu #ncdu alternative
    lsof
    wget
    git
    which
    htop
    # pkg-config
    killall
    unzip
    fwupd
    # pass
    # via
  ];

  networking.hostName = "${hostname}";
  networking.networkmanager = {
    enable = true;
    plugins = with pkgs; [
      networkmanager-openconnect
      networkmanager-openvpn
    ];
  };
  networking.firewall.enable = lib.mkDefault false;
  services.fwupd.enable = true;
}
