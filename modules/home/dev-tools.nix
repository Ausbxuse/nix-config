# for language specific packages (e.g. linters, debuggers, compilers)
{pkgs, ...}: {
  home.packages = with pkgs; [
    # Keep tools used by Neovim's formatters, debugger and parser tooling.
    # Project-only toolchains belong in a dev shell; applications such as
    # Prism Launcher retains its own required Java runtimes.
    prettier
    gcc
    gdb
    alejandra
    stylua
    black
    isort
    tig
    tealdeer
    sshfs
  ];

  programs.direnv = {
    enable = true;
    enableZshIntegration = false;
  };
}
