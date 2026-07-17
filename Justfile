set shell := ["bash", "-uc"]

# GENERATION := $$(nixos-rebuild list-generations --flake ".\#" | grep current)
nix-interactive-options := "--option connect-timeout 3 --option download-attempts 1 --option fallback true"

gitgc:
  git reflog expire --expire-unreachable=now --all
  git gc --prune=now

dconf:
	rsync -av ~/.config/dconf/ ./hosts/base/home/gnome/dconf

sys:
	#!/usr/bin/env bash
	set -euo pipefail

	user_home="$HOME"
	if [ -n "${SUDO_USER:-}" ]; then
		user_home="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
	fi

	extra_args=()
	if [ -d "$user_home/src/private/nix-secrets" ]; then
		extra_args+=(--override-input nix-secrets "path:$user_home/src/private/nix-secrets")
	fi

	mode=switch
	target_nvidia="$(
		nix eval --raw .#nixosConfigurations."$(hostname)".config.hardware.nvidia.package.version \
			"${extra_args[@]}" {{nix-interactive-options}} 2>/dev/null || true
	)"
	running_nvidia="$(
		modinfo nvidia 2>/dev/null | sed -n 's/^version:[[:space:]]*//p' | head -n1 || true
	)"

	if [ -n "$target_nvidia" ] && [ -n "$running_nvidia" ] && [ "$target_nvidia" != "$running_nvidia" ]; then
		mode=boot
		printf 'NVIDIA driver changes require reboot: running %s, target %s.\n' "$running_nvidia" "$target_nvidia"
		printf 'Using `nh os boot` instead of `nh os switch` to avoid live driver/userspace mismatch.\n'
	fi

	nh os "$mode" --bypass-root-check --ask --diff always --fallback --hostname "$(hostname)" . -- "${extra_args[@]}" {{nix-interactive-options}}

	if [ "$mode" = boot ]; then
		printf '\nBoot generation installed. Reboot to load NVIDIA %s.\n' "$target_nvidia"
	fi

# Optionally select a configured host when activating from a differently named machine.
# Usage: just home spacy
home CONFIG='':
	#!/usr/bin/env bash
	set -euo pipefail

	config='{{CONFIG}}'
	if [ -z "$config" ]; then config="$(hostname)"; fi
	env -u FLAKE nh home switch --ask --diff always --fallback --configuration $(if [ -n "${SUDO_USER:-}" ]; then printf '%s' "$SUDO_USER"; else whoami; fi)@"$config" . -- $(user_home="$HOME"; if [ -n "${SUDO_USER:-}" ]; then user_home="$(getent passwd "$SUDO_USER" | cut -d: -f6)"; fi; if [ -d "$user_home/src/private/nix-secrets" ]; then echo "--override-input nix-secrets path:$user_home/src/private/nix-secrets"; fi) {{nix-interactive-options}}

sys-test:
	nh os test --bypass-root-check --ask --diff always --fallback --hostname $(hostname) . -- $(user_home="$HOME"; if [ -n "${SUDO_USER:-}" ]; then user_home="$(getent passwd "$SUDO_USER" | cut -d: -f6)"; fi; if [ -d "$user_home/src/private/nix-secrets" ]; then echo "--override-input nix-secrets path:$user_home/src/private/nix-secrets"; fi) {{nix-interactive-options}}

debug:
	nh os switch --bypass-root-check --ask --diff always --fallback --show-trace --verbose --hostname $(hostname) . -- $(user_home="$HOME"; if [ -n "${SUDO_USER:-}" ]; then user_home="$(getent passwd "$SUDO_USER" | cut -d: -f6)"; fi; if [ -d "$user_home/src/private/nix-secrets" ]; then echo "--override-input nix-secrets path:$user_home/src/private/nix-secrets"; fi) {{nix-interactive-options}}

sys-debug:
	nh os switch --bypass-root-check --ask --diff always --fallback --show-trace --print-build-logs --verbose --hostname $(hostname) . -- $(user_home="$HOME"; if [ -n "${SUDO_USER:-}" ]; then user_home="$(getent passwd "$SUDO_USER" | cut -d: -f6)"; fi; if [ -d "$user_home/src/private/nix-secrets" ]; then echo "--override-input nix-secrets path:$user_home/src/private/nix-secrets"; fi) {{nix-interactive-options}} --option max-call-depth 10000

nvidia-prime-bus-ids:
	nix run .#nvidia-prime-bus-ids

# Update specific input
# usage: make upp i=home-manager
upp:
	nix flake lock --update-input $(i)

# Update the sadjow/codex-cli-nix input and activate the new Codex package.
# Usage: just upgrade-codex [CONFIG]
upgrade-codex CONFIG='':
	nix flake update codex-cli-nix
	just home {{CONFIG}}

history:
	nix profile history --profile /nix/var/nix/profiles/system

repl:
	nix repl -f flake:nixpkgs

fmt:
	alejandra .

precommit-install:
	pre-commit install

precommit:
	pre-commit run --all-files

clean:
	nh clean all --keep-since 7d --keep 3 --ask

gc:
	# Garbage collect all unused nix store entries
	sudo nix-collect-garbage --delete-old

# Regenerate nix-secrets/.sops.yaml from private hosts.nix
# and re-encrypt secrets.yaml. Run after editing either file.
rotate-secrets:
	nix run .#admit-host

# enroll a fresh NixOS host into the secrets trust mesh.
# Runs admit-host locally, rsyncs nix-secrets to the target, and triggers
# nixos-rebuild switch on the target with --override-input nix-secrets.
# Usage:
#   just enroll custom-nixos zhenyu@127.0.0.1:2224
#   just enroll dev-box zhenyu@dev.example.com
enroll HOSTNAME SSHDEST:
	nix run .#enroll -- {{HOSTNAME}} {{SSHDEST}}

# Trigger a recovery backup manually (requires RECOVERY USB plugged in).
backup-bundle:
	sudo systemctl start recovery-backup.service

# Write the NixOS installer ISO to the USB installer partition.
# Usage: just refresh-installer-usb /dev/sdX1
refresh-installer-usb PARTITION:
	nix build .#gnome-iso
	sudo dd if=$(ls ./result/iso/*.iso) of={{PARTITION}} bs=64M status=progress oflag=sync
