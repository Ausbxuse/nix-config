{package, nodeConfig ? "%h/.config/agent-hub/node.json"}: {
  Unit = {
    Description = "Agent Hub node and shared supervisor";
    ConditionPathExists = nodeConfig;
    # System network targets do not order user services. The application waits
    # for its configured tailnet address when Tailscale is still starting.
  };
  Service = {
    ExecStart = "${package}/bin/agent-hub --node-config ${nodeConfig}";
    Restart = "on-failure";
    RestartSec = 3;
    TimeoutStopSec = 10;
    UMask = "0077";
    Environment = [
      "PYTHONUNBUFFERED=1"
      "TMUX_TMPDIR=%t"
      "PATH=%h/.local/bin:%h/.nix-profile/bin:/etc/profiles/per-user/%u/bin:/run/current-system/sw/bin:/nix/var/nix/profiles/default/bin:/usr/bin:/bin"
    ];
  };
  Install.WantedBy = ["default.target"];
}
