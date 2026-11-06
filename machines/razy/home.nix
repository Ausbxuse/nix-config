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

  # LESLEY's model (Valkrie on timy): Gemma 4 26B, always loaded. Only on the
  # tailnet address, timy's one way into razy (the Headscale policy). Two
  # conversations cached at once, her window and a call, 64k each of the 128k;
  # her lesley.env on timy compacts at 56k, under Ollama's cut.
  services.ollama-agent = {
    enable = true;
    host = "100.64.0.1";
    port = 11434;
    contextLength = 131072;
    flashAttention = true;
    kvCacheType = "q8_0";
    parallel = 2;
    maxLoadedModels = 1;
    modelsDir = "${config.home.homeDirectory}/.local/state/ollama/models";
    idleKeepAlive = "-1";
    autoStart = true;
    # (Its preloader loads only when docked; the warm-up below always does.)
    preloadModel = "";
    stopOnBattery = false;
  };

  # At boot, wait for Tailscale to give razy its tailnet address (the only
  # one Ollama listens on; before it, the bind fails), then load Gemma as soon
  # as the server answers, so her first reply after a reboot is quick too.
  systemd.user.services.ollama = let
    cfg = config.services.ollama-agent;
    tailnet = pkgs.writeShellScript "ollama-wait-for-tailnet" ''
      for _ in $(${pkgs.coreutils}/bin/seq 300); do
        ${pkgs.iproute2}/bin/ip -4 -o addr show | ${pkgs.gnugrep}/bin/grep -q " ${cfg.host}/" && exit 0
        ${pkgs.coreutils}/bin/sleep 1
      done
      echo "no ${cfg.host} after 5 minutes" >&2
      exit 1
    '';
    warm = pkgs.writeShellScript "ollama-warm-gemma" ''
      url=http://${cfg.host}:${toString cfg.port}
      for _ in $(${pkgs.coreutils}/bin/seq 30); do
        ${pkgs.coreutils}/bin/sleep 2
        ${pkgs.curl}/bin/curl -s -m 2 "$url/" >/dev/null && break
      done
      exec ${pkgs.curl}/bin/curl -s -m 120 "$url/api/generate" \
        -d '{"model":"gemma4:26b","keep_alive":-1}' >/dev/null
    '';
  in {
    Unit.Description = lib.mkForce "Ollama (Gemma 4 for LESLEY on timy)";
    Service = {
      ExecStartPre = "${tailnet}";
      ExecStartPost = "${warm}";
      TimeoutStartSec = 480;
      RestartSec = lib.mkForce 10;
    };
  };

  # Server mode on AC (see razy-inhibit-lid-suspend-while-docked): never
  # suspend when idle while plugged in; on battery, as everywhere else.
  dconf.settings."org/gnome/settings-daemon/plugins/power".sleep-inactive-ac-type = lib.mkForce "nothing";

  programs.firefox.profiles.betterfox.extraConfig = lib.mkAfter ''
    user_pref("browser.uidensity", 1);
  '';
}
