{
  config,
  lib,
  inputs,
  hostDef,
  pkgs,
  ...
}: let
  useNixGL =
    pkgs.stdenv.hostPlatform.isLinux
    && pkgs.stdenv.hostPlatform.isx86_64
    && !(hostDef.nixos.enable or false);
  nixGLMesa =
    (import inputs.nixgl {
      inherit pkgs;
      enable32bits = false;
    }).nixGLMesa;
  # These paths belong to Ghostty's GTK/nixGL runtime, not to the programs
  # launched from its shell. Preserve unset versus empty host values too.
  runtimeEnvironment = [
    "GI_TYPELIB_PATH"
    "GIO_EXTRA_MODULES"
    "GDK_PIXBUF_MODULE_FILE"
    "GST_PLUGIN_SYSTEM_PATH_1_0"
    "LD_LIBRARY_PATH"
    "LD_PRELOAD"
    "LIBGL_DRIVERS_PATH"
    "LIBVA_DRIVERS_PATH"
    "GBM_BACKENDS_PATH"
    "__EGL_VENDOR_LIBRARY_FILENAMES"
    "GDK_BACKEND"
    "G_RESOURCE_OVERLAYS"
  ];
  saveEnvironment =
    lib.concatMapStringsSep "\n" (name: ''
      if [[ -v ${name} ]]; then
        export GHOSTTY_PARENT_${name}="${"$"}${name}"
      else
        unset GHOSTTY_PARENT_${name}
      fi
    '')
    runtimeEnvironment;
  restoreEnvironment =
    lib.concatMapStringsSep "\n" (name: ''
      if [[ -v GHOSTTY_PARENT_${name} ]]; then
        export ${name}="$GHOSTTY_PARENT_${name}"
        unset GHOSTTY_PARENT_${name}
      else
        unset ${name}
      fi
    '')
    runtimeEnvironment;
  # GTK 4.22 on GNOME 46 draws its own oversized Adwaita cursors instead of
  # the cursor theme; see gtk-cursors.py.
  gtkCursors =
    pkgs.runCommand "ghostty-gtk-cursors" {nativeBuildInputs = [pkgs.python3];} ''
      python3 -I ${./gtk-cursors.py} \
        ${config.home.pointerCursor.package}/share/icons/${config.home.pointerCursor.name}/cursors $out \
        --size ${toString config.home.pointerCursor.size}
    '';
  # libwayland caps a client's outgoing buffer at 4 KiB unless the toolkit
  # raises it, and GTK 4.22 never does. Kitty image previews (Yazi) can fill
  # it faster than GNOME Shell drains it; GTK then treats EAGAIN as fatal and
  # Ghostty exits with "Error flushing display: Resource temporarily
  # unavailable". Raise the cap right after GTK connects.
  waylandBufferShim = pkgs.runCommandCC "ghostty-wayland-buffer-shim" {} ''
    mkdir -p $out/lib
    cc -O2 -shared -fPIC -o $out/lib/libghostty-wayland-buffer.so -x c - <<'EOF'
    #define _GNU_SOURCE
    #include <dlfcn.h>
    #include <stddef.h>

    struct wl_display;

    struct wl_display *wl_display_connect(const char *name) {
      struct wl_display *(*connect)(const char *) =
        dlsym(RTLD_NEXT, "wl_display_connect");
      void (*set_max)(struct wl_display *, size_t) =
        dlsym(RTLD_DEFAULT, "wl_display_set_max_buffer_size");
      struct wl_display *display = connect(name);
      if (display && set_max) set_max(display, 16 << 20);
      return display;
    }
    EOF
  '';
  ghosttyPackage =
    if pkgs.stdenv.hostPlatform.isLinux
    then
      pkgs.symlinkJoin {
        name = "ghostty-wrapped-${pkgs.ghostty.version}";
        paths = [pkgs.ghostty];
        inherit (pkgs.ghostty) meta;
        nativeBuildInputs = [pkgs.makeWrapper];
        postBuild = ''
          # Native Wayland. GDK_BACKEND=x11 used to dodge GTK exiting on EAGAIN
          # during Kitty image floods, but under Xwayland Ghostty deadlocked
          # silently when a hidden window became visible again (workspace
          # switch, alt-tab); see ghostty-org/ghostty discussion 12411. The
          # preload shim above handles the EAGAIN exit instead.
          wrapProgram "$out/bin/ghostty" \
            --run ${lib.escapeShellArg saveEnvironment} \
            --set GHOSTTY_RESTORE_ENVIRONMENT 1 \
            --prefix LD_PRELOAD : ${waylandBufferShim}/lib/libghostty-wayland-buffer.so \
            --set G_RESOURCE_OVERLAYS /org/gtk/libgdk/cursor=${gtkCursors} \
            --run 'if [[ ! -e /etc/NIXOS ]]; then exec ${lib.getExe nixGLMesa} ${lib.getExe pkgs.ghostty} "$@"; fi'

          # A standalone home profile can run on NixOS or another distro.
          # Select graphics support at launch, and route desktop/D-Bus/systemd
          # activation through this same wrapper instead of the raw package.
          for file in "$out"/share/applications/*.desktop \
                      "$out"/share/dbus-1/services/*.service \
                      "$out"/share/systemd/user/*.service; do
            [[ -e "$file" ]] || continue
            substitute "$file" "$file.new" \
              --replace-quiet '${pkgs.ghostty}' "$out" \
              --replace-quiet 'Exec=ghostty' "Exec=$out/bin/ghostty"
            mv "$file.new" "$file"
          done
        '';
      }
    else pkgs.ghostty;
in {
  # Frosted-glass backgrounds baked from the desktop wallpapers
  # (frost-background.py): blurred like an iOS material and toned per theme,
  # then shown opaque. blur-my-shell's live per-app blur stopped GNOME sending
  # frame callbacks to Ghostty after workspace switches, freezing it.
  # sigma 34 image px x 1.5 cover scale = 51 screen px on the 27" 4K.
  xdg.configFile = let
    walls = "https://raw.githubusercontent.com/dharmx/walls/6bf4d733ebf2b484a37c17d742eb47e5139e6a14";
    frost = name: src: args:
      pkgs.runCommand name {
        nativeBuildInputs = [(pkgs.python3.withPackages (p: [p.numpy p.pillow]))];
      } "python3 ${./frost-background.py} ${src} $out ${args}";
  in {
    "ghostty/shaders/cursor_warp.glsl".source = ./cursor_warp.glsl;
    "ghostty/shaders/ambient.glsl".source = ./ambient.glsl;
    "ghostty/backgrounds/dark.png".source = frost "ghostty-frost-dark.png" (pkgs.fetchurl {
      url = "${walls}/mountain/a_snowy_mountain_tops_with_blue_sky.jpg";
      hash = "sha256-zjl0t/8LfXNTIsZtByM1Lczdwmm0tHZs50XPsaniy6I=";
    }) "--bg '#1e2233' --tint 0.62 --dim 0.5";
    "ghostty/backgrounds/light.png".source = frost "ghostty-frost-light.png" (pkgs.fetchurl {
      url = "${walls}/mountain/a_mountain_with_snow_and_clouds.jpg";
      hash = "sha256-I/ZV9UoxeGrbRGadYNkhFfa8g2ulSSbS0GeATN3x43M=";
    }) "--bg '#EEF0E4' --tint 0.62 --lift 0.25 --saturate 0.6";
  };

  targets.genericLinux = lib.mkIf useNixGL {
    enable = lib.mkDefault true;
    nixGL = {
      packages = import inputs.nixgl {inherit pkgs;};
      defaultWrapper = "mesa";
      installScripts = ["mesa"];
      # WebGPU in Firefox needs Mesa's Vulkan drivers (RADV on the AMD iGPU).
      vulkan.enable = true;
    };
  };

  programs = {
    zsh.envExtra = lib.mkIf pkgs.stdenv.hostPlatform.isLinux (lib.mkBefore ''
      # Consume the saved environment once so nested development shells keep
      # their own library paths. Ghostty retains its private graphics runtime.
      if [[ -n "''${GHOSTTY_RESTORE_ENVIRONMENT-}" ]]; then
        ${restoreEnvironment}
        unset GHOSTTY_RESTORE_ENVIRONMENT
      fi
    '');

    ghostty = {
      enable = true;
      package = ghosttyPackage;
      themes = {
        # Matches snappy.nvim and the Agent Hub UI: blue-slate base, soft pastels.
        snappy = {
          palette = [
            "0=#3a3d4d"
            "1=#e38b96"
            "2=#a3c88b"
            "3=#e6c27a"
            "4=#77bdfb"
            "5=#a890d3"
            "6=#8fd3e6"
            "7=#a9b1d6"

            "8=#565f89"
            "9=#ec9aa4"
            "10=#b5d6a0"
            "11=#edd096"
            "12=#93cbfc"
            "13=#b4befe"
            "14=#a8def0"
            "15=#e6e4f0"
          ];

          background = "#1e2233";
          foreground = "#d4d2e2";
          background-image = "${config.xdg.configHome}/ghostty/backgrounds/dark.png";
          background-image-fit = "cover";
          cursor-color = "#b4befe";
          cursor-text = "#1e2233";
          selection-background = "#3c4e64";
          selection-foreground = "#e6e4f0";
        };
        # Daytime counterpart: sage paper with moss, lake and ochre accents, all
        # normal colors at WCAG AA (>= 4.5:1) on the background.
        snappy_light = {
          palette = [
            "0=#4A5548" # ink2
            "1=#B03E45" # berry
            "2=#36703A" # moss
            "3=#7E5F0E" # ochre
            "4=#2A6E99" # lake
            "5=#8A4F8E" # heather
            "6=#1B726C" # teal
            "7=#626B5E" # stone

            "8=#7C8576" # lichen / muted gutter
            "9=#B84B52"
            "10=#448246"
            "11=#937112"
            "12=#3779A3"
            "13=#965C9A"
            "14=#25857F"
            "15=#1E261D" # deep ink
          ];

          background = "#EEF0E4"; # sage paper
          foreground = "#2A3329";
          background-image = "${config.xdg.configHome}/ghostty/backgrounds/light.png";
          background-image-fit = "cover";
          cursor-color = "#36703A";
          cursor-text = "#EEF0E4";

          selection-background = "#D8E2CC";
          selection-foreground = "#2A3329";
        };
      };
      settings = {
        theme = "dark:snappy,light:snappy_light";
        # custom-shader = "shaders/cursor_warp.glsl";
        # ambient.glsl (aurora + motes) saturated the AMD iGPU (23% -> 99% busy)
        # while animating on a 4K/120 Hz screen; kept for reference, not enabled.
        # custom-shader = "${config.xdg.configHome}/ghostty/shaders/ambient.glsl";
        # custom-shader-animation = true;

        font-family = "Maple Mono NF";
        font-size = lib.mkDefault 12;
        font-thicken = true;
        font-feature = [
          "+cv01"
          "+cv02"
          "+zero"
        ];

        shell-integration-features = "no-cursor,no-sudo,title";
        # Slow touchpad scrolling, including Codex history through tmux.
        # Keep mouse-wheel scrolling at Ghostty's default speed.
        mouse-scroll-multiplier = "precision:0.1,discrete:3";
        bell-features = "no-system,no-audio,no-attention,no-title,no-border";
        cursor-style-blink = false;
        adjust-cursor-thickness = 2;
        cursor-style = "bar";

        # gtk-adwaita = false;
        resize-overlay = "never";
        clipboard-read = "allow";
        clipboard-write = "allow";
        clipboard-paste-protection = false;
        gtk-single-instance = false;
        confirm-close-surface = false;

        window-decoration = false;
        link-url = true;
      };
    };
  };
}
