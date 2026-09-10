# Keep the Java runtime modules without the tools that construct/package runtimes.
# jdk.jlink embeds native libraries containing references to the complete SDK.
{
  jre_minimal,
  jdk,
}:
(jre_minimal.override {
  inherit jdk;
  jdkOnBuild = jdk;
}).overrideAttrs {
  pname = "${jdk.pname}-runtime";
  disallowedReferences = [jdk];
  buildPhase = ''
    runHook preBuild

    modules=()
    for module in ${jdk}/lib/openjdk/jmods/*.jmod; do
      name="''${module##*/}"
      name="''${name%.jmod}"
      case "$name" in
        jdk.jlink|jdk.jpackage) continue ;;
      esac
      modules+=("$name")
    done
    module_list=$(IFS=,; echo "''${modules[*]}")
    jlink --module-path ${jdk}/lib/openjdk/jmods \
      --add-modules "$module_list" --no-header-files --no-man-pages \
      --output "$out"

    runHook postBuild
  '';
}
