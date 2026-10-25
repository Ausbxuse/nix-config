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
    systemd.user.services.agent-hub-backup = lib.mkIf cfg.backup.enable
      (import ../../pkgs/agent-hub/backup-service.nix {package = pkgs.callPackage ../../pkgs/agent-hub/backup.nix {};}).service;
    systemd.user.timers.agent-hub-backup = lib.mkIf cfg.backup.enable
      (import ../../pkgs/agent-hub/backup-service.nix {package = pkgs.callPackage ../../pkgs/agent-hub/backup.nix {};}).timer;
  };
}
