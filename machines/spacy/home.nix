{
  lib,
  username,
  ...
}: {
  imports = [
    ../../modules/profiles/home/minimal-gui.nix
  ];

  home.activation.make-zsh-default-shell = lib.hm.dag.entryAfter ["writeBoundary"] ''
    # NixOS already sets users.defaultUserShell declaratively. This activation
    # is only needed when the Spacy home profile is used on a generic Linux
    # installation, where changing /etc/shells may require sudo.
    if [[ ! -e /etc/NIXOS ]]; then
      PATH="/usr/bin:/bin:$PATH"
      ZSH_PATH="/home/${username}/.nix-profile/bin/zsh"

      # only run if current shell is not ZSH_PATH
      if [[ $(getent passwd ${username}) != *"$ZSH_PATH" ]]; then
        echo "setting zsh as default shell (using chsh). password might be necessary."

        # add to /etc/shells if missing
        if ! grep -q "$ZSH_PATH" /etc/shells; then
          echo "adding $ZSH_PATH to /etc/shells"
          run sudo tee -a /etc/shells <<<"$ZSH_PATH"
        fi

        echo "running chsh to make zsh the default shell"
        run chsh -s "$ZSH_PATH" ${username}
        echo "zsh is now set as default shell!"
      fi
    fi
  '';
}
