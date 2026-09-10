# Installation And Bring-Up

This repo now treats installation as a flake app, not a pile of custom shell scripts.

The primary entrypoint is:

```bash
nix run .#install -- ...
```

Or directly from a remote machine without cloning first:

```bash
nix run github:ausbxuse/nix-config#install -- ...
```

This document covers:

- installing a known NixOS host
- installing a new custom NixOS host
- setting up a new Home Manager-only host
- validating a machine after installation
- common post-install tweaks

## Concepts

There are two sources of truth involved in the new flow.

### Global repo settings

[globals.nix](../globals.nix) is for repo-wide values only:

- username
- supported systems

It should not be the place where per-host architecture or per-host role lives.

### Machine registry

[machines/defs.nix](../machines/defs.nix) is the public staging registry for hosts that have not been admitted yet.

Canonical admitted hosts live in private `nix-secrets/hosts.nix`, which is merged over the public staging defs at evaluation time.

Each host definition can declare:

- `system`
- `username`
- `nixos = { enable; profile; }`
- `home = { enable; profile; displayProfile; }`
- `install = { layout; disk; swapSize; }`
- `platform`
- `visibility`

This drives:

- `nixosConfigurations`
- `homeConfigurations`
- install behavior for staged or admitted hosts
- default username and profile selection
- host list generation for checks

Use public `machines/defs.nix` for pre-admission bootstrap entries. Once a host is enrolled, its long-term canonical definition belongs in private `nix-secrets/hosts.nix`.

## Quick Start

### Fast offline Razy or Spacy install

Build the x86_64 installer while online:

```bash
nix build .#images.x86_64-linux.gnome-iso
```

Write `result/iso/*.iso` to installation media and boot it. The graphical live
session starts `install-profile` automatically. It checks PCI display devices,
recommends Razy when it sees NVIDIA, recommends Spacy otherwise, and asks you to
affirm the choice before doing anything destructive.

The live `nixos` account uses the same reusable minimal GNOME system layer,
Home Manager `minimal-gui` profile, and `gnome-default` display profile as
Spacy. Installer-specific overrides disable persistent/background behavior
such as Syncthing, screen locking, and automatic suspend; they also retain the
offline-safe Neovim wrapper and installer launchers. The image also disables
the upstream graphical ISO's forced Hyper-V and Xen guest integrations: under
Quickemu they probe the wrong hypervisor and can otherwise produce a misleading
`Failed to start Load Kernel Modules` banner. Because these profiles provision
Btrfs, the live image also refuses to force-import unrelated ZFS root pools.

The useful commands on the ISO are:

```bash
install-profile       # auto-detect, optional config edit, then install
edit-install-config   # edit ~/nixos-install.conf only
install-razy          # explicitly select Razy; accepts install-config options
install-spacy         # explicitly select Spacy; accepts install-config options
install-interactive   # original question-by-question terminal installer
```

The current dual-profile image is about 14.88 GiB (15.97 GB).
`nix run .#setup-recovery-usb` now reserves up to 32 GiB for it, and
`just refresh-installer-usb /dev/<partition>` refuses to write an image that
does not fit.

The image contains complete public NixOS closures, including Home Manager
packages, for both targets:

- `razy`: `portable-nvidia-gnome`, `personal-gnome`, `laptop-2_5k`, and NVIDIA
  PRIME offload (sync remains disabled)
- `spacy`: `portable-gnome`, `minimal-gui`, `gnome-default`, and no NVIDIA
  configuration

Installation does not evaluate or build either target and does not contact a
binary cache: it partitions the selected disk, copies the selected closure
locally in parallel, registers it, and activates that exact system. The target
disk may be overridden without changing the embedded configuration.

