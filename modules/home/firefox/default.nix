{
  config,
  pkgs,
  ...
}: {
  programs = {
    firefox = {
      enable = true;
      # Use the configured graphics wrapper on non-NixOS hosts.
      package = config.lib.nixGL.wrap pkgs.firefox;
      configPath = ".mozilla/firefox";
      profiles.betterfox = {
        extraConfig = builtins.readFile ./user.js;
      };
    };
  };
}
