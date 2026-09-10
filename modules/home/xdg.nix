# XDG stands for "Cross-Desktop Group", with X used to mean "cross".
# It's a bunch of specifications from freedesktop.org intended to standardize desktops and
# other GUI applications on various systems (primarily Unix-like) to be interoperable:
#   https://www.freedesktop.org/wiki/Specifications/
{config, ...}: {
  xdg = {
    configFile."mimeapps.list".force = true;
    # manage $XDG_CONFIG_HOME/mimeapps.list
    # xdg search all desktop entries from $XDG_DATA_DIRS, check it by command:
    #  echo $XDG_DATA_DIRS
    # the system-level desktop entries can be list by command:
    #   ls -l /run/current-system/sw/share/applications/
    # the user-level desktop entries can be list by command:
    #  ls /etc/profiles/per-user/$USER/share/applications/
    mimeApps = {
      enable = true;
      defaultApplications = let
        browser = ["firefox.desktop"];
        editor = ["nvim.desktop"];
        pdfviewer = ["sioyek.desktop" "org.pwmt.zathura.desktop"];
        mediaplayer = ["mpv.desktop"];
        office = ["libreoffice-writer.desktop"];
      in {
        "application/json" = browser;
        "application/pdf" = pdfviewer;
        "application/x-shellscript" = editor;

        "text/html" = browser;
        "text/markdown" = editor;
        "text/xml" = browser;
        "text/plain" = editor;
        "text/x-nix" = editor;
        "text/x-python" = editor;
        "text/x-typst" = editor;
        "application/xml" = browser;
        "application/xhtml+xml" = browser;
        "application/xhtml_xml" = browser;
        "application/rdf+xml" = browser;
        "application/rss+xml" = browser;
        "application/x-extension-htm" = browser;
        "application/x-extension-html" = browser;
        "application/x-extension-shtml" = browser;
        "application/x-extension-xht" = browser;
        "application/x-extension-xhtml" = browser;
        "application/x-wine-extension-ini" = editor;

        # define default applications for some url schemes.
        "x-scheme-handler/about" = browser; # open `about:` url with `browser`
        "x-scheme-handler/ftp" = browser; # open `ftp:` url with `browser`
        "x-scheme-handler/http" = browser;
        "x-scheme-handler/https" = browser;
        # https://github.com/microsoft/vscode/issues/146408
        "x-scheme-handler/vscode" = ["code-url-handler.desktop"]; # open `vscode://` url with `code-url-handler.desktop`
        "x-scheme-handler/vscode-insiders" = ["code-insiders-url-handler.desktop"]; # open `vscode-insiders://` url with `code-insiders-url-handler.desktop`
        # all other unknown schemes will be opened by this default application.
        # "x-scheme-handler/unknown" = editor;

        "x-scheme-handler/tg" = ["org.telegram.desktop.desktop "];

        "audio/*" = mediaplayer;
        "video/mp4" = mediaplayer;
        "video/x-matroska" = mediaplayer;
        "video/webm" = mediaplayer;
        "video/quicktime" = mediaplayer;
        "video/x-msvideo" = mediaplayer;
        "video/x-ms-wmv" = mediaplayer;
        "video/mpeg" = mediaplayer;
        "video/ogg" = mediaplayer;
        "video/3gpp" = mediaplayer;
        "video/3gpp2" = mediaplayer;
        "video/x-flv" = mediaplayer;
        "video/mp2t" = mediaplayer;
        "application/msword" = office; # .doc
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document" = office; # .docx
        "application/vnd.ms-excel" = office; # .xls
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" = office; # .xlsx
        "application/vnd.ms-powerpoint" = office; # .ppt
        "application/vnd.openxmlformats-officedocument.presentationml.presentation" = office; # .pptx
        "application/vnd.oasis.opendocument.text" = office; # .odt
        "application/vnd.oasis.opendocument.spreadsheet" = office; # .ods
        "application/vnd.oasis.opendocument.presentation" = office; # .odp
      };

      associations.added = let
        mediaplayer = ["mpv.desktop"];
      in {
        "video/mp4" = mediaplayer;
        "video/x-matroska" = mediaplayer;
        "video/webm" = mediaplayer;
        "video/quicktime" = mediaplayer;
        "video/x-msvideo" = mediaplayer;
        "video/x-ms-wmv" = mediaplayer;
        "video/mpeg" = mediaplayer;
        "video/ogg" = mediaplayer;
        "video/3gpp" = mediaplayer;
        "video/3gpp2" = mediaplayer;
        "video/x-flv" = mediaplayer;
        "video/mp2t" = mediaplayer;
      };

      associations.removed = {
        "video/mp4" = ["org.gnome.Showtime.desktop"];
        "video/x-matroska" = ["org.gnome.Showtime.desktop"];
      };
    };

    configFile."user-dirs.dirs".force = true;
    userDirs = {
      enable = true;
      createDirectories = false;
      setSessionVariables = true;
      pictures = "${config.home.homeDirectory}/Media/Pictures";
      videos = "${config.home.homeDirectory}/Media/Videos";
      music = "${config.home.homeDirectory}/Media/Music";
      publicShare = "${config.home.homeDirectory}/Public";
      extraConfig = {
        SCREENSHOTS = "${config.xdg.userDirs.pictures}/Screenshots";
      };
    };
  };

  # xdg.portal = {
  #   enable = true;
  #   extraPortals = [
  #     pkgs.xdg-desktop-portal-gtk
  #     # pkgs.xdg-desktop-portal-hyprland
  #   ];
  # };
}
