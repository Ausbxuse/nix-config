{
  lib,
  const,
  ...
}: {
  # Public staging registry for hosts that have not been admitted into the
  # private trust mesh yet. Canonical admitted host definitions live in the
  # private nix-secrets checkout at hosts.nix.
  razy = {
    system = "x86_64-linux";
    username = "zhenyu";
    platform = "custom";
    visibility = "private";

    home = {
      enable = true;
      profile = "personal-gnome";
      displayProfile = "laptop-2_5k";
    };

    nixos = {
      enable = true;
      profile = "portable-nvidia-gnome";
    };

    install = {
      layout = "luks-btrfs";
      disk = "/dev/nvme0n1";
      swapSize = "31G";
      canTouchEfiVariables = false;
      efiInstallAsRemovable = true;
      useOSProber = true;
    };
  };

  Alienware51 = {
    system = "x86_64-linux";
    username = "zhenyu";
    platform = "custom";
    visibility = "private";

    home = {
      enable = true;
      profile = "minimal-gui";
      displayProfile = "gnome-default";
    };

    nixos = {
      enable = false;
    };
  };

  spacy = {
    system = "x86_64-linux";
    username = "zhenyu";
    platform = "custom";
    visibility = "private";

    home = {
      enable = true;
      profile = "minimal-gui";
      displayProfile = "gnome-default";
    };

    nixos = {
      enable = true;
      profile = "portable-gnome";
    };

    install = {
      layout = "luks-btrfs";
      disk = "/dev/nvme0n1";
      swapSize = "15G";
      canTouchEfiVariables = false;
      efiInstallAsRemovable = true;
      useOSProber = false;
    };
  };
}