The installed Neovim configuration, all enabled plugins, compiled Tree-sitter
parsers, language servers, and plugin helper binaries are store-backed Home
Manager inputs. First launch does not run `vim.pack.add`, `TSInstall`, or a
plugin-specific binary downloader. It does not depend on the repository being
copied to `~/src/public/nix-config`, so Neovim remains configured when
`copy_repo=no`. Features whose purpose is an online service, such as Copilot,
still need that service when used, but editor installation and startup do not.
Mutable `vim.pack` plugins, parsers, and queries left by an older deployment
are ignored so they cannot shadow the Nix versions; they are not deleted
automatically.
By default the installer copies the adjusted public repository there without a
separate prompt, making the new host ready for subsequent rebuilds and
enrollment. Set `copy_repo=no` explicitly to opt out.

Offline setup reads ahead from the compressed ISO while you edit settings,
confirm the profile, or enter the disk passphrase. It uses reclaimable page
cache, capped at half of currently available RAM, and stops if available memory
falls below 512 MiB. The worker is cancelled before disk preparation/copying,
on cancellation, and when handing off between installer interfaces. It does
not unpack packages, write another image, or download anything. The helper
only runs when the live ISO's Squashfs backing file is present. Online and
dry-run installs skip it. To compare the same setup with prefetch disabled:

```sh
NIXOS_INSTALLER_PREFETCH=0 install-profile
```

The live installer extracts only the selected host's closure directly from
SquashFS, allowing parallel decompression to run ahead of file creation. It
preserves file metadata and hardlinks, and treats missing paths or extraction
errors as installation failures. The extraction reuses the compressed pages
warmed during setup. Offline installs launched outside the live ISO use the
mounted Nix store instead.

Razy retains GRUB's other-OS detection. During `nixos-enter`, target activation
creates dmraid's runtime lock directory after mounting `/run`; creating it
under the installer's outer `/mnt/run` is insufficient because that directory
is hidden by the activation mount.

After a successful installation, the installer prints a phase-by-phase timing
table followed by the required next steps. It pauses for Enter when launched
interactively so a desktop terminal cannot close before those instructions are
read. An end-to-end Spacy installation in an 8 GiB, 6-vCPU KVM with no network
device measured on 2026-09-03 as follows (storage speed will change the result):

| Phase | Time |
| --- | ---: |
| Prepare | 0s |
| Disk + LUKS | 25s |
| Hardware config | 0s |
| Embedded store copy | 1m 41s |
| Store registration | 1s |
| `nixos-install` | 2s |
| Finish | 0s |
| **Total** | **2m 09s** |

When that success screen appears, run `sudo poweroff`, remove or eject the
installer medium, and boot the target disk. Enter the LUKS passphrase at the
boot prompt, then enroll Razy or Spacy to add private secrets and admin access.
Do not launch the installer against that disk again unless erasing it is
intentional.

The portable target initrds include NVMe/SATA and virtio block drivers. This is
required for Quickemu: UEFI and GRUB can see a virtio disk before Linux starts,
but the encrypted root is invisible to stage 1 unless `virtio_pci` and
`virtio_blk` are already in the initrd. The offline Spacy path has been tested
through installation, firmware/GRUB boot, LUKS unlock, all Btrfs mounts and
swap activation, Home Manager activation, and the GNOME login screen.

The launcher creates one writable, documented settings file at
`~/nixos-install.conf`. Every accepted key and value is listed in comments in
that file. Choosing the edit option opens it with the repository's Neovim
options, keymaps, and autocmds. Plugin bootstrapping is disabled in this special
editor mode so opening it remains offline-safe. The original `install-config`
terminal UI is still available through `install-interactive`.

Disk selection, repo copying, dry-run behavior, and other installer-only
options can change while remaining fully offline. Identity, NixOS/Home/display
profiles, layout, and swap size are baked into each closure; the launcher
rejects incompatible offline overrides and tells you to select
`install_source=online`. Rebuild the ISO when a baked setting changes.

The embedded targets intentionally use the checked-in public `nix-secrets`
stub even if the ISO build command is given a private input override. Enroll
the installed machine afterward to add its real secrets. Other hosts are
rejected by default on this image; pass `--online` to `install-config` only
when you deliberately want the normal network/build path.

Each closure is a snapshot. Rebuild the ISO whenever the flake changes. The
installer checks that every expected store path is present before asking for
destructive confirmation.

