{pkgs, ...}: let
  package = import ./default.nix {inherit pkgs;};
in {
  # Bootstrap deployment used writable systemd control units so it could avoid
  # activating unrelated NixOS edits. A full switch adopts the declared units.
  system.activationScripts.agentHubPushBootstrapMigration.text = ''
    if ${pkgs.gnugrep}/bin/grep -qF 'Description=Agent Hub push delivery' /etc/systemd/system.control/agent-hub-push.service 2>/dev/null; then
      ${pkgs.coreutils}/bin/rm -f /etc/systemd/system.control/agent-hub-push.service
    fi
    if ${pkgs.gnugrep}/bin/grep -qF '/var/lib/agent-hub-push-nginx/nginx.conf' /etc/systemd/system.control/nginx.service.d/30-agent-hub-push.conf 2>/dev/null; then
      ${pkgs.coreutils}/bin/rm -f /etc/systemd/system.control/nginx.service.d/30-agent-hub-push.conf
    fi
    if ${pkgs.gnugrep}/bin/grep -qxF 'Wants=agent-hub-push.service' /etc/systemd/system.control/multi-user.target.d/30-agent-hub-push.conf 2>/dev/null; then
      ${pkgs.coreutils}/bin/rm -f /etc/systemd/system.control/multi-user.target.d/30-agent-hub-push.conf
    fi
  '';
  systemd.services.agent-hub-push = {
    description = "Agent Hub push delivery";
    wantedBy = ["multi-user.target"];
    after = ["network-online.target"];
    serviceConfig = {
      ExecStart = "${package}/bin/agent-hub-push --config /run/credentials/agent-hub-push.service/config";
      LoadCredential = "config:/var/lib/agent-hub-push-config.json";
      DynamicUser = true;
      StateDirectory = "agent-hub-push";
      StateDirectoryMode = "0700";
      UMask = "0077";
      Restart = "on-failure";
      RestartSec = 3;
      NoNewPrivileges = true;
      PrivateTmp = true;
      ProtectHome = true;
      ProtectSystem = "strict";
      RestrictAddressFamilies = ["AF_UNIX" "AF_INET" "AF_INET6"];
    };
  };
  services.nginx.virtualHosts."zhenyuzhao.com".locations."^~ /agent-alerts/" = {
    proxyPass = "http://127.0.0.1:14575";
    extraConfig = ''
      client_max_body_size 10k;
      proxy_read_timeout 35s;
    '';
  };
}
