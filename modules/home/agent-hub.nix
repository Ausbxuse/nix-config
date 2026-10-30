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
  };
}
