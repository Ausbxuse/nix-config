{
  config,
  lib,
  pkgs,
  ...
}: let
  defaultLocalModel = "qwen3.6:27b";
in {
  imports = [
    ../../modules/home/gnome-tweaks.nix
    ../../modules/home/codex-local.nix
    ../../modules/home/ollama-agent.nix
  ];

  my.codexLocal = {
    enable = false;
    localModel = defaultLocalModel;
    models = [defaultLocalModel];
  };

  home.packages = [pkgs.wechat];

  services.ollama-agent = {
    enable = false;
    port = 11434;
    contextLength = 32768;
    flashAttention = true;
    kvCacheType = "q4_0";
    parallel = 1;
    maxLoadedModels = 1;
    idleKeepAlive = "30m";
    autoStart = false;
    preloadModel = "";
    stopOnBattery = false;
  };

  # Server mode on AC (see razy-inhibit-lid-suspend-while-docked): never
  # suspend when idle while plugged in; on battery, as everywhere else.
  dconf.settings."org/gnome/settings-daemon/plugins/power".sleep-inactive-ac-type = lib.mkForce "nothing";

  programs.firefox.profiles.betterfox.extraConfig = lib.mkAfter ''
    user_pref("browser.uidensity", 1);
  '';
}
