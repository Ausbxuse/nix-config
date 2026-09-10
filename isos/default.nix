# isos/default.nix
{
  pkgs,
  inputs,
  const,
  hostDefs,
  offlineTargets ? {},
  ...
}: let
  inherit (inputs.nixpkgs) lib;
  system = pkgs.stdenv.hostPlatform.system;
  targetsForSystem = lib.filterAttrs (_: target: target.pkgs.stdenv.hostPlatform.system == system) offlineTargets;
  mkOfflineTarget = host: target: let
    hostDef = hostDefs.${host};
    diskAlias = "/run/nix-config-installer/${host}-target-disk";
    installerDiskConfig = target.extendModules {
      modules = [
        {
          disko.devices.disk.main.device = lib.mkForce diskAlias;
        }
      ];
    };
    toplevel = target.config.system.build.toplevel;
    closureInfo = target.pkgs.closureInfo {rootPaths = [toplevel];};
  in {
    inherit diskAlias;
    system = target.pkgs.stdenv.hostPlatform.system;
    username = hostDef.username or const.username;
    name = const.name;
    email = const.email;
    nixosEnabled =
      if (hostDef.nixos.enable or false)
      then "yes"
      else "no";
    homeEnabled =
      if (hostDef.home.enable or false)
      then "yes"
      else "no";
    nixosProfile = hostDef.nixos.profile or "";
    homeProfile = hostDef.home.profile or "";
    displayProfile = hostDef.home.displayProfile or "";
    installLayout = hostDef.install.layout or "";
    swapSize = hostDef.install.swapSize or "";
    toplevel = toString toplevel;
    closureInfo = toString closureInfo;
    diskoScript = toString installerDiskConfig.config.system.build.diskoScript;
  };
  offlineTargetManifest = lib.mapAttrs mkOfflineTarget targetsForSystem;
  offlineInstallManifest = pkgs.writeText "offline-install-manifest-${system}.json" (builtins.toJSON {
    schemaVersion = 1;
    strict = true;
    inherit system;
    targets = offlineTargetManifest;
  });
  # Give the editable template its own store object.  Referring directly to
  # ./installer.conf from a generated shell script leaves the flake source path
  # as plain text, so Nix does not retain that source tree in the ISO closure.
  installerConfigTemplate = pkgs.writeText "nixos-installer.conf" (builtins.readFile ./installer.conf);
  installerEditorHost = "spacy";
  installerEditorTarget = assert lib.assertMsg (builtins.hasAttr installerEditorHost targetsForSystem)
  "the graphical installer requires the embedded '${installerEditorHost}' target for its declarative Neovim setup";
    targetsForSystem.${installerEditorHost};
  installerEditorUsername = hostDefs.${installerEditorHost}.username or const.username;
  installerEditorHomeConfig = installerEditorTarget.config.home-manager.users.${installerEditorUsername};
  installerEditorHome = installerEditorHomeConfig.home.activationPackage;
  installerEditorNvim = installerEditorHomeConfig.programs.neovim.finalPackage;
  installerEditorConfigHome = "${installerEditorHome}/home-files/.config";
  installerEditorSite = "${installerEditorHome}/home-files/.local/share/nvim/site";
  # Do not depend on live-user activation having won a race with GNOME
  # autostart.  Supply the immutable config and plugin pack explicitly while
  # leaving data, cache, and state in the live user's writable home.
  installerEditor =
    (pkgs.writeShellApplication {
      name = "nvim";
      runtimeInputs = [pkgs.coreutils];
      text = ''
        export XDG_CONFIG_HOME=${lib.escapeShellArg installerEditorConfigHome}
        export NVIM_NIX_INSTALLER_FULL_CONFIG=1

        installer_cache="''${XDG_CACHE_HOME:-$HOME/.cache}/nvim"
        installer_state="''${XDG_STATE_HOME:-$HOME/.local/state}/nvim"
        mkdir -p "$installer_cache/undo" "$installer_state/spell"

        exec ${installerEditorNvim}/bin/nvim \
          --cmd ${lib.escapeShellArg "set packpath^=${installerEditorSite}"} \
          "$@"
      '';
    }).overrideAttrs {
      doInstallCheck = true;
      installCheckPhase = ''
        runHook preInstallCheck

        test_root="$TMPDIR/installer-editor-check"
        mkdir -p "$test_root/home" "$test_root/data" "$test_root/state" "$test_root/cache"
        HOME="$test_root/home" \
        XDG_CONFIG_HOME="$test_root/ignored-config" \
        XDG_DATA_HOME="$test_root/data" \
        XDG_STATE_HOME="$test_root/state" \
        XDG_CACHE_HOME="$test_root/cache" \
          ${pkgs.coreutils}/bin/timeout 30 "$out/bin/nvim" --headless "$test_root/installer.conf" \
            '+lua assert(vim.env.NVIM_NIX_INSTALLER_FULL_CONFIG == "1"); assert(vim.env.NVIM_NIX_PACKAGED == "1"); assert(vim.g.nix_config_plugins_validated == true); assert(vim.g.nix_config_installer_init_loaded == nil); assert(#vim.api.nvim_get_runtime_file("snippets/lua.json", true) > 0); assert(vim.startswith(vim.fn.stdpath("config"), "/nix/store/"))' \
            '+write' '+qall!'
        test -f "$test_root/installer.conf"

        runHook postInstallCheck
      '';
    };
  installerPackages = import ../pkgs {
    inherit lib pkgs const offlineInstallManifest;
    hostDefs = hostDefs;
    inherit installerConfigTemplate installerEditor;
  };
  installProfile = installerPackages."install-profile";
  installRazy = pkgs.writeShellApplication {
    name = "install-razy";
    runtimeInputs = [installProfile];
    text = ''
      exec install-profile --profile razy --no-edit-prompt -- "$@"
    '';
  };
  installSpacy = pkgs.writeShellApplication {
    name = "install-spacy";
    runtimeInputs = [installProfile];
    text = ''
      exec install-profile --profile spacy --no-edit-prompt -- "$@"
    '';
  };
  installInteractive = pkgs.writeShellApplication {
    name = "install-interactive";
    runtimeInputs = [installProfile];
    text = ''
      exec install-profile --interactive "$@"
    '';
  };
  editInstallConfig = pkgs.writeShellApplication {
    name = "edit-install-config";
    runtimeInputs = [installProfile];
    text = ''
      exec install-profile --edit
    '';
  };
  installProfileDesktop = pkgs.makeDesktopItem {
    name = "install-nixos-auto";
    desktopName = "Install NixOS (Auto Detect)";
    comment = "Detect NVIDIA hardware and install the embedded Razy or Spacy profile";
    icon = "system-software-install";
    exec = "${installProfile}/bin/install-profile";
    terminal = true;
    categories = ["System"];
  };
  editInstallConfigDesktop = pkgs.makeDesktopItem {
    name = "edit-install-config";
    desktopName = "Edit Installer Configuration";
    comment = "Customize the single-file NixOS installer settings with Neovim";
    icon = "accessories-text-editor";
    exec = "${editInstallConfig}/bin/edit-install-config";
    terminal = true;
    categories = ["System" "Utility" "TextEditor"];
  };
  installInteractiveDesktop = pkgs.makeDesktopItem {
    name = "install-nixos-interactive";
    desktopName = "Interactive NixOS Installer";
    comment = "Run the original interactive terminal installer";
    icon = "utilities-terminal";
    exec = "${installInteractive}/bin/install-interactive";
    terminal = true;
    categories = ["System"];
  };
  installProfileAutostart = pkgs.makeAutostartItem {
    name = "install-nixos-auto";
    package = installProfileDesktop;
  };
  isoConfig = inputs.nixpkgs.lib.nixosSystem {
    inherit system;
    specialArgs = {inherit inputs const hostDefs;};
    modules = [
      inputs.sops-nix.nixosModules.sops
      ./system.nix
      ./installer-workarounds.nix
      ./gnome-graphical.nix
      {
        assertions =
          lib.mapAttrsToList (host: target: {
            assertion =
              lib.all
              (module: lib.elem module target.config.boot.initrd.availableKernelModules)
              ["virtio_pci" "virtio_blk"];
            message = "embedded target '${host}' must include virtio-blk drivers so the installed system boots in Quickemu";
          })
          targetsForSystem;

        environment.etc."nix-config/offline-install-manifest.json".source = offlineInstallManifest;
        environment.etc."nix-config/installer.conf".source = installerConfigTemplate;
        environment.systemPackages = [
          installerPackages.install
          installProfile
          installRazy
          installSpacy
          installInteractive
          editInstallConfig
          installProfileDesktop
          editInstallConfigDesktop
          installInteractiveDesktop
          installProfileAutostart
        ];
        isoImage.storeContents = lib.concatMap (target: [
          target.config.system.build.toplevel
        ]) (lib.attrValues targetsForSystem);
      }
    ];
  };
in {
  gnome-iso = isoConfig.config.system.build.isoImage;
}
