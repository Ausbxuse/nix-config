{package, configFile ? "%h/.config/agent-hub/backup/config.json"}: {
  service = {
    Unit = {
      Description = "Save and verify an encrypted Agent Hub backup";
      ConditionPathExists = configFile;
      After = ["network-online.target"];
    };
    Service = {
      Type = "oneshot";
      ExecStart = "${package}/bin/agent-hub-backup run --config ${configFile}";
      TimeoutStartSec = "25min";
      UMask = "0077";
      Nice = 10;
      IOSchedulingClass = "idle";
      NoNewPrivileges = true;
      PrivateTmp = true;
      Environment = ["PYTHONUNBUFFERED=1"];
    };
  };
  timer = {
    Unit.Description = "Back up Agent Hub every 15 minutes";
    Timer = {
      OnCalendar = "*-*-* *:00/15:00";
      RandomizedDelaySec = 30;
      Persistent = true;
      Unit = "agent-hub-backup.service";
    };
    Install.WantedBy = ["timers.target"];
  };
}
