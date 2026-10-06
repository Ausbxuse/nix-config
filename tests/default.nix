{
  pkgs,
  lib,
}: let
  const = import ../globals.nix;
  hostDefsJson = pkgs.writeText "host-defs.json" (
    builtins.toJSON (import ../machines/defs.nix {
      inherit lib const;
    })
  );
  emptyOfflineManifest = pkgs.writeText "offline-install-manifest.json" (builtins.toJSON {
    schemaVersion = 1;
    strict = false;
    targets = {};
  });
  mkInstallScript = name: offlineInstallManifest:
    pkgs.writeText name (
      lib.replaceStrings
      ["@source_lib@" "@repoSource@" "@hostDefsFile@" "@username@" "@offlineInstallManifest@"]
      [
        (builtins.readFile ../scripts/lib.sh)
        (toString ../.)
        (toString hostDefsJson)
        "zhenyu"
        (toString offlineInstallManifest)
      ]
      (builtins.readFile ../scripts/install-flake.sh)
    );
  installScript = mkInstallScript "install-flake-test.sh" emptyOfflineManifest;
  profileTestConfig = pkgs.writeText "installer-profile-test.conf" ''
    install_profile=auto
    install_source=offline
    edit_before_install=no
    disk=prompt
    copy_repo=yes
    skip_partitioning=yes
    dry_run=yes
    color=no
    assume_yes=no
  '';
  profileTestInjectionConfig = pkgs.writeText "installer-profile-injection-test.conf" ''
    install_profile=spacy
    install_source=online
    edit_before_install=no
    name=$(${pkgs.coreutils}/bin/touch /build/installer-profile-parser-executed)
  '';
  profileTestInvalidConfig = pkgs.writeText "installer-profile-invalid-test.conf" ''
    install_profile=auto
    unknown_key=yes
  '';
  profileTestManifest = pkgs.writeText "installer-profile-manifest-test.json" (builtins.toJSON {
    schemaVersion = 1;
    strict = true;
    system = "x86_64-linux";
    targets = {
      razy = {};
      spacy = {};
    };
  });
  profileTestScript = pkgs.writeText "install-profile-test.sh" (
    lib.replaceStrings
    ["@source_lib@" "@installerConfigTemplate@" "@offlineInstallManifest@" "@installerEditor@"]
    [
      (builtins.readFile ../scripts/lib.sh)
      (toString ../isos/installer.conf)
      (toString profileTestManifest)
      "${profileTestNvim}/bin/nvim"
    ]
    (builtins.readFile ../scripts/install-profile.sh)
  );
  profileTestInstaller = pkgs.writeShellScriptBin "install-config" ''
    set -euo pipefail
    : "''${INSTALLER_TEST_OUTPUT:?}"
    printf '%s\n' "$@" >"$INSTALLER_TEST_OUTPUT"
  '';
  profileTestNvim = pkgs.writeShellScriptBin "nvim" ''
    set -euo pipefail
    : "''${INSTALLER_TEST_OUTPUT:?}"
    printf '%s\n' "$@" >"$INSTALLER_TEST_OUTPUT"
  '';
  installerProfileTest =
    pkgs.runCommand "installer-profile" {
      nativeBuildInputs = [
        pkgs.bash
        pkgs.coreutils
        pkgs.gnugrep
        pkgs.jq
        pkgs.util-linux
        profileTestInstaller
        profileTestNvim
      ];
    } ''
      mkdir -p "$out"
      mkdir -p "$out/pci-nvidia/0000:01:00.0" "$out/pci-no-nvidia"
      printf '0x10de\n' >"$out/pci-nvidia/0000:01:00.0/vendor"
      printf '0x030000\n' >"$out/pci-nvidia/0000:01:00.0/class"

      printf '\n' | INSTALLER_TEST_OUTPUT="$out/razy-args" \
        NIXOS_INSTALLER_CONFIG=${profileTestConfig} \
        NIXOS_INSTALLER_PCI_ROOT="$out/pci-nvidia" \
        script -qefc "bash ${profileTestScript}" "$out/razy-terminal"
      grep -F "Use the 'razy' install profile?" "$out/razy-terminal"
      grep -Fx -- '--host' "$out/razy-args"
      grep -Fx -- 'razy' "$out/razy-args"
      grep -Fx -- '--offline' "$out/razy-args"
      grep -Fx -- '--ask-disk' "$out/razy-args"
      grep -Fx -- '--skip-partitioning' "$out/razy-args"
      grep -Fx -- '--dry-run' "$out/razy-args"
      grep -Fx -- '--no-color' "$out/razy-args"
      grep -Fx -- '--copy-repo' "$out/razy-args"
      grep -Fx -- 'yes' "$out/razy-args"

      printf '\n' | INSTALLER_TEST_OUTPUT="$out/spacy-args" \
        NIXOS_INSTALLER_CONFIG=${profileTestConfig} \
        NIXOS_INSTALLER_PCI_ROOT="$out/pci-no-nvidia" \
        script -qefc "bash ${profileTestScript}" "$out/spacy-terminal"
      grep -F "Use the 'spacy' install profile?" "$out/spacy-terminal"
      grep -Fx -- '--host' "$out/spacy-args"
      grep -Fx -- 'spacy' "$out/spacy-args"
      grep -Fx -- '--offline' "$out/spacy-args"

      INSTALLER_TEST_OUTPUT="$out/edit-args" \
        NIXOS_INSTALLER_CONFIG=${profileTestConfig} \
        bash ${profileTestScript} --edit
      sed -n '1p' "$out/edit-args" | grep -Fx '${profileTestConfig}'

      INSTALLER_TEST_OUTPUT="$out/interactive-args" \
        bash ${profileTestScript} --interactive --dry-run --host razy
      grep -Fx -- '--dry-run' "$out/interactive-args"
      grep -Fx -- '--host' "$out/interactive-args"
      grep -Fx -- 'razy' "$out/interactive-args"

      printf '\n' | INSTALLER_TEST_OUTPUT="$out/injection-args" \
        NIXOS_INSTALLER_CONFIG=${profileTestInjectionConfig} \
        NIXOS_INSTALLER_PCI_ROOT="$out/pci-no-nvidia" \
        script -qefc "bash ${profileTestScript}" "$out/injection-terminal"
      test ! -e /build/installer-profile-parser-executed
      grep -Fx '$(${pkgs.coreutils}/bin/touch /build/installer-profile-parser-executed)' \
        "$out/injection-args"

      if INSTALLER_TEST_OUTPUT="$out/invalid-args" \
        NIXOS_INSTALLER_CONFIG=${profileTestInvalidConfig} \
        bash ${profileTestScript} >"$out/invalid.out" 2>"$out/invalid.err"; then
        echo "invalid installer option unexpectedly succeeded" >&2
        exit 1
      fi
      grep -F "unknown option 'unknown_key'" "$out/invalid.err"

      for key in \
        install_profile install_source edit_before_install disk copy_repo \
        repo_dest skip_partitioning dry_run color assume_yes system username \
        name email nixos home nixos_profile home_profile display_profile \
        install_layout swap_size caps_remap portable; do
        grep -Eq "^$key=" ${../isos/installer.conf}
      done
    '';
  repoSource = builtins.path {
    path = ../.;
    name = "nix-config-shellcheck-source";
  };
  shellScripts = [
    "scripts/admit-host.sh"
    "scripts/enroll.sh"
    "scripts/install-gnome-resume-background-test.sh"
    "scripts/install-flake.sh"
    "scripts/install-profile.sh"
    "scripts/install_caps_ubuntu.sh"
    "scripts/lib.sh"
    "scripts/recovery-backup.sh"
    "scripts/setup-recovery-usb.sh"
    "scripts/validate-host.sh"
    "scripts/watt.sh"
    "tests/run-nixos-system-install.sh"
    "tests/run-ubuntu-home-install.sh"
  ];
  shellcheck = pkgs.runCommand "shellcheck" {nativeBuildInputs = [pkgs.shellcheck];} ''
    export LC_ALL=C.UTF-8
    mkdir normalized
    for rel in ${lib.escapeShellArgs shellScripts}; do
      src="${repoSource}/$rel"
      dst="normalized/$(basename "$rel")"
      sed 's|@source_lib@|. ${repoSource}/scripts/lib.sh|' "$src" >"$dst"
      shellcheck -x -s bash "$dst"
    done
    touch "$out"
  '';

  mkTest = {
    name,
    modules,
    testScript,
  }:
    pkgs.testers.nixosTest {
      inherit name testScript;
      nodes.machine = {config, ...}: {
        imports = modules;
        system.stateVersion = "24.05";
        networking.hostName = name;
      };
    };

  mkcustomHomeInstallTest = {
    name,
    systemOverride ? null,
    homeProfile ? "personal-gnome",
    displayProfile ? "gnome-default",
  }:
    mkTest {
      inherit name;
      modules = [
        ({pkgs, ...}: {
          environment.systemPackages = with pkgs; [
            bash
            git
            jq
            rsync
            gnugrep
            gnused
            gawk
            perl
            util-linux
          ];

          # Simulate a generic Linux target rather than relying on NixOS-specific host identity.
          environment.etc."os-release".text = ''
            NAME="Ubuntu"
            ID=ubuntu
            PRETTY_NAME="Ubuntu 24.04"
          '';
        })
      ];
      testScript = let
        systemArgs =
          if systemOverride == null
          then ""
          else "          --system ${systemOverride} \\\n";
        displayArgs =
          if displayProfile == null
          then ""
          else "          --display-profile ${displayProfile} \\\n";
        systemAsserts =
          if systemOverride == null
          then ""
          else "        grep -F 'system = \"${systemOverride}\";' /tmp/test-artifacts/defs.nix\n";
        displayAsserts =
          if displayProfile == null
          then ""
          else "        grep -F 'displayProfile = \"${displayProfile}\";' /tmp/test-artifacts/defs.nix\n";
      in
        lib.concatStringsSep "\n" [
          ''machine.wait_for_unit("multi-user.target")''
          (
            ''
              machine.succeed("""
                mkdir -p /tmp/fakebin /tmp/test-artifacts
                cat >/tmp/fakebin/nix <<'EOF'
                #!/usr/bin/env bash
                set -euo pipefail
                printf '%s\n' "$@" > /tmp/test-artifacts/home-nix-args
                for arg in "$@"; do
                  case "$arg" in
                    *'#zhenyu@${name}')
                      test -d "$HOME/src/public/nix-config/modules/home/nvim/nvim"
                      test -f "$HOME/src/public/nix-config/modules/profiles/home/minimal.nix"
                      worktree="''${arg%%#*}"
                      cp "$worktree/machines/defs.nix" /tmp/test-artifacts/defs.nix
                      ;;
                  esac
                done
                exit 0
                EOF

                cat >/tmp/fakebin/sudo <<'EOF'
                #!/usr/bin/env bash
                echo "sudo should not be used in home-only mode" >&2
                exit 99
                EOF

                cat >/tmp/fakebin/disko <<'EOF'
                #!/usr/bin/env bash
                echo "disko should not be used in home-only mode" >&2
                exit 99
                EOF

                cat >/tmp/fakebin/nixos-generate-config <<'EOF'
                #!/usr/bin/env bash
                echo "nixos-generate-config should not be used in home-only mode" >&2
                exit 99
                EOF

                cat >/tmp/fakebin/nixos-install <<'EOF'
                #!/usr/bin/env bash
                echo "nixos-install should not be used in home-only mode" >&2
                exit 99
                EOF

                chmod +x /tmp/fakebin/sudo /tmp/fakebin/disko /tmp/fakebin/nixos-generate-config /tmp/fakebin/nixos-install
                chmod +x /tmp/fakebin/nix

                PATH=/tmp/fakebin:$PATH ${pkgs.bash}/bin/bash ${installScript} \
                  --host ${name} \
                  --name 'Test User' \
                  --email 'test@example.com' \
            ''
            + systemArgs
            + ''
              --home \
              --home-profile ${homeProfile} \
              --caps-remap no \
            ''
            + displayArgs
            + ''

              test -f /tmp/test-artifacts/defs.nix
              grep -F '${name} = {' /tmp/test-artifacts/defs.nix
              grep -F 'username = "zhenyu";' /tmp/test-artifacts/defs.nix
              grep -F 'profile = "${homeProfile}";' /tmp/test-artifacts/defs.nix
            ''
            + displayAsserts
            + systemAsserts
            + ''
                test -d "$HOME/src/public/nix-config/modules/home/nvim/nvim"
                test -f "$HOME/src/public/nix-config/modules/profiles/home/minimal.nix"
                grep -F '#zhenyu@${name}' /tmp/test-artifacts/home-nix-args
                grep -F 'ID=ubuntu' /etc/os-release
              """)
            ''
          )
        ];
    };

  mkcustomNixosInstallTest = {
    name,
    systemOverride ? null,
    nixosProfile ? "portable-gnome",
  }:
    mkTest {
      inherit name;
      modules = [
        ({pkgs, ...}: {
          environment.systemPackages = with pkgs; [
            bash
            git
            jq
            rsync
            gnugrep
            gnused
            gawk
            perl
            util-linux
            coreutils
          ];
        })
      ];
      testScript = let
        systemArgs =
          if systemOverride == null
          then ""
          else "          --system ${systemOverride} \\\n";
        systemAsserts =
          if systemOverride == null
          then ""
          else "        grep -F 'system = \"${systemOverride}\";' /tmp/test-artifacts/defs.nix\n";
      in
        lib.concatStringsSep "\n" [
          ''machine.wait_for_unit("multi-user.target")''
          (
            ''
              machine.succeed("""
                mkdir -p /tmp/fakebin /tmp/test-artifacts /mnt/etc/nixos

                cat >/tmp/fakebin/sudo <<'EOF'
                #!/usr/bin/env bash
                exec "$@"
                EOF

                cat >/tmp/fakebin/disko <<'EOF'
                #!/usr/bin/env bash
                set -euo pipefail
                printf '%s\n' "$@" > /tmp/test-artifacts/disko-args
                exit 0
                EOF

                cat >/tmp/fakebin/nixos-generate-config <<'EOF'
                #!/usr/bin/env bash
                set -euo pipefail
                mkdir -p /mnt/etc/nixos
                cat >/mnt/etc/nixos/hardware-configuration.nix <<'EOC'
                { ... }: { boot.loader.grub.enable = false; }
                EOC
                EOF

                cat >/tmp/fakebin/nixos-install <<'EOF'
                #!/usr/bin/env bash
                set -euo pipefail
                printf '%s\n' "$@" > /tmp/test-artifacts/nixos-install-args
                cp "$PWD/machines/defs.nix" /tmp/test-artifacts/defs.nix
                cp "$PWD/machines/${name}/hardware-configuration.nix" /tmp/test-artifacts/hardware-configuration.nix
                exit 0
                EOF

                chmod +x /tmp/fakebin/sudo /tmp/fakebin/disko /tmp/fakebin/nixos-generate-config /tmp/fakebin/nixos-install

                printf 'secret-pass\n' | PATH=/tmp/fakebin:$PATH ${pkgs.bash}/bin/bash ${installScript} \
                  --host ${name} \
                  --name 'Test User' \
                  --email 'test@example.com' \
            ''
            + systemArgs
            + ''
                --nixos \
                --disk /dev/vda \
                --nixos-profile ${nixosProfile} \
                --swap-size 8G \
                --copy-repo no \
                --yes

              test -f /tmp/test-artifacts/defs.nix
              test -f /tmp/test-artifacts/hardware-configuration.nix
              grep -F '${name} = {' /tmp/test-artifacts/defs.nix
              grep -F 'profile = "${nixosProfile}";' /tmp/test-artifacts/defs.nix
              grep -F 'layout = "luks-btrfs";' /tmp/test-artifacts/defs.nix
              grep -F 'disk = "/dev/vda";' /tmp/test-artifacts/defs.nix
              grep -F 'swapSize = "8G";' /tmp/test-artifacts/defs.nix
            ''
            + systemAsserts
            + ''
                grep -F '.#${name}' /tmp/test-artifacts/disko-args
                grep -F '.#${name}' /tmp/test-artifacts/nixos-install-args
              """)
            ''
          )
        ];
    };

  offlineToplevel = pkgs.runCommand "offline-razy-toplevel-test" {} ''
    mkdir -p "$out"
  '';
  offlineDiskoScript = pkgs.writeShellScript "offline-razy-disko-test" ''
    printf 'offline Disko should be skipped by this fixture\n' >&2
    exit 99
  '';
  offlineClosureInfo = pkgs.runCommand "offline-razy-closure-info-test" {} ''
    mkdir -p "$out"
    printf '%s\n' ${offlineToplevel} >"$out/store-paths"
    : >"$out/registration"
  '';
  offlineRazyManifest = pkgs.writeText "offline-razy-manifest-test.json" (builtins.toJSON {
    schemaVersion = 1;
    strict = true;
    system = "x86_64-linux";
    targets.razy = {
      system = "x86_64-linux";
      username = "zhenyu";
      name = const.name;
      email = const.email;
      nixosEnabled = "yes";
      homeEnabled = "yes";
      nixosProfile = "portable-nvidia-gnome";
      homeProfile = "personal-gnome";
      displayProfile = "laptop-2_5k";
      installLayout = "luks-btrfs";
      swapSize = "31G";
      toplevel = toString offlineToplevel;
      closureInfo = toString offlineClosureInfo;
      diskoScript = toString offlineDiskoScript;
      diskAlias = "/run/nix-config-installer/razy-target-disk";
    };
  });
  offlineInstallScript = mkInstallScript "install-flake-offline-test.sh" offlineRazyManifest;
