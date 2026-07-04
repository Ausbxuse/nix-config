{
  config,
  lib,
  pkgs,
  ...
}: let
  cfg = config.my.codexSkills;
  emptySkills = pkgs.runCommandLocal "codex-empty-skills" {} ''
    mkdir -p "$out"
  '';
  pSkill = {
    skill = pkgs.writeText "codex-skill-p-SKILL.md" ''
      ---
      name: p
      description: Supervised parallel backlog workflow for faster Codex execution. Use when the user invokes $p, says "fast queue", "parallel backlog", "triage this", or gives a messy queue of independent coding/repo tasks and wants faster results through subagents while keeping the main agent as final integrator.
      ---

      # Parallel Backlog

      Use this skill to turn a rough task dump into supervised fan-out/fan-in.

      ## Workflow

      1. Treat the main agent as dispatcher and final integrator.
      2. Quickly classify each item as read-only exploration, mechanical edit, semantic/cross-cutting edit, or verification.
      3. Spawn read-only explorers for independent codepath, behavior, or "where is this implemented?" questions.
      4. Spawn implementation workers only for bounded edits with disjoint file/module ownership.
      5. Keep ambiguous behavior, cross-cutting design, shared CLI plumbing, conflict resolution, and final review in the main thread.
      6. While subagents run, do useful non-overlapping work in the main thread.
      7. Review returned findings and diffs before integrating them.
      8. Run the smallest relevant checks, then summarize changed files, checks, and remaining risks.

      ## Routing

      Prefer `repo-explorer` for read-only exploration when available; otherwise use the built-in explorer.
      Prefer `mechanical-worker` for bounded mechanical edits when available; otherwise use the built-in worker.
      Do not use parallel writers for tasks likely to edit the same file, command registry, parser, generated doc, or test fixture.

      ## Output Discipline

      If the user asks for implementation, proceed after brief triage instead of stopping at a plan.
      Keep user-facing triage concise: list only the split, ownership, and blocked ambiguities that affect execution.
      Preserve user edits and do not revert unrelated work.
    '';

    openaiYaml = pkgs.writeText "codex-skill-p-openai.yaml" ''
      interface:
        display_name: "p"
        short_description: "Parallel backlog dispatcher"
        default_prompt: "Use $p to triage this backlog, fan out independent work, and integrate the result."
      policy:
        allow_implicit_invocation: true
    '';
  };
  builtInSkills = pkgs.runCommandLocal "codex-built-in-skills" {} ''
    install -Dm644 ${pSkill.skill} "$out/p/SKILL.md"
    install -Dm644 ${pSkill.openaiYaml} "$out/p/agents/openai.yaml"
  '';
  mergedSkills = pkgs.runCommandLocal "codex-skills" {} ''
    mkdir -p "$out"
    cp -R --no-preserve=mode,ownership ${builtInSkills}/. "$out"/
    cp -R --no-preserve=mode,ownership ${cfg.source}/. "$out"/
  '';
in {
  options.my.codexSkills = {
    enable = lib.mkEnableOption "declarative Codex skill deployment";

    source = lib.mkOption {
      type = lib.types.path;
      default = emptySkills;
      defaultText = lib.literalExpression "pkgs.runCommandLocal \"codex-empty-skills\" {} \"mkdir -p \\$out\"";
      description = ''
        Directory tree to deploy into the global Codex skill location.
        This is expected to come from a skill-pack flake package output.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    home.activation.deployCodexSkills = lib.hm.dag.entryAfter ["writeBoundary"] ''
      target="${config.home.homeDirectory}/.agents/skills"
      ${pkgs.coreutils}/bin/mkdir -p "$target"
      ${pkgs.coreutils}/bin/cp \
        -R -L \
        --no-preserve=mode,ownership \
        --remove-destination \
        ${mergedSkills}/. \
        "$target"/
    '';
  };
}
