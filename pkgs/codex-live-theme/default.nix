{
  lib,
  codex,
  rustPlatform,
  fetchFromGitHub,
  jq,
  stdenv,
  upstream,
}: let
  # Follow sadjow's release. Updating it also requires reviewing these patches
  # and refreshing the source/vendor hashes; never silently use an older CLI.
  patched = codex.overrideAttrs (old: rec {
    version = upstream.version;
    src = fetchFromGitHub {
      owner = "openai";
      repo = "codex";
      tag = "rust-v${version}";
      hash = "sha256-iib2+qQcGewC4Ga8cA80sInDJpzdSxDJP0y1DDOouI4=";
    };
    sourceRoot = "${src.name}/codex-rs";
    cargoDeps = rustPlatform.fetchCargoVendor {
      inherit src sourceRoot;
      name = "codex-${version}-vendor";
      hash = "sha256-0N2mEGqEl2ZjWqlbraVCWP0eoITgwmT9uO/1w/1eJno=";
    };
    patches = [./codex.patch];
    patchFlags = ["-p2"];
    postPatch =
      (old.postPatch or "")
      + ''
        crosstermDir=$(find "$cargoDepsCopy" -type d -name crossterm-0.29.0)
        patch -d "$crosstermDir" -p1 < ${./crossterm.patch}
      '';
    cargoBuildFlags = ["--package" "codex-cli" "--bin" "codex"];
    env =
      old.env
      // {
        CARGO_PROFILE_RELEASE_DEBUG = "0";
        CARGO_PROFILE_RELEASE_CODEGEN_UNITS = "16";
      };
    postInstall = "";
    postFixup = "";
  });
in
  upstream.overrideAttrs (old: {
    nativeBuildInputs = old.nativeBuildInputs ++ [jq];
    postInstall =
      (old.postInstall or "")
      + ''
        # Retain the complete sadjow package layout and runtime companions.
        install -m755 ${patched}/bin/codex "$out/lib/codex/bin/codex"
        jq --arg target ${lib.escapeShellArg stdenv.hostPlatform.rust.rustcTarget} \
          '.target = $target' "$out/lib/codex/codex-package.json" > manifest.json
        mv manifest.json "$out/lib/codex/codex-package.json"
      '';
    passthru = (old.passthru or {}) // {inherit patched;};
  })
