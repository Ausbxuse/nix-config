#
# phone-media-sort — moves files from ~/Media/Phone into the organized
# ~/Media/{Pictures,Videos,Audio} tree, sorted by MIME type.
#
# ~/Media/Phone is a syncthing receive-only folder mirroring the phone's
# DCIM. Moved files won't re-download (receive-only doesn't auto-revert
# local changes). Empty Phone/ = everything sorted, safe to delete on phone.
#
{
  config,
  lib,
  pkgs,
  ...
}: let
  home = config.home.homeDirectory;
  phoneDir = "${home}/Media/Phone";
  picturesDir = "${home}/Media/Pictures";
  videosDir = "${home}/Media/Videos";
  audioDir = "${home}/Media/Audio";

  sortScript = pkgs.writeShellScript "phone-media-sort" ''
    set -euo pipefail
    src=${lib.escapeShellArg phoneDir}
    [[ -d "$src" ]] || exit 0

    pictures=${lib.escapeShellArg picturesDir}
    videos=${lib.escapeShellArg videosDir}
    audio=${lib.escapeShellArg audioDir}
    mkdir -p "$pictures" "$videos" "$audio"

    # `file` may inspect several MiB per video. Rechecking an ingest directory
    # full of basename collisions consequently filled gigabytes of page cache,
    # and a run frozen at lid-close resumed on GNOME's critical wake path.
    # Phone media has reliable extensions, so handle those without opening the
    # payload. Keep a tightly bounded MIME fallback for unfamiliar formats.
    # Syncthing's .stversions/.stfolder trees are metadata, not new media.
    find "$src" -type d -name ".*" -prune -o \
      -type f -mmin +1 -print0 | while IFS= read -r -d "" f; do
      name="''${f##*/}"
      extension="''${name##*.}"
      extension="''${extension,,}"

      case "$extension" in
        avif|bmp|dng|gif|heic|heif|jpeg|jpg|jxl|png|tif|tiff|webp)
          dest="$pictures"
          ;;
        3gp|avi|m2ts|m4v|mkv|mov|mp4|mpeg|mpg|mts|webm)
          dest="$videos"
          ;;
        aac|flac|m4a|mp3|oga|ogg|opus|wav)
          dest="$audio"
          ;;
        *)
          mime=$(${pkgs.file}/bin/file -P bytes=262144 -b --mime-type -- "$f")
          case "$mime" in
            image/*) dest="$pictures" ;;
            video/*) dest="$videos" ;;
            audio/*) dest="$audio" ;;
            *) continue ;;
          esac
          ;;
      esac

      # A collision is deliberately left in the receive-only ingest tree. It
      # may be a distinct file with the same basename, so silently deleting or
      # overwriting it would be unsafe. Crucially, do not reopen its payload on
      # every timer run.
      [[ -e "$dest/$name" ]] && continue
      mv -n -- "$f" "$dest/"
    done
  '';
in {
  systemd.user.services.phone-media-sort = {
    Unit.Description = "Sort phone media into organized directories";
    Service = {
      Type = "oneshot";
      ExecStart = "${sortScript}";
      # This is background housekeeping. If it overlaps a resume, GNOME and
      # interactive applications must win both CPU and storage contention.
      Nice = 19;
      CPUSchedulingPolicy = "idle";
      IOSchedulingClass = "idle";
      CPUWeight = 1;
      IOWeight = 1;
      MemoryHigh = "64M";
      MemoryMax = "256M";
    };
  };

  systemd.user.timers.phone-media-sort = {
    Unit.Description = "Sort phone media periodically";
    Timer = {
      OnBootSec = "5m";
      OnUnitActiveSec = "15m";
    };
    Install.WantedBy = ["timers.target"];
  };
}