in {
  inherit shellcheck;

  "desktop-integration" =
    pkgs.runCommand "desktop-integration-test" {
      nativeBuildInputs = with pkgs; [
        bash
        coreutils
        gnugrep
        gnused
        neovim-unwrapped
        procps
        python3
        tmux
        zsh
      ];
    } ''
      python ${repoSource}/tests/test-desktop-integration.py
      touch "$out"
    '';

  "installer-profile" = installerProfileTest;
  "installer-prefetch" = pkgs.runCommand "installer-prefetch-test" {
    nativeBuildInputs = [pkgs.python3];
  } ''
    python ${repoSource}/tests/test-installer-prefetch.py
    touch "$out"
  '';

  "custom-home-install" = mkcustomHomeInstallTest {
    name = "custom-home";
  };

  "custom-home-install-aarch64" = mkcustomHomeInstallTest {
    name = "custom-home-aarch64";
    systemOverride = "aarch64-linux";
  };

  "custom-nixos-install" = mkcustomNixosInstallTest {
    name = "custom-nixos";
  };

  "custom-nixos-install-aarch64" = mkcustomNixosInstallTest {
    name = "custom-nixos-aarch64";
    systemOverride = "aarch64-linux";
    nixosProfile = "minimal";
  };

  "installer-store-extract" = pkgs.runCommand "installer-store-extract" {
    nativeBuildInputs = with pkgs; [bash coreutils diffutils findutils gnused nix squashfsTools xcp];
  } ''
    # Load the actual installer functions without starting its interactive main.
    source <(sed '$d' ${installScript})
    sudo() { "$@"; }
    export NIX_CONFIG="experimental-features = nix-command"
    OFFLINE_CLOSURE_INFO="$PWD/closure"
    mkdir -p "$OFFLINE_CLOSURE_INFO" tree/pkg/subdir tree/unselected target
    printf 'payload\n' >tree/pkg/subdir/data
    printf '#!/bin/sh\nexit 0\n' >tree/pkg/executable
    chmod 0555 tree/pkg/executable
    ln tree/pkg/subdir/data tree/pkg/hardlink
    ln -s subdir/data tree/pkg/symlink
    ln -s pkg tree/root-link
    ln -s /nix/store/pkg/executable tree/absolute-link
    touch tree/unselected/excluded
    printf '/nix/store/pkg\n/nix/store/root-link\n/nix/store/absolute-link\n' >"$OFFLINE_CLOSURE_INFO/store-paths"
    mksquashfs tree image.squashfs -noappend -no-progress -processors 1 -comp zstd
    copy_offline_store "$PWD/image.squashfs" "$PWD/target"
    test ! -e target/unselected
    test "$(readlink target/root-link)" = pkg
    test "$(readlink target/absolute-link)" = /nix/store/pkg/executable
    test "$(stat -c %i target/pkg/hardlink)" = "$(stat -c %i target/pkg/subdir/data)"
    test "$(nix hash path tree/pkg)" = "$(nix hash path target/pkg)"

    printf '/nix/store/missing\n' >"$OFFLINE_CLOSURE_INFO/store-paths"
    if copy_offline_store "$PWD/image.squashfs" "$PWD/missing-target"; then
      echo 'missing image path was silently accepted' >&2
      exit 1
    fi
    : >"$OFFLINE_CLOSURE_INFO/store-paths"
    if copy_offline_store "$PWD/image.squashfs" "$PWD/empty-target"; then
      echo 'empty closure list was silently accepted' >&2
      exit 1
    fi
    printf '/nix/store/pkg\n' >"$OFFLINE_CLOSURE_INFO/store-paths"
    printf 'not squashfs' >corrupt.squashfs
    if copy_offline_store "$PWD/corrupt.squashfs" "$PWD/corrupt-target"; then
      echo 'corrupt image was silently accepted' >&2
      exit 1
    fi

    # Offline installs launched outside the live ISO retain the mounted-store path.
    printf '%s\n' "$PWD/tree/pkg" >"$OFFLINE_CLOSURE_INFO/store-paths"
    mkdir fallback-target
    copy_offline_store "$PWD/no-image" "$PWD/fallback-target"
    test "$(nix hash path tree/pkg)" = "$(nix hash path fallback-target/pkg)"
    touch "$out"
  '';

  "razy-offline-install" = mkTest {
    name = "razy-offline-install";
    modules = [
      ({pkgs, ...}: {
        environment.systemPackages = with pkgs; [
          bash
          coreutils
          findutils
          gawk
          git
          gnugrep
          gnused
          jq
          perl
          rsync
          util-linux
        ];
      })
    ];
    testScript = lib.concatStringsSep "\n" [
      ''machine.wait_for_unit("multi-user.target")''
      ''
        machine.succeed("""
          mkdir -p /tmp/fakebin /tmp/test-artifacts /mnt/etc/nixos

          cat >/tmp/fakebin/sudo <<'EOF'
          #!/usr/bin/env bash
          exec "$@"
          EOF

          cat >/tmp/fakebin/mountpoint <<'EOF'
          #!/usr/bin/env bash
          exit 0
          EOF

          cat >/tmp/fakebin/xcp <<'EOF'
          #!/usr/bin/env bash
          set -euo pipefail
          printf '%s\n' "$@" > /tmp/test-artifacts/xcp-args
          EOF

          cat >/tmp/fakebin/nix-store <<'EOF'
          #!/usr/bin/env bash
          set -euo pipefail
          printf '%s\n' "$@" > /tmp/test-artifacts/nix-store-args
          cat >/dev/null
          EOF

          cat >/tmp/fakebin/nix <<'EOF'
          #!/usr/bin/env bash
          printf 'offline install attempted Nix evaluation or a build\n' >&2
          exit 99
          EOF

          cat >/tmp/fakebin/nixos-generate-config <<'EOF'
          #!/usr/bin/env bash
          set -euo pipefail
          mkdir -p /mnt/etc/nixos
          printf '{ ... }: {}\n' >/mnt/etc/nixos/hardware-configuration.nix
          EOF

          cat >/tmp/fakebin/nixos-install <<'EOF'
          #!/usr/bin/env bash
          set -euo pipefail
          printf '%s\n' "$@" > /tmp/test-artifacts/nixos-install-args
          EOF

          cat >/tmp/fakebin/disko <<'EOF'
          #!/usr/bin/env bash
          printf 'network Disko path was used\n' >&2
          exit 99
          EOF

          chmod +x /tmp/fakebin/*

          PATH=/tmp/fakebin:$PATH ${pkgs.bash}/bin/bash ${offlineInstallScript} \
            --host razy \
            --disk /dev/vda \
            --offline \
            --skip-partitioning \
            --copy-repo no \
            --yes \
            > /tmp/test-artifacts/install-output 2>&1

          grep -Fx -- '--recursive' /tmp/test-artifacts/xcp-args
          grep -Fx -- '--load-db' /tmp/test-artifacts/nix-store-args
          grep -Fx -- '--system' /tmp/test-artifacts/nixos-install-args
          grep -Fx '${offlineToplevel}' /tmp/test-artifacts/nixos-install-args
          grep -Fx -- 'builders' /tmp/test-artifacts/nixos-install-args
          grep -Fx -- 'substitute' /tmp/test-artifacts/nixos-install-args
          grep -Fx -- 'false' /tmp/test-artifacts/nixos-install-args
          grep -F '==> installation time' /tmp/test-artifacts/install-output
          grep -F 'total' /tmp/test-artifacts/install-output
          grep -F 'Power off this live installer: sudo poweroff' /tmp/test-artifacts/install-output
          grep -F 'Boot from /dev/vda and enter the LUKS passphrase' /tmp/test-artifacts/install-output
          if grep -Fx -- '--flake' /tmp/test-artifacts/nixos-install-args; then
            printf 'offline install unexpectedly used --flake\n' >&2
            exit 1
          fi
        """)
      ''
    ];
  };

  "custom-nixos-install-reports-errors" = mkTest {
    name = "custom-nixos-error";
    modules = [
      ({pkgs, ...}: {
        environment.systemPackages = with pkgs; [
          bash
          git
          jq
          rsync
          gnugrep
          gnused
          gawk
          perl
          util-linux
          coreutils
        ];
      })
    ];
    testScript = lib.concatStringsSep "\n" [
      ''machine.wait_for_unit("multi-user.target")''
      ''
        machine.succeed("""
          mkdir -p /tmp/fakebin /tmp/test-artifacts /mnt/etc/nixos

          cat >/tmp/fakebin/sudo <<'EOF'
          #!/usr/bin/env bash
          exec "$@"
          EOF

          cat >/tmp/fakebin/disko <<'EOF'
          #!/usr/bin/env bash
          set -euo pipefail
          exit 0
          EOF

          cat >/tmp/fakebin/nixos-generate-config <<'EOF'
          #!/usr/bin/env bash
          set -euo pipefail
          mkdir -p /mnt/etc/nixos
          cat >/mnt/etc/nixos/hardware-configuration.nix <<'EOC'
          { ... }: { boot.loader.grub.enable = false; }
          EOC
          EOF

          cat >/tmp/fakebin/nixos-install <<'EOF'
          #!/usr/bin/env bash
          set -euo pipefail
          echo 'installing the boot loader...'
          echo 'ERROR: mkdir /var/lock/dmraid'
          exit 0
          EOF

          chmod +x /tmp/fakebin/sudo /tmp/fakebin/disko /tmp/fakebin/nixos-generate-config /tmp/fakebin/nixos-install

          if printf 'secret-pass\n' | PATH=/tmp/fakebin:$PATH ${pkgs.bash}/bin/bash ${installScript} \
            --host custom-nixos-error \
            --name 'Test User' \
            --email 'test@example.com' \
            --nixos \
            --disk /dev/vda \
            --nixos-profile minimal \
            --swap-size 8G \
            --copy-repo no \
            --yes \
            >/tmp/test-artifacts/install.out 2>/tmp/test-artifacts/install.err; then
            echo 'installer unexpectedly succeeded' >&2
            exit 1
          fi

          grep -F 'ERROR: mkdir /var/lock/dmraid' /tmp/test-artifacts/install.err
          grep -F 'nixos-install reported an installation error' /tmp/test-artifacts/install.err
        """)
      ''
    ];
  };

  "custom-nixos-install-copies-git-repo" = mkTest {
    name = "custom-nixos-copy-repo";
    modules = [
      ({pkgs, ...}: {
        environment.systemPackages = with pkgs; [
          bash
          git
          jq
          rsync
          gnugrep
          gnused
          gawk
          perl
          util-linux
          coreutils
        ];
      })
    ];
    testScript = lib.concatStringsSep "\n" [
      ''machine.wait_for_unit("multi-user.target")''
      ''
        machine.succeed("""
          mkdir -p /tmp/fakebin /tmp/test-artifacts /mnt/etc/nixos /tmp/source-repo
          cp -r ${../.}/. /tmp/source-repo/
          chmod -R u+w /tmp/source-repo

          cd /tmp/source-repo
          git init
          git config user.name 'Fixture User'
          git config user.email 'fixture@example.com'
          git add .
          git commit -m 'fixture'
          git rev-parse HEAD >/tmp/test-artifacts/source-head
          echo '# dirty source file' >> TODO.md

          cat >/tmp/fakebin/sudo <<'EOF'
          #!/usr/bin/env bash
          exec "$@"
          EOF

          cat >/tmp/fakebin/disko <<'EOF'
          #!/usr/bin/env bash
          set -euo pipefail
          exit 0
          EOF

          cat >/tmp/fakebin/nixos-generate-config <<'EOF'
          #!/usr/bin/env bash
          set -euo pipefail
          mkdir -p /mnt/etc/nixos
          cat >/mnt/etc/nixos/hardware-configuration.nix <<'EOC'
          { ... }: { boot.loader.grub.enable = false; }
          EOC
          EOF

          cat >/tmp/fakebin/nixos-install <<'EOF'
          #!/usr/bin/env bash
          set -euo pipefail
          exit 0
          EOF

          cat >/tmp/fakebin/nixos-enter <<'EOF'
          #!/usr/bin/env bash
          set -euo pipefail
          if [ "$1" = "--root" ]; then
            shift 2
          fi
          if [ "$1" = "-c" ]; then
            shift
            eval "$1"
            exit 0
          fi
          exec "$@"
          EOF

          chmod +x /tmp/fakebin/sudo /tmp/fakebin/disko /tmp/fakebin/nixos-generate-config /tmp/fakebin/nixos-install /tmp/fakebin/nixos-enter

          PATH=/tmp/fakebin:$PATH ${pkgs.bash}/bin/bash ${installScript} \
            --host custom-nixos-copy-repo \
            --name 'Test User' \
            --email 'test@example.com' \
            --nixos \
            --disk /dev/vda \
            --nixos-profile minimal \
            --swap-size 8G \
            --copy-repo yes \
            --yes

          TARGET_REPO=/mnt/home/zhenyu/src/public/nix-config
          test -d "$TARGET_REPO/.git"
          test "$(cat /tmp/test-artifacts/source-head)" = "$(git -C "$TARGET_REPO" rev-parse HEAD)"
          grep -F 'custom-nixos-copy-repo = {' "$TARGET_REPO/machines/defs.nix"
          grep -F 'name = "Test User";' "$TARGET_REPO/globals.nix"
          test -f "$TARGET_REPO/machines/custom-nixos-copy-repo/hardware-configuration.nix"

          git -C "$TARGET_REPO" status --short >/tmp/test-artifacts/target-status
          grep -F ' M globals.nix' /tmp/test-artifacts/target-status
          grep -F ' M machines/defs.nix' /tmp/test-artifacts/target-status
          grep -F '?? machines/custom-nixos-copy-repo/' /tmp/test-artifacts/target-status
          if grep -F 'TODO.md' /tmp/test-artifacts/target-status; then
            echo 'source dirty files leaked into target clone' >&2
            exit 1
          fi
        """)
      ''
    ];
  };
}
