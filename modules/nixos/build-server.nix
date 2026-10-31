# Build machine for the laptops: accepts their remote builds over the tailnet
# and, once an attic push token is in nix-secrets, pre-builds their systems
# every night into the binary cache they already substitute from, so their
# `just sys` downloads instead of compiling.
#
# One-time setup for the nightly push (see docs/server-hosts.md):
#   on milkyway: sudo atticd-atticadm make-token --sub timy-prebuild \
#                  --validity 2y --pull zhenyu-public --push zhenyu-public
#   here:        sops ~/src/private/nix-secrets/secrets.yaml   (add attic-push-token)
#   then set my.buildServer.prebuild.enable = true for this host.
{
  config,
  lib,
  pkgs,
  username,
  ...
}: let
  cfg = config.my.buildServer;
  home = config.users.users.${username}.home;
  prebuild = pkgs.writeShellApplication {
    name = "prebuild-hosts";
    runtimeInputs = [config.nix.package pkgs.git pkgs.attic-client];
    text = ''
      secrets=${lib.escapeShellArg cfg.prebuild.secretsCheckout}
      # Committed nix-secrets only: the checkout may carry local edits the
      # other hosts don't have.
      rev=$(git -C "$secrets" rev-parse HEAD)
      attic login zhenyu ${lib.escapeShellArg cfg.prebuild.cacheUrl} "$(cat "$CREDENTIALS_DIRECTORY/attic-token")"
      status=0
      hosts=(${lib.escapeShellArgs cfg.prebuild.hosts})
      for host in "''${hosts[@]}"; do
        echo "building $host"
        if out=$(nix build --refresh --print-out-paths --out-link "$STATE_DIRECTORY/$host" \
            ${lib.escapeShellArg cfg.prebuild.flake}"#nixosConfigurations.$host.config.system.build.toplevel" \
            --override-input nix-secrets "git+file://$secrets?rev=$rev"); then
          # attic skips paths already signed by cache.nixos.org.
          attic push ${lib.escapeShellArg "zhenyu:${cfg.prebuild.cache}"} "$out" || status=1
        else
          status=1
        fi
      done
      exit "$status"
    '';
  };
in {
  options.my.buildServer = {
    clientKeys = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [];
      description = "SSH public keys of hosts' nix-daemons (root) allowed to build here.";
    };
    prebuild = {
      enable = lib.mkEnableOption "nightly pre-builds pushed to the attic cache (needs the attic-push-token secret)";
      hosts = lib.mkOption {
        type = lib.types.listOf lib.types.str;
        default = ["razy"];
      };
      flake = lib.mkOption {
        type = lib.types.str;
        default = "github:Ausbxuse/nix-config";
        description = "Built as pushed, so a dirty working tree here never reaches the cache.";
      };
      secretsCheckout = lib.mkOption {
        type = lib.types.str;
        default = "${home}/src/private/nix-secrets";
      };
      cacheUrl = lib.mkOption {
        type = lib.types.str;
        default = "https://cache.zhenyuzhao.com";
      };
      cache = lib.mkOption {
        type = lib.types.str;
        default = "zhenyu-public";
      };
    };
  };

  config = lib.mkMerge [
    {
      # The nix-ssh user can only run `nix-daemon --stdio`; trusted so it
      # accepts the client's unsigned build inputs.
      nix.sshServe = {
        enable = true;
        protocol = "ssh-ng";
        write = true;
        trusted = true;
        keys = cfg.clientKeys;
      };

      # Builds (local, remote and nightly) yield the CPU and disk to the
      # agents and services this host is really for.
      nix.daemonCPUSchedPolicy = "batch";
      nix.daemonIOSchedPriority = 7;
    }

    (lib.mkIf cfg.prebuild.enable {
      sops.secrets.attic-push-token = {};

      systemd.services.prebuild-hosts = {
        description = "Pre-build ${lib.concatStringsSep ", " cfg.prebuild.hosts} into the binary cache";
        wants = ["network-online.target"];
        after = ["network-online.target"];
        serviceConfig = {
          Type = "oneshot";
          User = username;
          ExecStart = lib.getExe prebuild;
          LoadCredential = "attic-token:${config.sops.secrets.attic-push-token.path}";
          StateDirectory = "prebuild-hosts";
          RuntimeDirectory = "prebuild-hosts";
          # attic login writes its config (with the token) here, not in ~.
          Environment = "XDG_CONFIG_HOME=%t/prebuild-hosts";
          TimeoutStartSec = "6h";
        };
      };
      systemd.timers.prebuild-hosts = {
        wantedBy = ["timers.target"];
        timerConfig = {
          OnCalendar = "*-*-* 02:30";
          RandomizedDelaySec = "15m";
          Persistent = true;
        };
      };
    })
  ];
}