### Known host install

Use this when the host already exists in the merged host registry and has corresponding files under [machines](../machines).

Example:

```bash
nix run github:ausbxuse/nix-config#install -- --host razy
```

The installer will:

- load the host definition from the registry
- use that host's architecture
- ask for destructive confirmation
- ask for the target disk if needed
- ask for the LUKS password
- run `disko`
- generate and save `hardware-configuration.nix`
- run `nixos-install`
- optionally copy the repo into the new system

### custom install for a new machine

Use this when the host does not exist in the repo yet and you want to bootstrap fast.

Example:

```bash
nix run github:ausbxuse/nix-config#install -- --host newbox --nixos --home
```

The installer will:

- auto-detect the architecture
- ask whether NixOS and/or Home Manager should be enabled
- ask for a NixOS profile if `--nixos` is enabled
- ask for a home profile if `--home` is enabled
- ask for a display profile if needed
- ask for disk and swap settings if doing NixOS installation
- generate temporary host files inside a worktree under `/tmp`
- install from that generated configuration

This path is meant for fast bring-up. After the machine is proven working, keep any pre-admission staging entry in [machines/defs.nix](../machines/defs.nix) only until enrollment. The admitted long-term definition should live in private `nix-secrets/hosts.nix`, with machine files committed under [machines](../machines).

### Home Manager-only setup

Use this when you only want the home configuration on an existing Linux system.

Example for a known home-capable host:

```bash
nix run github:ausbxuse/nix-config#install -- --host earthy --home
```

Example for a brand new custom machine:

```bash
nix run github:ausbxuse/nix-config#install -- --host laptop-work --home
```

In Home Manager-only mode the installer:

- skips disk formatting and `nixos-install`
- generates an custom home config when necessary
- runs:

```bash
nix run nixpkgs#home-manager -- switch --flake '<worktree>#<user>@<host>'
```

## Detailed Flows

## Installing A Known NixOS Host

Prerequisites:

- booted into a NixOS live environment or another environment with Nix installed
- network access
- access to the target disk
- this repo must already contain:
  - [machines/<name>/nixos.nix](../machines)
  - [machines/defs.nix](../machines/defs.nix) entry

Recommended command:

```bash
nix run github:ausbxuse/nix-config#install -- --host uni
```

What happens:

1. The installer reads the known host definition.
2. It uses the registered `system`, `username`, profiles, and install layout.
3. It prompts for the target disk if you did not pass `--disk`.
4. It writes a temporary `machines/defs.nix` overlay in the worktree with any runtime overrides.
5. It asks for the LUKS password and writes it temporarily to `/tmp/secret.key`.
6. It runs:

```bash
sudo disko --mode destroy,format,mount --flake .#<host>
sudo nixos-generate-config --no-filesystems --root /mnt
sudo nixos-install --root /mnt --flake .#<host>
```

7. It offers to copy the repo into the installed system.

Recommended explicit-disk variant:

```bash
nix run github:ausbxuse/nix-config#install -- --host uni --disk /dev/nvme1n1
```

This is the safest pattern for repeatability.

### Failure Mode: Dirty Checkout Changes The Installed System

Symptom:

- install succeeds, but the installed machine boots differently from what you expect
- debugging becomes confusing because `nix run .#install` and the resulting system do not seem to match

Cause:

- if the installer copies from your live working tree instead of the packaged flake snapshot, any local dirty changes can silently change what gets installed

Why it is subtle:

- the install command itself still looks normal
- the boot failure can show up much later, for example as initrd waiting for the wrong LUKS path

Quick check:

- if removing `.git` from the copied repo suddenly makes the install behave correctly, you were probably installing from the live checkout instead of the packaged source

Current expected behavior:

- the installer always installs from the packaged `REPO_SOURCE` snapshot
- the repo copied into the target is that same snapshot, not your dirty checkout

## Installing A New NixOS Host

This is the fast bootstrap path for a machine that is not yet committed to the repo.

Example:

