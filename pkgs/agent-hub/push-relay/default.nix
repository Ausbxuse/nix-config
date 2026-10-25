{pkgs}: let
  python = pkgs.python3.withPackages (ps: [ps.pywebpush ps.cryptography ps.requests]);
  source = pkgs.lib.fileset.toSource {
    root = ./.;
    fileset = pkgs.lib.fileset.unions [./server.py ./static];
  };
in pkgs.writeShellApplication {
  name = "agent-hub-push";
  text = ''exec ${python}/bin/python3 ${source}/server.py "$@"'';
}
