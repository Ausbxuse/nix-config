# Development tools; keep using the host's Nix client (including Determinate Nix).
# You can enter it through 'nix develop' or (legacy) 'nix-shell'
{pkgs}: {
  default = pkgs.mkShell {
    # Enable experimental features without having to specify the argument
    NIX_CONFIG = "experimental-features = nix-command flakes";
    nativeBuildInputs = with pkgs; [git sops ssh-to-age gnupg age nil];
    packages = with pkgs; [
      just
      pre-commit
      nixos-anywhere
      home-manager
      lua-language-server
      stylua
      nil
      alejandra
      nixpkgs-fmt
      # nix-index
      # nix-prefetch-git
    ];
  };
}