```bash
nix run github:ausbxuse/nix-config#install -- --host razer-test --nixos --home
```

The installer will ask for:

- target disk
- NixOS profile
- home profile
- display profile
- swap size
- LUKS password

Typical interactive answers for a new laptop might look like:

```text
Host name for install or bootstrap: razer-test
custom NixOS profile: portable-nvidia-gnome
custom home profile: personal-gnome
custom display profile: razy-current
Available disks:
  /dev/nvme0n1  1.8T  Samsung SSD  NVMe
Target disk: /dev/nvme0n1
custom swap size: 32G
Enter LUKS disk password:
```

The installer auto-detects:

- architecture via `uname -m`

For custom hosts, the installer does not infer a mode. Pass `--home`, `--nixos`, or both explicitly.

Examples:

```bash
nix run github:ausbxuse/nix-config#install -- --host earthy
# fails with: Nothing to do: pass --home and/or --nixos.

nix run github:ausbxuse/nix-config#install -- --host earthy --home
nix run github:ausbxuse/nix-config#install -- --host razer-test --nixos --home
```

The installer may suggest defaults for:

- NixOS profile
- disk

For example, if an NVIDIA GPU is visible via `lspci`, it currently suggests:

```text
portable-nvidia-gnome
```

For the display profile prompt, the value should match one of the profiles in [modules/home/display-profile.nix](../modules/home/display-profile.nix), for example:

- `gnome-default`
- `laptop-2_5k`
- `external-4k`
- `docked-dual`

For disk and swap prompts:

- disk should be a whole-disk device like `/dev/nvme0n1`
- swap should be a Nix size string like `16G`, `32G`, or `8G`

The temporary worktree is created under:

```text
/tmp/nixos-installer.XXXXXX
```

Inside that worktree, the installer may generate:

- `machines/defs.nix` overlaying the custom host entry
- `machines/<host>/hardware-configuration.nix`

The temporary host inventory overlay is for the install run only. It is not written back into your real repo automatically.

When the install succeeds, you should convert the custom host into a real host definition:

1. Add or keep a staging entry in [machines/defs.nix](../machines/defs.nix) if you want to bootstrap admission from the public repo.
2. Create:
   - [machines/<host>/nixos.nix](../machines) if needed
   - [machines/<host>/home.nix](../machines) if needed
3. Move the generated hardware configuration into:
   - [machines/<host>/hardware-configuration.nix](../machines)
4. Enroll the host so its canonical admitted entry lands in private `nix-secrets/hosts.nix`.
5. Rebuild from the committed repo afterward.

This custom path is for speed, not for long-term configuration ownership.

## Installing A New Home Manager-Only Host

For machines where you do not want NixOS installation, run Home Manager-only mode.

Known host:

```bash
nix run github:ausbxuse/nix-config#install -- --host earthy --home
```

custom host:

```bash
nix run github:ausbxuse/nix-config#install -- --host office-laptop --home
```

The installer will prompt for:

- home profile
- display profile

This is useful for:

- non-NixOS Linux
- temporary machines
- new cross-platform personal environments

If the machine should become a long-term tracked host later, stage it in [machines/defs.nix](../machines/defs.nix), then enroll it so its canonical entry lands in private `nix-secrets/hosts.nix`, and create a real [machines/<host>/home.nix](../machines) entry.

## Installer CLI Reference

The current help output is:

```text
Usage:
  nix run .#install -- [options]

Options:
  --host NAME              Known host name or a new custom host name
  --disk PATH              Target disk for NixOS installation
  --system SYSTEM          Override detected system, e.g. x86_64-linux
  --username NAME          Override the host user name
  --nixos      Enable or disable NixOS installation mode
  --home       Enable or disable Home Manager mode
  --nixos-profile NAME     Profile file basename under modules/profiles/nixos/
  --home-profile NAME      Profile file basename under modules/profiles/home/
  --display-profile NAME   Display profile for custom home configs
  --swap-size SIZE         Swapfile size for custom disk configs, e.g. 32G
  --copy-repo yes|no       Copy the resulting repo into the installed system
  --repo-dest PATH         Destination for copied repo inside the target root
  -y, --yes                Accept destructive prompts
```

