{
  inputs = {
    grub2-theme.url = "github:vinceliuice/grub2-themes";
    minegrub.url = "github:Lxtharia/minegrub-theme";
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    codex-cli-nix = {
      url = "github:sadjow/codex-cli-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    claude-code-nix = {
      url = "github:sadjow/claude-code-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    herdr = {
      url = "github:herdrdev/herdr/v0.8.2";
      flake = false;
    };
    nixgl = {
      url = "github:nix-community/nixGL";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    home-manager = {
      url = "github:nix-community/home-manager";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nix-index-database = {
      url = "github:nix-community/nix-index-database";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    disko = {
      url = "github:nix-community/disko";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    sops-nix = {
      url = "github:Mic92/sops-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    # Stub by default; override with --override-input nix-secrets <path-or-url>
    # for real builds. See secrets/nix-secrets/README.md.
    nix-secrets = {
      url = "path:./secrets/nix-secrets";
      flake = true;
    };
    zsh-better-prompt = {
      url = "github:ausbxuse/zsh-better-prompt";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    de = {
      url = "github:ausbxuse/de";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = {nixpkgs, ...} @ inputs: let
    inherit (nixpkgs) lib;

    publicConst = import ./globals.nix;
    privateConstPath = inputs.nix-secrets + "/globals.nix";
    privateConst =
      if builtins.pathExists privateConstPath
      then import privateConstPath
      else {};
    const = lib.recursiveUpdate publicConst privateConst;
    adminAccessPath = inputs.nix-secrets + "/admin-access.nix";
    adminAccess =
      if builtins.pathExists adminAccessPath
      then import adminAccessPath
      else {};
    repo = import ./lib {
      inherit lib inputs nixpkgs const adminAccess;
    };
    nixosConfigurations =
      repo.mkNamedAttrs
      (host: host)
      (host: repo.mkNixosWithHome host)
      repo.nixosHosts;
    homeConfigurations =
      repo.mkNamedAttrs
      (host: "${repo.userFor host}@${host}")
      (host: repo.mkHome host)
      repo.homeHosts;
    # Offline images must never capture an overridden private nix-secrets
    # input. Build their targets from the checked-in public stub explicitly.
    publicNixSecretsOutputs = (import ./secrets/nix-secrets/flake.nix).outputs {};
    publicNixSecrets =
      publicNixSecretsOutputs
      // {
        outPath = ./secrets/nix-secrets;
        __toString = self: toString self.outPath;
      };
    publicInputs = inputs // {nix-secrets = publicNixSecrets;};
    publicRepo = import ./lib {
      inherit lib nixpkgs;
      inputs = publicInputs;
      const = publicConst;
      adminAccess = {};
    };
    offlineNixosConfigurations = {
      razy = publicRepo.mkNixosWithHome "razy";
      spacy = publicRepo.mkNixosWithHome "spacy";
    };
    # Keep install media out of the default package/app surfaces so common
    # flake queries do not pay for image evaluation.
    images = lib.optionalAttrs (builtins.elem "x86_64-linux" publicRepo.supportedSystems) {
      x86_64-linux = import ./isos {
        pkgs = publicRepo.pkgsFor "x86_64-linux";
        inputs = publicInputs;
        const = publicConst;
        hostDefs = publicRepo.hostDefs;
        offlineTargets = offlineNixosConfigurations;
      };
    };
    packages = repo.forEachSystem ({pkgs, ...}:
      import ./pkgs {
        inherit lib pkgs const;
        inherit (repo) hostDefs;
      });
    apps = repo.forEachSystem ({system, ...}:
      import ./apps.nix {
        packages = packages.${system};
      });
    checks = repo.forEachSystem ({
      system,
      pkgs,
    }: let
      systemPackages = packages.${system};
      installerNvimCheck =
        pkgs.runCommand "installer-nvim-offline" {
          nativeBuildInputs = [pkgs.coreutils systemPackages.nvim];
        } ''
          export XDG_CONFIG_HOME="$TMPDIR/config"
          export XDG_DATA_HOME="$TMPDIR/data"
          export XDG_STATE_HOME="$TMPDIR/state"
          export XDG_CACHE_HOME="$TMPDIR/cache"
          printf 'install_profile=auto\n' >"$TMPDIR/installer.conf"

          NIX_CONFIG_INSTALLER_EDITOR=1 \
            timeout 20 nvim --headless "$TMPDIR/installer.conf" \
              '+lua if vim.g.nix_config_installer_init_loaded ~= true then vim.cmd("cquit 1") end' \
              '+write' '+qall!'

          # The package also remains safe if invoked directly.  In particular,
          # it must never load the store-backed full init and ask vim.pack to
          # update its lock file or clone plugins on the live ISO.
          timeout 20 nvim --headless \
            '+lua if vim.g.nix_config_installer_init_loaded ~= true then vim.cmd("cquit 1") end' \
            '+qall!'

          test ! -e "$XDG_DATA_HOME/nvim/site/pack"
          test ! -e "$XDG_CONFIG_HOME/nvim"
          touch "$out"
        '';
      installerProfileTemplateCheck = let
        installProfile = systemPackages."install-profile";
        closureInfo = pkgs.closureInfo {rootPaths = [installProfile];};
      in
        pkgs.runCommand "installer-profile-template" {
          nativeBuildInputs = [pkgs.coreutils pkgs.gnugrep pkgs.gnused];
        } ''
          template="$(${pkgs.gnused}/bin/sed -n \
            "s/^readonly INSTALLER_CONFIG_TEMPLATE='\(.*\)'$/\1/p" \
            ${installProfile}/bin/install-profile)"
          editor="$(${pkgs.gnused}/bin/sed -n \
            "s/^readonly INSTALLER_EDITOR='\(.*\)'$/\1/p" \
            ${installProfile}/bin/install-profile)"
          test -f "$template"
          test -x "$editor"
          ${pkgs.gnugrep}/bin/grep -Fx "$template" ${closureInfo}/store-paths
          ${pkgs.gnugrep}/bin/grep -Fx "''${editor%/bin/nvim}" ${closureInfo}/store-paths
          ${pkgs.gnugrep}/bin/grep -Eq '^install_profile=' "$template"
          ${pkgs.gnugrep}/bin/grep -Fx 'copy_repo=yes' "$template"
          touch "$out"
        '';
      nvimPluginNames = [
        "auto-session"
        "bento.nvim"
        "blink.cmp"
        "blink-copilot"
        "cellular-automaton.nvim"
        "conform.nvim"
        "copilot.lua"
        "dropbar.nvim"
        "fzf-lua"
        "gitsigns.nvim"
        "indent-blankline.nvim"
        "lazydev.nvim"
        "markdown-preview.nvim"
        "nvim-bqf"
        "nvim-colorizer.lua"
        "nvim-dap"
        "nvim-dap-ui"
        "nvim-nio"
        "nvim-treesitter"
        "nvim-treesitter-context"
        "nvim-treesitter-textobjects"
        "plenary.nvim"
        "snappy.nvim"
        "symbols.nvim"
        "undotree"
        "vimtex"
        "yazi.nvim"
        "zk-nvim"
      ];
      nvimParserNames = [
        "bash"
        "c"
        "comment"
        "cpp"
        "diff"
        "html"
        "just"
        "lua"
        "luadoc"
        "markdown"
        "markdown_inline"
        "nix"
        "python"
        "query"
        "tmux"
        "tsx"
        "typescript"
        "vim"
        "vimdoc"
        "yaml"
        "zsh"
      ];
      nvimExecutableNames = ["fd" "rg" "yazi" "zk"];
      nvimOfflineTest = pkgs.writeText "nvim-offline-test.lua" ''
        local function check()
          assert(vim.g.nix_config_plugins_declarative == true, 'declarative Nix plugin mode is disabled')
          assert(vim.g.nix_config_plugins_validated == true, 'not every configured plugin was validated')
          assert(vim.g.nix_config_mutable_plugin_loaded == nil, 'a mutable plugin shadowed the Nix pack')

          local data_site = vim.fs.joinpath(vim.fn.stdpath 'data', 'site')
          assert(not vim.tbl_contains(vim.opt.runtimepath:get(), data_site), 'mutable data site is on runtimepath')
          for _, runtime_file in ipairs({
            'parser/nix.so',
            'queries/nix/highlights.scm',
          }) do
            local matches = vim.api.nvim_get_runtime_file(runtime_file, true)
            assert(#matches > 0, 'Nix runtime file is missing: ' .. runtime_file)
            assert(
              vim.startswith(matches[1], vim.fs.joinpath(data_site, 'pack', 'hm', 'start')),
              'mutable runtime file shadowed the Nix pack: ' .. matches[1]
            )
          end

          for _, name in ipairs(${builtins.toJSON nvimPluginNames}) do
            local matches = vim.fn.globpath(vim.o.packpath, 'pack/*/opt/' .. name, false, true)
            assert(#matches > 0, 'plugin is missing from the Nix pack: ' .. name)
          end

          for _, language in ipairs(${builtins.toJSON nvimParserNames}) do
            assert(vim.treesitter.language.add(language), 'Tree-sitter parser is missing: ' .. language)
          end

          for _, executable in ipairs(${builtins.toJSON nvimExecutableNames}) do
            assert(vim.fn.executable(executable) == 1, 'runtime executable is missing: ' .. executable)
          end
          assert(
            vim.env.NVIM_LUVIT_META_PATH
              and vim.uv.fs_stat(vim.fs.joinpath(vim.env.NVIM_LUVIT_META_PATH, 'luv.lua')),
            'lazydev luvit-meta library is missing from the Nix closure'
          )

          local expected_spellfile = vim.fs.joinpath(vim.fn.stdpath 'state', 'spell', 'en.utf-8.add')
          assert(vim.o.spellfile == expected_spellfile, 'spellfile is not stored in writable XDG state')
          vim.cmd 'spellgood ncfgspellprobe'
          assert(
            vim.tbl_contains(vim.fn.readfile(vim.o.spellfile), 'ncfgspellprobe'),
            'spellfile update was not persisted: ' .. vim.o.spellfile
          )

          local markdown_preview = vim.fn.globpath(
            vim.o.packpath,
            'pack/*/opt/markdown-preview.nvim/app/out/index.html',
            false,
            true
          )
          assert(#markdown_preview > 0, 'markdown-preview.nvim web assets were not built')
          assert(#vim.v.errmsg == 0, 'Neovim startup error: ' .. vim.v.errmsg)
        end

        local ok, err = xpcall(check, debug.traceback)
        if not ok then
          vim.api.nvim_err_writeln(err)
          vim.cmd 'cquit 1'
        end
      '';
      mkNvimDeclarativeCheck = host: let
        username = publicRepo.userFor host;
        homeConfig = offlineNixosConfigurations.${host}.config.home-manager.users.${username};
        home = homeConfig.home.activationPackage;
        nvim = homeConfig.programs.neovim.finalPackage;
      in
        pkgs.runCommand "${host}-nvim-declarative" {
          nativeBuildInputs = [pkgs.coreutils pkgs.findutils];
        } ''
          nvim_source="$(${pkgs.coreutils}/bin/readlink -f \
            ${home}/home-files/.config/nvim)"
          case "$nvim_source" in
            /nix/store/*) ;;
            *)
              echo "${host} Neovim config escapes the Home Manager closure: $nvim_source" >&2
              exit 1
              ;;
          esac
          test -f "$nvim_source/init.lua"

          mkdir -p \
            "$TMPDIR/config" \
            "$TMPDIR/data/nvim/site/pack" \
            "$TMPDIR/data/nvim/site/pack/core/opt/auto-session/plugin" \
            "$TMPDIR/data/nvim/site/parser" \
            "$TMPDIR/data/nvim/site/queries/nix" \
            "$TMPDIR/state/nvim/spell" \
            "$TMPDIR/cache" \
            "$TMPDIR/home"
          ln -s ${home}/home-files/.config/nvim "$TMPDIR/config/nvim"
          ln -s \
            ${home}/home-files/.local/share/nvim/site/pack/hm \
            "$TMPDIR/data/nvim/site/pack/hm"
          printf 'let g:nix_config_mutable_plugin_loaded = 1\n' \
            >"$TMPDIR/data/nvim/site/pack/core/opt/auto-session/plugin/shadow.vim"
          printf 'not a shared library\n' \
            >"$TMPDIR/data/nvim/site/parser/nix.so"
          printf '((identifier) @mutable-shadow)\n' \
            >"$TMPDIR/data/nvim/site/queries/nix/highlights.scm"
          find "$TMPDIR/data/nvim/site" -printf '%P %y\n' \
            | sort >"$TMPDIR/site-before"
          printf '{ pkgs, ... }: { environment.systemPackages = [ pkgs.git ]; }\n' \
            >"$TMPDIR/offline-test.nix"

          HOME="$TMPDIR/home" \
          XDG_CONFIG_HOME="$TMPDIR/config" \
          XDG_DATA_HOME="$TMPDIR/data" \
          XDG_STATE_HOME="$TMPDIR/state" \
          XDG_CACHE_HOME="$TMPDIR/cache" \
            timeout 60 ${nvim}/bin/nvim --headless "$TMPDIR/offline-test.nix" \
              '+lua dofile("${nvimOfflineTest}")' '+qall!'

          # A Nix build has no network access. Also prove that startup ignored
          # and did not mutate the seeded legacy plugins, parsers, or queries.
          find "$TMPDIR/data/nvim/site" -printf '%P %y\n' \
            | sort >"$TMPDIR/site-after"
          cmp "$TMPDIR/site-before" "$TMPDIR/site-after"
          touch "$out"
        '';
    in
      (import ./tests {inherit pkgs lib;})
      // lib.optionalAttrs (system == "x86_64-linux") {
        inherit (systemPackages) nvim;
        "installer-nvim-offline" = installerNvimCheck;
        "installer-profile-template" = installerProfileTemplateCheck;
        "razy-nvim-declarative" = mkNvimDeclarativeCheck "razy";
        "spacy-nvim-declarative" = mkNvimDeclarativeCheck "spacy";
      }
      // repo.mkChecks "home" (
        host: homeConfigurations."${repo.userFor host}@${host}".activationPackage
      ) (repo.hostsForSystem system repo.homeHosts)
      // repo.mkChecks "nixos" (
        host: nixosConfigurations.${host}.config.system.build.toplevel
      ) (repo.hostsForSystem system repo.nixosHosts));
  in {
    templates = import ./templates;

    devShells = repo.forEachSystem ({pkgs, ...}: {
      default = (import ./shell.nix {inherit pkgs;}).default;
    });

    inherit apps checks homeConfigurations images nixosConfigurations packages;
  };
}
