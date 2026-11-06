{config, lib, pkgs, ...}: let
  cfg = config.services.agent-hub;
in {
  options.services.agent-hub = {
    enable = lib.mkEnableOption "the per-host Agent Hub service";
    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.callPackage ../../pkgs/agent-hub {};
    };
    nodeConfig = lib.mkOption {
      type = lib.types.str;
      default = "%h/.config/agent-hub/node.json";
      description = "Private runtime node/peer configuration; keep credentials outside the Nix store.";
    };
    backup.enable = lib.mkEnableOption "encrypted controller backups (requires a private backup config)";
    # A worker the controller can't open connections to (razy: timy may not reach it) opens the
    # way itself: an SSH tunnel to the controller, which reaches this hub (on loopback only) at
    # 127.0.0.1:<port> on its side. Set up once with pkgs/agent-hub/join-over-ssh.sh.
    uplink = {
      enable = lib.mkEnableOption "the SSH tunnel by which the controller reaches this worker";
      controller = lib.mkOption {
        type = lib.types.str;
        default = "zhenyu@100.64.0.4";
      };
      port = lib.mkOption {
        type = lib.types.port;
        default = 8766;
        description = "The controller's loopback port for this worker (its peer URL is http://127.0.0.1:<port>).";
      };
      identity = lib.mkOption {
        type = lib.types.str;
        default = "%h/.ssh/agent-hub-uplink";
        description = "A key the controller lets do nothing but hold this tunnel (and clear a stale one).";
      };
    };
  };
  config = lib.mkIf (cfg.enable && pkgs.stdenv.hostPlatform.isLinux) {
    home.packages = [cfg.package];
    systemd.user.services.agent-hub = import ../../pkgs/agent-hub/service.nix {
      package = cfg.package;
      inherit (cfg) nodeConfig;
    };
    # install-service.sh relinks these to its own build for quick rollouts;
    # take them back on activation instead of aborting the whole switch.
    xdg.configFile."systemd/user/agent-hub.service".force = true;
    xdg.configFile."systemd/user/default.target.wants/agent-hub.service".force = true;
    systemd.user.services.agent-hub-backup = lib.mkIf cfg.backup.enable
      (import ../../pkgs/agent-hub/backup-service.nix {package = pkgs.callPackage ../../pkgs/agent-hub/backup.nix {};}).service;
    systemd.user.timers.agent-hub-backup = lib.mkIf cfg.backup.enable
      (import ../../pkgs/agent-hub/backup-service.nix {package = pkgs.callPackage ../../pkgs/agent-hub/backup.nix {};}).timer;
    systemd.user.services.agent-hub-uplink = lib.mkIf cfg.uplink.enable (let
      ssh = "${pkgs.openssh}/bin/ssh -i ${cfg.uplink.identity} -o IdentitiesOnly=yes -o BatchMode=yes"
        + " -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=2";
    in {
      Unit = {
        Description = "Agent Hub: the tunnel by which ${cfg.uplink.controller} reaches this worker";
        ConditionPathExists = cfg.uplink.identity;
        After = ["agent-hub.service"];
        # Offline (asleep, away from a network) it keeps trying, every 10 s, for good.
        StartLimitIntervalSec = 0;
      };
      Service = {
        # A tunnel this machine left open as it slept still holds the port on the controller
        # (until its TCP gives up, hours later). The key's only command there frees it.
        ExecStartPre = "-${ssh} ${cfg.uplink.controller} clear";
        ExecStart = "${ssh} -N -o ExitOnForwardFailure=yes"
          + " -R 127.0.0.1:${toString cfg.uplink.port}:127.0.0.1:8765 ${cfg.uplink.controller}";
        Restart = "always";
        RestartSec = 10;
      };
      Install.WantedBy = ["default.target"];
    });
  };
}
