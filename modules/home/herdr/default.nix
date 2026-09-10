{
  config,
  inputs,
  lib,
  pkgs,
  ...
}: let
  herdr = pkgs.callPackage ../../../pkgs/herdr.nix {};
in {
  home.packages = [herdr];

  xdg.configFile."herdr/config.toml".source = ./config.toml;

  home.file.".local/bin/herdr/focus-tab.sh" = {
    executable = true;
    text = ''
      #!/usr/bin/env bash
      set -euo pipefail

      index="''${1:-}"
      case "$index" in
        [1-9]) ;;
        *) exit 2 ;;
      esac

      herdr_bin="''${HERDR_BIN_PATH:-}"
      if [[ -z "$herdr_bin" ]]; then
        herdr_bin="${herdr}/bin/herdr"
      fi

      workspace_id="''${HERDR_ACTIVE_WORKSPACE_ID:-}"
      if [[ -z "$workspace_id" ]]; then
        workspace_id="''${HERDR_WORKSPACE_ID:-}"
      fi
      [[ -n "$workspace_id" ]] || exit 0

      tab_id="$(
        "$herdr_bin" tab list --workspace "$workspace_id" |
          ${pkgs.jq}/bin/jq -r --argjson index "$index" \
            '.result.tabs[$index - 1].tab_id // empty'
      )"
      [[ -n "$tab_id" ]] || exit 0

      "$herdr_bin" tab focus "$tab_id" >/dev/null
    '';
  };

  home.file.".claude/skills/herdr/SKILL.md".source =
    inputs.herdr + "/skills/herdr/SKILL.md";

  home.activation.installHerdrAgentIntegrations =
    lib.hm.dag.entryAfter ["configureCodexNotify"] ''
      if [[ -d "${config.home.homeDirectory}/.codex" ]]; then
        run ${herdr}/bin/herdr integration install codex
      fi

      if [[ -d "${config.home.homeDirectory}/.claude" ]]; then
        run ${herdr}/bin/herdr integration install claude
      fi
    '';
}
