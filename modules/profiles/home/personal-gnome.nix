{
  lib,
  pkgs,
  ...
}: {
  imports = [
    ./minimal-gui.nix
    ../../home/sops.nix
    ../../home/programs.nix
    ../../home/gaming.nix
    ../../home/phone-media-sort.nix
  ];

  home.packages = with pkgs;
    [
      jupyter
      thunderbird-bin
      brave
      (libreoffice.override {
        unwrapped = libreoffice.unwrapped.override {langs = ["en-US"];};
      })
      # PDFLaTeX and the resume template's fonts/packages; latexmk also serves VimTeX.
      (texliveBasic.withPackages (ps:
        with ps; [
          latexmk
          newtx
          tex-gyre
          fontawesome5
          titlesec
          titling
          geometry
          nopageno
          etaremune
          tools
          xcolor
          amsfonts
          scalerel
          stackengine
          pgf
          enumitem
          hyperref
          # Font and macro dependencies loaded indirectly by the template.
          figureversions
          fontaxes
          fontname
          mweights
          xkeyval
          xpatch
          xstring
        ]))
      gimp
      foliate
      # Existing scenes use camera/screen capture; omit the large CEF browser engine.
      (obs-studio.override {browserSupport = false;})
      # Calibre's read-aloud support otherwise retains a second MBROLA voice
      # collection even when desktop speech services are disabled.
      (calibre.override {
        speechSupport = false;
        espeak-ng = pkgs.espeak-ng.override {mbrolaSupport = false;};
      })
      quickemu
    ]
    ++ lib.optionals pkgs.stdenv.hostPlatform.isx86_64 [pkgs.sct];

  # services.syncthing is configured by ../../home/syncthing.nix.
  my.display.profile = lib.mkDefault "gnome-default";
}