## Post-Install Validation

After the first boot, run:

```bash
nix run .#validate-host
```

Or directly from GitHub if the repo is not yet copied locally:

```bash
nix run github:ausbxuse/nix-config#validate-host
```

The validator currently checks:

- `systemctl is-system-running`
- PipeWire sink presence
- ALSA playback device enumeration
- ALSA capture device enumeration
- V4L2 camera enumeration
- brightness device exposure
- `nvidia-smi` when an NVIDIA GPU is present

This is not a full bring-up test suite. It is a fast sanity pass after install.

## Suggested Validation Workflow

After installation and first boot:

1. Run:

```bash
nix run .#validate-host
```

2. Check flake evaluation:

```bash
nix flake check --no-build
```

3. Check that the expected host outputs evaluate:

```bash
nix build .#checks.x86_64-linux.nixos-<host>
nix build .#checks.x86_64-linux.home-<host>
```

4. Confirm:

- internal audio works
- suspend/resume works
- Wi-Fi and Bluetooth work
- webcam works
- brightness keys work
- GPU path matches expectations
- GNOME scaling and monitors look correct

## Post-Install Tweaks

These are the common follow-up tasks after the base install succeeds.

### Copy or restore secrets

The current secret flow is intentionally minimal:

- the repo optionally consumes a private `nix-secrets` checkout via the `nix-secrets` flake input
- Home Manager enables `sops-nix` only if `nix-secrets/secrets.yaml` exists
- decryption expects an age key at `~/.config/sops/age/keys.txt`
- the repo does not currently bootstrap SSH keys or clone private secrets for you

So after a fresh install, secret-backed configuration only works if you manually provide:

- SSH access to your private Git remote
- your private `nix-secrets` checkout or flake override
- your age key file at `~/.config/sops/age/keys.txt`

If Home Manager warns that `nix-secrets/secrets.yaml` is missing, secret-backed Home Manager config was skipped for that build.

### Rebuild from the local checkout

If you copied the repo into the target system, switch again from the local tree once the machine is up:

```bash
sudo nixos-rebuild switch --flake .#<host>
```

And if needed:

```bash
home-manager switch --flake .#<username>@<host>
```

### Verify committed host metadata

Known-host installs and custom installs now override host metadata by writing a temporary `machines/defs.nix` in the installer worktree.

That means:

- the live install works immediately
- but you should still make sure the long-term host definition in `nix-secrets/hosts.nix` (or the temporary staging entry in [machines/defs.nix](../machines/defs.nix) before enrollment) matches the intended username, profiles, disk path, and swap size

For permanent hosts, prefer committed host inventory data over relying on custom runtime overrides forever.

### Promote an custom host into the repo

If you installed an unknown machine interactively and want to keep it:

1. Add or keep a staging entry in [machines/defs.nix](../machines/defs.nix) if you want to enroll from a public bootstrap record.
2. Create real host files.
3. Save and review `hardware-configuration.nix`.
4. Ensure the admitted canonical entry in private `nix-secrets/hosts.nix` carries the final `home` / `nixos` / `install` fields.
5. Run:

```bash
nix flake check --no-build
```

### Run host-specific bring-up notes

Some machines need manual verification beyond the generic validator.

For `razy`, see:

- [razy-bringup.md](razy-bringup.md)

That document covers the real debugging path and the machine-specific issues for the Razer Blade.

## Migration From The Old Install Scripts

The old scripts under [scripts](../scripts):

- `install.sh`
- `install_home.sh`

should now be considered legacy. `install_portable.sh` has been folded into the
main installer as `--portable` mode:

```bash
NP_RUNTIME=bwrap nix-portable nix run .#install -- --portable --host earthy
```

The intended path is:

- `nix run github:ausbxuse/nix-config#install -- ...`
- `nix run .#validate-host`

The goal is to eliminate the old scripts entirely once the new flow has been exercised enough.
