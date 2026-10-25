{lib, pkgs}: let
  source = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      ./encrypted_backup.py ./controller_backup.py ./federation.py ./bridge.py ./updates.py
    ];
  };
in pkgs.writeShellApplication {
  name = "agent-hub-backup";
  runtimeInputs = [pkgs.python3 pkgs.restic pkgs.openssh];
  text = ''
    umask 077
    exec python3 ${source}/encrypted_backup.py "$@"
  '';
  meta = {
    description = "Encrypted off-host Agent Hub snapshots and offline recovery";
    mainProgram = "agent-hub-backup";
    platforms = lib.platforms.linux;
  };
}
