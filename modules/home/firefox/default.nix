{
  config,
  pkgs,
  ...
}: {
  # Keep terminal word-deletion muscle memory from closing browser tabs.
  # Firefox's native shortcut editor (about:keyboard) uses empty entries to unbind keys.
  home.file."${config.programs.firefox.configPath}/${config.programs.firefox.profiles.betterfox.path}/customKeys.json".text = builtins.toJSON {
    key_close = {};
  };

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
