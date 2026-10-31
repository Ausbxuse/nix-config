# Send builds to timy (modules/nixos/build-server.nix) over the tailnet, so a
# rebuild on battery doesn't compile here. When timy is unreachable Nix gives
# up on it after the connect timeout and builds locally.
#
# The daemon's key is generated on first activation; add its public half,
#   sudo cat /etc/nix/remote-builder_ed25519.pub
# to my.buildServer.clientKeys on timy.
{
  pkgs,
  hostname,
  ...
}: let
  key = "/etc/nix/remote-builder_ed25519";
  timy = "100.64.0.4";
in {
  system.activationScripts.remoteBuilderKey.text = ''
    if [ ! -f ${key} ]; then
      ${pkgs.openssh}/bin/ssh-keygen -q -t ed25519 -N "" -C "root@${hostname}-builder" -f ${key}
    fi
  '';

  nix.distributedBuilds = true;
  nix.buildMachines = [
    {
      hostName = timy;
      protocol = "ssh-ng";
      sshUser = "nix-ssh";
      sshKey = key;
      # base64 of timy's /etc/ssh/ssh_host_ed25519_key.pub
      publicHostKey = "c3NoLWVkMjU1MTkgQUFBQUMzTnphQzFsWkRJMU5URTVBQUFBSU1ORjIxbGtoQi9vdzRHQURkRStFUVFjcGo1b2Z5OVFTVzJjVWovUHhqTE8=";
      system = "x86_64-linux";
      maxJobs = 8;
      speedFactor = 4;
      supportedFeatures = ["nixos-test" "benchmark" "big-parallel" "kvm"];
    }
  ];
  # timy fetches what caches have itself instead of razy uploading it.
  nix.settings.builders-use-substitutes = true;

  programs.ssh.extraConfig = ''
    Host ${timy}
      ConnectTimeout 5
  '';
}
