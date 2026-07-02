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
  fixSkill = {
    skill = pkgs.writeText "codex-skill-fix-SKILL.md" ''
      ---
      name: fix
      description: Result-driven coding workflow for normal development. Use when the user invokes $fix, asks to fix, implement, polish, adjust, or make a concrete repo result happen with low human mental load and focused verification.
      ---

      # Fix

      Use this skill as the default single-agent workflow for normal coding tasks.

      ## Contract

      The user owns the desired result and design consent. You own discovery,
      implementation mechanics, and verification.

      Treat the user's requested result as the source of truth. The user should
      not need to name files, tests, commands, or internal APIs unless they
      already know them.

      ## Workflow

      1. Restate the desired observable result in one sentence.
      2. Discover the relevant subsystem with the smallest useful search.
      3. Before editing, give one compact design checkpoint:
         - subsystem or user-facing boundary
         - intended code boundary/change
         - verification command or bounded check
      4. Ask only for real product/design ambiguity, missing hardware/secrets,
         unsafe commands, or multiple plausible meanings that lead to different
         implementations.
      5. Patch narrowly using local patterns.
      6. Run the cheapest meaningful verification that proves the requested
         result.
      7. Iterate until verification passes or a real blocker remains.
      8. Final answer: changed files, checks run, and any bounded/remaining gap.

      ## Verification

      Verification is required; new tests are not automatic.

      Prefer, in order:

      1. the real user-facing command or workflow
      2. a bounded smoke of that same workflow when the real one is expensive,
         slow, hardware-bound, or unsafe
      3. an existing focused test
      4. a new focused test only when it protects a durable contract

      Add or change tests only when they capture durable behavior, reproduce a
      real bug, protect a schema/CLI/config boundary, or cover a workflow the
      repo can reasonably exercise.

      Do not add tests that only mirror implementation details, freeze
      subjective styling choices, assert private helper internals without a
      contract, or exist only because a helper was introduced.

      For cosmetic or style changes, update existing tests only if they already
      cover that output. Otherwise use a focused smoke/manual-visible check and
      state that no durable test was added.

      ## Scope Discipline

      Keep edits surgical. Do not refactor adjacent code, add compatibility
      shims, introduce configuration knobs, or create new abstractions unless
      they are necessary for the requested result.

      If the task starts crossing subsystem boundaries, the reproducer changes,
      or the thread gets confused, produce a concise handoff and recommend a
      fresh thread.
    '';

    openaiYaml = pkgs.writeText "codex-skill-fix-openai.yaml" ''
      interface:
        display_name: "fix"
        short_description: "Result-driven coding workflow"
        default_prompt: "Use $fix to make the requested repo result happen with a compact design checkpoint and focused verification."
      policy:
        allow_implicit_invocation: true
    '';
  };
  builtInSkills = pkgs.runCommandLocal "codex-built-in-skills" {} ''
    install -Dm644 ${fixSkill.skill} "$out/fix/SKILL.md"
    install -Dm644 ${fixSkill.openaiYaml} "$out/fix/agents/openai.yaml"
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
