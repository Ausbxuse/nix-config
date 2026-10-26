{
  lib,
  inputs,
  nixpkgs,
  const,
  adminAccess,
}: rec {
  stagingHostDefs = import ../machines/defs.nix {
    inherit lib const;
  };
  privateHostDefsPath = inputs.nix-secrets + "/hosts.nix";
  privateHostDefs =
    if builtins.pathExists privateHostDefsPath
    then import privateHostDefsPath {
      inherit lib const;
    }
    else {};
  hostDefs = lib.recursiveUpdate stagingHostDefs privateHostDefs;

  hostDefFor = host: hostDefs.${host};
  userFor = host: (hostDefFor host).username or const.username;
  homeDefFor = host: (hostDefFor host).home or {};
  nixosDefFor = host: (hostDefFor host).nixos or {};
  installDefFor = host: (hostDefFor host).install or {};

  nixosHosts = lib.attrNames (lib.filterAttrs (_: def: (def.nixos.enable or false)) hostDefs);
  homeHosts = lib.attrNames (lib.filterAttrs (_: def: (def.home.enable or false)) hostDefs);

  supportedSystems = const.supported-systems;
  forAllSystems = lib.genAttrs supportedSystems;
  pkgsBySystem = forAllSystems (system:
    import nixpkgs {
      inherit system;
      config.allowUnfree = true;
      overlays = import ../overlays;
    });
  forEachSystem = f: forAllSystems (system: f {
    inherit system;
    pkgs = pkgsFor system;
  });

  systemFor = host: hostDefs.${host}.system;
  hostsForSystem = system: hosts: builtins.filter (host: systemFor host == system) hosts;

  pkgsFor = system: pkgsBySystem.${system};

  machinePathFor = host: ./.. + "/machines/${host}";
  machineFileExists = host: file: builtins.pathExists (machinePathFor host + "/${file}");
  privateModulePathFor = kind: name: inputs.nix-secrets + "/modules/${kind}/${name}.nix";
  optionalPrivateModule = kind: name: let
    path = privateModulePathFor kind name;
  in
    lib.optional (builtins.pathExists path) path;

  mkHomeBaseModule = hostname: let
    homeDef = homeDefFor hostname;
    profile = homeDef.profile or null;
    displayProfile = homeDef.displayProfile or null;
  in
    {
      lib,
      ...
    }: {
      imports = lib.optional (profile != null) (./.. + "/modules/profiles/home/${profile}.nix");
    }
    // lib.optionalAttrs (displayProfile != null) {
      my.display.profile = displayProfile;
    };

  mkNixosBaseModule = hostname: let
    nixosDef = nixosDefFor hostname;
    installDef = installDefFor hostname;
    profile = nixosDef.profile or null;
    layout = installDef.layout or null;
  in {
    imports =
      lib.optional (profile != null) (./.. + "/modules/profiles/nixos/${profile}.nix")
      ++ lib.optional (layout != null) (./.. + "/modules/nixos/install/${layout}.nix");
  };

  homeModulesFor = hostname:
    [ (mkHomeBaseModule hostname) ]
    ++ lib.optional (machineFileExists hostname "home.nix") (machinePathFor hostname + "/home.nix")
    ++ optionalPrivateModule "home" hostname;

  nixosModulesFor = hostname:
    [ (mkNixosBaseModule hostname) ]
    ++ lib.optional (machineFileExists hostname "hardware-configuration.nix") (machinePathFor hostname + "/hardware-configuration.nix")
    ++ lib.optional (machineFileExists hostname "nixos.nix") (machinePathFor hostname + "/nixos.nix")
    ++ optionalPrivateModule "nixos" hostname;

  mkHome = hostname: let
    system = systemFor hostname;
    username = userFor hostname;
    hostDef = hostDefFor hostname;
  in
    inputs.home-manager.lib.homeManagerConfiguration {
      modules = homeModulesFor hostname;
      pkgs = pkgsFor system;
      extraSpecialArgs = {
        inherit inputs hostname const hostDefs hostDef username adminAccess;
      };
    };

  mkNixosWithHome = hostname: let
    system = systemFor hostname;
    username = userFor hostname;
    hostDef = hostDefFor hostname;
    homeDef = homeDefFor hostname;
  in
    nixpkgs.lib.nixosSystem {
      inherit system;
      modules =
        nixosModulesFor hostname
        ++ lib.optional (homeDef.enable or false) inputs.home-manager.nixosModules.home-manager
        ++ [
          {
            _module.args = {
              inherit username hostDef;
            };
          }
        ]
        ++ lib.optional (homeDef.enable or false) {
          home-manager.users.${username}.imports = homeModulesFor hostname;
          home-manager.extraSpecialArgs = {
            inherit inputs hostname const hostDefs hostDef username adminAccess;
          };
          home-manager.useGlobalPkgs = true;
          home-manager.useUserPackages = true;
        };
      specialArgs = {
        inherit inputs hostname const hostDefs hostDef username adminAccess;
      };
    };

  mkNamedAttrs = nameFor: valueFor: names:
    builtins.listToAttrs (map (name: {
        name = nameFor name;
        value = valueFor name;
      })
      names);

  mkChecks = prefix: valueFor: names:
    mkNamedAttrs (name: "${prefix}-${name}") valueFor names;
}
