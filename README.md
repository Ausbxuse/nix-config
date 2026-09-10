# NixOS Config

The canonical installation and bring-up guide is:

- [docs/installation.md](/home/zhenyu/src/public/nix-config/docs/installation.md)

The short version:

```bash
nix run github:ausbxuse/nix-config#install -- --host <host>
```

For post-install validation:

```bash
nix run .#validate-host
```

Useful local commands:

```bash
nix flake check --no-build
nix build .#images.x86_64-linux.gnome-iso
```

The x86_64 image embeds the complete public `razy` and `spacy` system closures.
Its launcher detects NVIDIA hardware, recommends the matching target, and asks
for affirmation; package installation for either target then works fully
offline. The live session itself uses Spacy's `minimal-gui` Home Manager setup
on the shared minimal GNOME system layer. See
[installation.md](docs/installation.md#fast-offline-razy-or-spacy-install).
