{
  config,
  lib,
  pkgs,
  const,
  username,
  ...
}: {
  home = {
    inherit username;
    homeDirectory = "/home/${username}"; # avoids infinite recursion
    stateVersion = "24.05";
  };

  xdg = {
    enable = true;
    cacheHome = "${config.home.homeDirectory}/.cache";
    configHome = "${config.home.homeDirectory}/.config";
    dataHome = "${config.home.homeDirectory}/.local/share";
    stateHome = "${config.home.homeDirectory}/.local/state";
    # configFile."nix/nix.conf".text = ''
    #   substituters = https://cache.nixos.org https://cache.nixos-cuda.org https://ausbxuse.cachix.org https://nix-community.cachix.org
    #   trusted-public-keys = cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY= cache.nixos-cuda.org:74DUi4Ye579gUqzH4ziL9IyiJBlDpMRn9MBN8oNan9M= nix-community.cachix.org-1:mB9FSh9qf2dCimDSUo8Zy7bkq5CX+/rkCWyvRCYg3Fs=
    # ''; # FIXME: breaks installation bootstrap script
  };

  home.sessionVariables = {
    ANDROID_SDK_HOME = "${config.xdg.dataHome}/android-sdk";
    PULSE_COOKIE = "${config.xdg.stateHome}/pulse/cookie";
    XCURSOR_THEME = "capitaine-cursors-white";
    XCURSOR_SIZE = "24";
    NH_FLAKE = "${config.home.homeDirectory}/src/public/nix-config";
    MANPAGER = "nvim +Man!";
    NPM_PACKAGES = "${config.home.homeDirectory}/.local/share/npm";
    NODE_PATH = "$NPM_PACKAGES/lib/node_modules:$NODE_PATH";
    PATH = "${config.home.homeDirectory}/.nix-profile/bin:$PATH:$(du ${config.home.homeDirectory}/.local/bin/ | cut -f2 | paste -sd ':')";
    ZK_NOTEBOOK_DIR = "${config.home.homeDirectory}/Documents/Notes";
    NOTMUCH_CONFIG = "${config.xdg.configHome}/notmuch-config";
    WGETRC = "${config.xdg.configHome}/wget/wgetrc";
    INPUTRC = "${config.xdg.configHome}/shell/inputrc";
    MOZ_ENABLE_WAYLAND = "1";
    PASSWORD_STORE_DIR = "${config.xdg.dataHome}/password-store";
    #TMUX_TMPDIR = "$XDG_RUNTIME_DIR";
    CARGO_HOME = "${config.xdg.dataHome}/cargo";
    GOPATH = "${config.xdg.dataHome}/go";
    ANSIBLE_CONFIG = "${config.xdg.configHome}/ansible/ansible.cfg";
    SQLITE_HISTORY = "${config.xdg.dataHome}/sqlite_history";
    NPM_CONFIG_USERCONFIG = "${config.xdg.configHome}/npm/npmrc";
    NPM_CONFIG_CACHE = "${config.xdg.cacheHome}/npm";
    PYLINTHOME = "${config.xdg.dataHome}/pylint";
    RUSTUP_HOME = "${config.xdg.dataHome}/rustup";
    CONDARC = "${config.xdg.configHome}/conda/condarc";
    PYTHON_HISTORY = "${config.xdg.dataHome}/python_history";
    IPYTHONDIR = "${config.xdg.configHome}/ipython";
    DICS = "${config.xdg.dataHome}/stardict/dic/";
    LC_ALL = "en_US.UTF-8";
    LANGUAGE = "en_US.UTF-8";
    TERM = "xterm-256color";
  };

  home.file.".gdbinit".text = ''
    set auto-load safe-path /nix/store
  '';
}
