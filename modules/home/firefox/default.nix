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
        settings = {
          # Prefer a consistent browser identity over per-site workarounds.
          # UA spoofing broke Duolingo's cross-origin Google sign-in button.
          # This also disables other site interventions, but not SmartBlock.
          "extensions.webcompat.enable_interventions" = false;
        };
        extraConfig = builtins.readFile ./user.js;
      };
    };
  };
}
