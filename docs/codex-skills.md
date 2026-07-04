# Codex Skills Workflow

This workflow keeps Codex skills easy to author and easy to deploy:

- author skills as plain files in a separate skill-pack repo
- group skills into profiles in Nix
- deploy one built profile into the global Codex skill location
  (`~/.agents/skills`) with Home Manager

## Create a Skill-Pack Repo

Create a new repo from this flake template:

```bash
nix flake init -t /home/zhenyu/src/public/nix-config#codex-skills
```

The generated repo has this shape:

```text
skills/
  example/
    SKILL.md
profiles/
  default.nix
flake.nix
```

The intended authoring flow is explicit:

1. create `skills/<name>/`
2. add `skills/<name>/SKILL.md`
3. register the skill in `profiles/default.nix`
4. build or deploy the profile

No helper command is required.

## Skill-Pack Flake Contract

The generated skill-pack flake exports:

- `packages.<system>.default`: the `default` profile as a directory tree
- `packages.<system>.skills-<profile>`: named profile outputs
- `packages.<system>.profiles.<profile>`: named profile outputs grouped under `profiles`
- `lib.profiles`: the profile definitions

Each selected skill must exist under `skills/<name>/` and contain `SKILL.md`.

## Built-In Skills

The home module deploys one built-in skill before merging any external
skill-pack source:

- `$p`: supervised parallel backlog workflow. Use it only for messy queues of
  independent items where read-only exploration or clearly disjoint edits can
  be fanned out.

## Deploy From nix-config

The built-in skills are deployed by `modules/home/codex.nix`. Any host using the
shared home profiles gets the global Codex skill directory populated at
activation time because those profiles import the Codex home module.

To add extra skills from a skill-pack, add the skill-pack as a flake input in
`flake.nix`, for example:

```nix
inputs.codex-skills.url = "path:../codex-skills";
```

Then point your home-manager config at the package output:

```nix
{
  my.codexSkills = {
    source = inputs.codex-skills.packages.${pkgs.system}.skills-default;
  };
}
```

Switch profiles by changing the package you point at, for example:

```nix
source = inputs.codex-skills.packages.${pkgs.system}.skills-coding;
```

Then deploy with your normal Home Manager workflow:

```bash
just hm
```

## Design Notes

- The Home Manager module materializes regular files during activation because
  current Codex releases may ignore symlinked `SKILL.md` files.
- Profile selection stays in the skill-pack flake.
- Skills are authored as normal files, not embedded as Nix strings.
- If the workflow starts feeling complex, prefer simpler profile data over new helpers.
