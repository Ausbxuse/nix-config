{
  config,
  lib,
  pkgs,
  inputs,
  ...
}: let
  system = pkgs.stdenv.hostPlatform.system;
  codex = inputs.codex-cli-nix.packages.${system}.default;
  codexModel = "gpt-5.5";
  codexReasoningEffort = "medium";
  agentMaxThreads = 5;
  agentMaxDepth = 1;
  toml = pkgs.formats.toml {};
  codexAgents = {
    "repo-explorer.toml" = {
      name = "repo-explorer";
      description = "Fast read-only codebase explorer for narrow questions.";
      model = "gpt-5.3-codex-spark";
      model_reasoning_effort = "low";
      sandbox_mode = "read-only";
      developer_instructions = ''
        Answer narrow repo questions quickly.
        Use rg before broader filesystem scans.
        Do not edit files.
        Return concise findings with file paths, command/function flow, and confidence.
      '';
    };

    "mechanical-worker.toml" = {
      name = "mechanical-worker";
      description = "Fast worker for bounded mechanical edits with explicit file ownership.";
      model = "gpt-5.4-mini";
      model_reasoning_effort = "medium";
      developer_instructions = ''
        Implement only the assigned bounded change.
        You are not alone in the codebase. Do not revert edits made by others.
        Respect the assigned file/module ownership, and report any required scope expansion.
        Prefer small diffs, local patterns, and focused checks.
        Return changed files, tests run, and remaining risks.
      '';
    };
  };
  configureCodex =
    pkgs.writers.writePython3 "configure-codex" {
      libraries = [pkgs.python3Packages.tomlkit];
    } ''
      import os
      import sys
      from pathlib import Path

      import tomlkit
      from tomlkit.items import Table

      config_file = Path(sys.argv[1])
      notify_command = sys.argv[2]
      model = sys.argv[3]
      reasoning_effort = sys.argv[4]
      agent_max_threads = int(sys.argv[5])
      agent_max_depth = int(sys.argv[6])
      status_line = [
          "model-with-reasoning",
          "current-dir",
          "weekly-limit",
          "five-hour-limit",
      ]


      def ensure_table(doc, key):
          table = doc.get(key)
          if isinstance(table, Table):
              return table

          table = tomlkit.table()
          doc[key] = table
          return table


      config_file.parent.mkdir(parents=True, exist_ok=True)

      if config_file.is_symlink():
          text = config_file.read_text()
          config_file.unlink()
      elif config_file.exists():
          text = config_file.read_text()
      else:
          text = ""

      doc = tomlkit.parse(text) if text.strip() else tomlkit.document()
      doc["model"] = model
      doc["model_reasoning_effort"] = reasoning_effort
      doc["notify"] = [notify_command]
      doc["sandbox_mode"] = "danger-full-access"

      tui = ensure_table(doc, "tui")
      tui["status_line"] = status_line
      tui["status_line_use_colors"] = True

      agents = ensure_table(doc, "agents")
      agents["max_threads"] = agent_max_threads
      agents["max_depth"] = agent_max_depth

      config_file.write_text(tomlkit.dumps(doc))
      os.chmod(config_file, 0o600)
    '';
in {
  imports = [
    ./codex-skills.nix
  ];

  my.codexSkills.enable = lib.mkDefault true;

  home.packages = [codex];

  home.file = lib.mapAttrs' (name: agent:
    lib.nameValuePair ".codex/agents/${name}" {
      source = toml.generate "codex-agent-${name}" agent;
    })
  codexAgents;

  home.activation.configureCodexNotify = lib.hm.dag.entryAfter ["writeBoundary"] ''
    ${configureCodex} \
      "${config.home.homeDirectory}/.codex/config.toml" \
      "${config.home.homeDirectory}/.local/bin/tmux/codex-notify.sh" \
      "${codexModel}" \
      "${codexReasoningEffort}" \
      "${toString agentMaxThreads}" \
      "${toString agentMaxDepth}"
  '';
}
