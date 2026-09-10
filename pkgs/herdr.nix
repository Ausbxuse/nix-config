{
  fetchurl,
  lib,
  stdenvNoCC,
}: let
  version = "0.8.2";
  releases = {
    x86_64-linux = {
      asset = "herdr-linux-x86_64";
      hash = "sha256-l2FQoU1JDJSyQ+ouGn6y37Z/EuNrGC25CTb2co5q7PQ=";
    };
    aarch64-linux = {
      asset = "herdr-linux-aarch64";
      hash = "sha256-9VYQZY4cLg0qrvcwtLKriF9/i6AChas3K/sU8uPVtA0=";
    };
  };
  release =
    releases.${stdenvNoCC.hostPlatform.system}
    or (throw "herdr is unsupported on ${stdenvNoCC.hostPlatform.system}");
in
  stdenvNoCC.mkDerivation {
    pname = "herdr";
    inherit version;

    src = fetchurl {
      url = "https://github.com/herdrdev/herdr/releases/download/v${version}/${release.asset}";
      inherit (release) hash;
    };

    dontUnpack = true;

    installPhase = ''
      runHook preInstall
      install -Dm755 "$src" "$out/bin/herdr"
      runHook postInstall
    '';

    meta = {
      description = "Agent-native terminal workspace";
      homepage = "https://herdr.dev";
      license = lib.licenses.asl20;
      mainProgram = "herdr";
      platforms = builtins.attrNames releases;
    };
  }
