{
  pkgs,
  username,
  adminAccess,
  ...
}: {
  programs.zsh.enable = true;
  programs.tmux.enable = true;

  programs.gnupg.agent = {
    enable = true;
    enableSSHSupport = true;
  };

  users.defaultUserShell = pkgs.zsh;
  users.users.${username} = {
    isNormalUser = true;
    extraGroups = ["wheel" "networkmanager" "video" "dialout"];
    home = "/home/${username}";
    initialHashedPassword = "";
    createHome = true;
    openssh.authorizedKeys.keys = adminAccess.authorizedKeys or [];
  };

  security.sudo = {
    enable = true;
    extraRules = [
      {
        commands = [
          {
            command = "/usr/bin/env";
            options = ["NOPASSWD"];
          }
        ];
        groups = ["wheel"];
      }
    ];
  };
}
