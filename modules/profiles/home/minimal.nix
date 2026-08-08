{
  config,
  pkgs,
  inputs,
  ...
}: {
  imports = [
    inputs.nix-index-database.homeModules.default
    ../../home/env
    ../../home/claude.nix
    ../../home/codex.nix
    ../../home/zsh
    ../../home/nvim
    ../../home/tmux
  ];

  xdg.configFile."nix/nix.conf".text = ''
    experimental-features = nix-command flakes
  '';

  programs.direnv = {
    enable = true;
    nix-direnv.enable = true;
  };
  # CLI helpers and nicer rebuild UX

  programs.command-not-found.enable = false;

  programs.nix-index = {
    enable = true;
    enableZshIntegration = true;
  };

  programs.nix-index-database.comma.enable = true;

  home.file."${config.xdg.cacheHome}/nix-index/files".force = true;

  home.packages = with pkgs; [
    nh
    nvd
    nix-output-monitor
  ];
  #    - Point your just recipes at nh os switch -- --flake .#${HOST} (and nh home switch -- --flake .#${USER}@${HOST}) so you get nom logs and nice diffs automatically.
  #  - Use nvd diff /run/current-system ./result (or nh os diff) before switching to catch surprises.
  #  - With nix-index + comma, you can run commands you don’t have installed as , <cmd> and they’re fetched on the fly.
  #
}
