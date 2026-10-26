# Overlays shared by NixOS hosts (modules/nixos/core/nix.nix) and the package
# set standalone Home Manager hosts build from (lib/default.nix).
[
  (_final: prev: {
    pythonPackagesExtensions =
      prev.pythonPackagesExtensions
      ++ [
        (_pyfinal: pyprev: {
          # reuse 6.2.0's sphinx man builder produces no pages, so the `man`
          # output is never created (pulled in by amberol). Drop it until fixed.
          reuse = pyprev.reuse.overridePythonAttrs (old: {
            outputs = prev.lib.remove "man" old.outputs;
            sphinxBuilders = prev.lib.remove "man" old.sphinxBuilders;
          });
        })
      ];
  })
]
