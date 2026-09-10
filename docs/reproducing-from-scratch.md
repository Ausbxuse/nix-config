# Reproducing from scratch

This document is the authoritative runbook for bringing a new machine from bare
hardware to a fully functional personal host, and for recovering from various
disaster scenarios. It covers the trust model, the enrolling flow, and how
to test the whole thing in a VM.

Status: **design — partial implementation in progress**.

---

## Prerequisite: 30-second manual safety step

Before running any tooling in this doc, close the immediate lockout risk
by copying the current trust anchors to the fingerprint-encrypted USB.
This is a one-time interim step; the Phase D auto-backup replaces it.

```bash
# With the USB mounted (e.g. /run/media/$USER/<label>):
DEST="/run/media/$USER/<label>/emergency-keys-$(date +%Y%m%d)"
mkdir -p "$DEST"
sudo cp /etc/ssh/ssh_host_ed25519_key{,.pub} "$DEST/"
cp ~/.config/sops/age/keys.txt "$DEST/admin-age-key-INTERIM.txt"
sudo chown "$USER:$USER" "$DEST"/*
chmod 600 "$DEST/ssh_host_ed25519_key" "$DEST/admin-age-key-INTERIM.txt"
sync
```

Why this matters: until this is done, the only copy of razy's SSH host key
(which IS its sops identity) lives on razy's disk. A disk failure would
make `secrets.yaml` permanently undecryptable. See "Scenario E" under
Disaster recovery for the full failure mode.

The `admin-age-key-INTERIM.txt` file exists only until Phase A is complete
and verified; after that, the standalone admin key is deleted entirely
(this design has no admin-key concept, see "Trust model" below).

---

## Trust model at a glance

Two categories of key material. No separate "admin key" concept — a trusted
host's SSH host key _is_ the trust anchor, and disaster recovery works by
restoring a previously-trusted host's SSH key onto new hardware.

| Key                                                | Where it lives                     | Purpose                                              |
| -------------------------------------------------- | ---------------------------------- | ---------------------------------------------------- |
| **Host SSH key** (`/etc/ssh/ssh_host_ed25519_key`) | On each host + backed up in bundle | Machine identity; age key derived from it            |
| **User SSH key** (`~/.ssh/id_ed25519`)             | Per-host, generated at enroll      | User identity; authorized on GitHub, VPS, git-remote |

Consequences:

- Every trusted host has its own SSH-derived age key listed in
  `nix-secrets/.sops.yaml`. Any such host can decrypt `secrets.yaml` and
  therefore re-encrypt it (i.e. admit a new host). There is **no** shared
  admin key sitting on a running machine or in a bundle — the concept is gone.
- Losing one host does **not** require rotating every secret. It only requires
  removing that host's `sops.ageKey` field (or the whole entry) from
  `machines/defs.nix` and running `just rotate-secrets` (which regenerates
  `.sops.yaml` and runs `sops updatekeys`).
- Losing **every** trusted host simultaneously is recoverable because **the
  recovery bundle contains the SSH host keys of at least two trusted hosts**
  (e.g. razy + timy). Restore one of those keys onto fresh hardware during
  stage 1 and the new machine inherits that identity — sops-nix decrypts
  normally on first boot, no admission step required.
- This design has **no "admin role" as a special concept.** Every trusted host
  is equally capable of admitting future hosts. The bundle is just a set of
  backed-up host keys.

### Why this design and not a separate admin key

An earlier version of this design used a standalone "admin age key" that
lived only in the recovery bundle. Research into idiomatic sops-nix setups
(Mic92's dotfiles, Michael Stapelberg's 2025 post, NixOS Discourse threads)
showed that the "admin key" concept is a wart — per-host SSH keys alone are
sufficient if one of them has an offline backup. Dropping the admin key
reduces concepts, eliminates a separate rotation schedule, and means the
bundle holds only things that already exist on running hosts, not
special-purpose key material.

---

## The three stages

```
┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐
│  Stage 1:        │      │  Stage 2:        │      │  Steady state    │
│  install         │ ───▶ │  enroll       │ ───▶ │                  │
│                  │      │                  │      │                  │
│  bare NixOS      │      │  personal data   │      │  trusted member  │
│  anonymous box   │      │  injected        │      │  of the mesh     │
└──────────────────┘      └──────────────────┘      └──────────────────┘
        │                          │                         │
        │                          │                         │
  nix run .#install         nix run .#enroll         normal use
                            (peer or bundle)            + rotate-secrets
                                                          after host edits
```

### Stage 1: `nix run .#install`

Already implemented. Partitions the disk, installs NixOS, creates user
accounts, enables SSH, generates a fresh host SSH key. The result is an
anonymous, functional box with no personal data.

**Stage 1 does not generate the user SSH key** — that is personal data and
belongs to stage 2. This keeps the "anonymous box" boundary clean: at the
end of stage 1 nothing on disk identifies you.

### Stage 2: `nix run .#enroll`

Runs on the new host **as your user** after stage 1 reboots into the installed
system. Its job is to convert the anonymous box into a trusted peer. Two
admission paths:

- **Peer path** — an existing trusted host admits the new host over the
  network. Requires network, a running trusted peer, and that peer to have
  access to the nix-secrets git remote.
- **Bundle path** — an offline USB bundle admits the new host. Requires the
  recovery bundle USB and the user's memorized passphrase. No other host
  needed.

Both paths converge: the new host ends up with its age key in `.sops.yaml`,
its user SSH key registered on GitHub and the VPS git server, and its
syncthing identity pinned via sops. Razy (the introducer) auto-discovers
the new host for the rest of the mesh.

### Steady state

- `~/vault/` is a syncthing-synced folder containing `vault.kdbx`,
  `vault.kdbx.key`, and any private notes.
- `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, etc. are materialized from
  `secrets.yaml` at activation time into `/run/user/1000/secrets/` and
  exported into the shell.
- The host can itself run `enroll` (or `admit-host --set-host-key`) for
  future machines.

---

## The recovery USB drive

A single fingerprint-encrypted USB drive serves **three roles**:

1. **Bootable NixOS installer** — a custom graphical ISO derived from
   `isos/` in this repo, so a bare machine can be brought up without network
   access to a separate installer.
2. **Recovery bundle** — everything needed to re-bootstrap trust from zero.
3. **Rolling backups** — incremental snapshots of `~/`, and mirrored copies
   of media (photos, videos, music).

### Drive layout (~900 GB drive)

Two partitions only — the data partition holds the bundle, the restic repo,
and media side by side, which is simpler than a 3-way split and makes future
rebalancing trivial:

```
/dev/sdX
├── sdX1  FAT32   8 GB   (label: NIXOS_INSTALL)  — bootable installer ISO (unencrypted)
└── sdX2  ext4    rest   (label: RECOVERY)       — bundle + restic + media
    ├── recovery-bundle.tar
    ├── restic/                   # restic repo for ~/ snapshots
    └── media/
        ├── Pictures/
        ├── Videos/
        └── Music/
```

The installer partition is **unencrypted** so the firmware can boot from it
before any OS is running — BIOS/UEFI cannot drive a fingerprint unlock. The
drive supports mixing encrypted and unencrypted partitions (confirmed), so
this lives on the same physical stick as the recovery data. The installer
contains no secrets; it is the same ISO you could publish publicly.

The `RECOVERY` partition lives on the drive's fingerprint-protected area and
only mounts after biometric authentication. The bundle is not separately
encrypted — the drive's hardware encryption is the sole protection layer.

### The recovery bundle (on partition `RECOVERY`)

A single file, `recovery-bundle.tar`, stored unencrypted on the
fingerprint-protected partition. Physical security of the drive is the
trust boundary.

#### Contents

```
recovery-bundle/
├── host-keys/                # the trust anchor — at least 2 hosts for redundancy
│   ├── razy/
│   │   ├── ssh_host_ed25519_key
│   │   └── ssh_host_ed25519_key.pub
│   └── timy/
│       ├── ssh_host_ed25519_key
│       └── ssh_host_ed25519_key.pub
├── nix-secrets-clone/        # shallow git clone with origin configured
├── nix-config-clone/       # shallow git clone with origin configured
├── github-pat.txt            # GitHub PAT with repo + admin:public_key scopes
├── vps-ssh-key               # SSH key authorized on the VPS git server
├── vault.kdbx                # keepassxc DB (passwords + TOTP, keyfile-protected)
├── vault.kdbx.key            # the keyfile
└── MANIFEST                  # plain text, lists contents + creation date
```

**Design note — no admin key.** An earlier iteration included a standalone
admin age key in the bundle. That has been removed: the SSH host keys under
`host-keys/` are the trust anchor. Restoring any one of them onto fresh
hardware during stage 1 makes the new machine inherit that host's identity,
and sops-nix decrypts `secrets.yaml` normally on first boot. No separate
admin role, no special re-encryption step. This reduces concepts, eliminates
a rotation schedule for a seldom-used key, and keeps the bundle contents
exactly mirroring things that already exist on running hosts.

**Why at least two host keys.** The bundle is the single point of failure
for disaster recovery. Backing up two hosts' keys (not just one) means losing
one of those hosts between bundle refreshes does not destroy your recovery
path. Three is excessive; two is sufficient and keeps the bundle small.

The git directories are **shallow clones with origin configured**, not
archive snapshots. After network is restored post-disaster, `git pull`
converges to latest — avoids the trap of recovering to a stale config.

#### Creating the bundle

Bundle creation is part of the auto-backup flow (see below), not a manual
step. If you need to force a bundle refresh:

```bash
just backup-bundle
```

The bundle is regenerated (not rotated) each time — the previous version is
overwritten. This is intentional: the bundle must always reflect current
reality, otherwise you will be locked out of newly-added secrets.

#### Restoring from the bundle

```bash
tar xvf /run/media/$USER/RECOVERY/recovery-bundle.tar -C /tmp/recovery
```

Two distinct restore scenarios:

1. **Reinstalling an existing host** — restore that host's own SSH key before
   first boot, from `/tmp/recovery/recovery-bundle/host-keys/<hostname>/`.
   Stage 1 supports this via `nix run .#install --restore-host-key <path>`,
   which places the key into `/mnt/etc/ssh/` before activation. The reinstalled
   machine inherits its prior identity, and sops-nix decrypts secrets normally
   on first boot. No re-admission step.

2. **Bootstrapping a brand new host from bundle (all other hosts lost)** —
   restore **some other trusted host's** SSH key (e.g. razy's key onto
   hardware that will become the replacement razy). The new hardware
   effectively takes over the old host's sops identity. Once it is running
   and can pull nix-secrets + rebuild, rotate to a fresh key:
   - Let the new host generate its own fresh SSH key
   - Run `admit-host --set-host-key <hostname> <age-key>` to write the fresh
     key into `nix-secrets/hosts.nix` and re-encrypt with both keys active
   - Remove the restored-from-bundle key by editing the adopted host's
     `sops.ageKey` in `hosts.nix` (or deleting its entry), then
     `just rotate-secrets`
   - Refresh the bundle with the new host key

This keeps the bundle's role narrow: it is a one-time bootstrap mechanism,
not a long-lived parallel identity.

### Auto-backup on plug-in

When the drive is plugged in and unlocked:

1. A udev rule matching the `RECOVERY` partition's UUID triggers a oneshot
   systemd service.
2. The udev rule sets `UDISKS_IGNORE=1` on the `RECOVERY` and `MEDIA`
   partitions so GNOME does **not** race to automount them — we mount them
   ourselves at `/mnt/recovery` and `/mnt/media` to keep paths stable.
3. The service:
   - Regenerates `recovery-bundle.tar` on `RECOVERY`
   - Runs `restic backup ~/` to `RECOVERY`'s restic repo (excludes caches,
     node_modules, build artifacts per `~/.backup-excludes`)
   - Runs `rsync -a --delete ~/Media/{Pictures,Videos,Music}/ /mnt/recovery/media/`
     for media files
   - Sends a desktop notification when done (or on failure)
   - Unmounts (optional; the drive is auto-unlocked again next time)

The `NIXOS_INSTALL` partition is **not** updated on every plug-in — it is
refreshed manually via `just refresh-installer-usb`, since the ISO changes
infrequently.

GNOME automount behavior: normally GNOME mounts removables under
`/run/media/$USER/<label>`. By setting `UDISKS_IGNORE=1` in the udev rule
for our specific drive UUID, GNOME leaves it alone and our service owns the
mount at a predictable location. This avoids path races and makes the udev
→ systemd service flow simpler.

---

## Prerequisite: private remote setup — TODO

> **Status:** Not yet done. Tracked as a follow-up.

Before the full flow is possible, a private remote must host:

1. **nix-secrets git remote** at
   `git+ssh://git@example.com/path/to/nix-secrets.git`
   - Bare repo owned by a `git` user
   - `authorized_keys` file **generated declaratively** from
     `machines/defs.nix` (each host's `userSshPubKey` field) as part of
     the private remote's NixOS config
   - Initial push from the current admin machine

Note: syncthing peer discovery does not require the private remote.

---

## Stage 2 walkthrough — peer path

On the new host after stage 1 completes:

```bash
nix run github:ausbxuse/nix-config#enroll
```

The script:

1. Generates a fresh ed25519 user SSH key at `~/.ssh/id_ed25519`
2. Computes the host's age key: `ssh-to-age < /etc/ssh/ssh_host_ed25519_key.pub`
3. Prints a block the user can paste into the admitting peer:

   ```
   HOSTNAME:   razy-2
   AGE KEY:    age1...
   USER SSH:   ssh-ed25519 AAAA... zhenyu@razy-2
   FINGERPRINT: SHA256:...
   ```

4. Prompts: "Run `nix run .#admit-host -- --set-host-key razy-2 <age-key>`
   on a trusted host, then press Enter to continue."
5. On the admitting peer, `admit-host --set-host-key`:
   - Patches `machines/defs.nix` to set `sops.ageKey` for the named host
     (host entry must already exist in `defs.nix`)
   - Regenerates `nix-secrets/.sops.yaml` from `nix-secrets/hosts.nix`
   - Runs `sops updatekeys -y nix-secrets/secrets.yaml` — succeeds because
     the peer's own host age key can decrypt
   - (Manual follow-up) commit both repos; GitHub/syncthing propagation
     is handled by `enroll`, not `admit-host`
6. Back on the new host, the user presses Enter. The script:
   - Pulls nix-secrets from the VPS git remote (using the new user SSH key,
     which the peer just authorized via... wait — see open question below)
   - Runs `sudo nixos-rebuild switch --flake ... --override-input nix-secrets ...`
   - sops-nix materializes secrets on next activation
   - Starts syncthing; within seconds, milky (the introducer) propagates the
     new device to the rest of the mesh; `vault.kdbx` begins syncing
7. Done. New host is a trusted peer.

### Bootstrap gap resolution (option C)

The new host's user SSH key must be authorized on the VPS git server before
step 6 can pull nix-secrets. The chosen resolution:

The plan (not yet implemented): `enroll` will do **both** for user SSH keys:

1. **Declaratively:** edits `machines/defs.nix` to add the new host's
   `userSshPubKey` field alongside `sops.ageKey`. Milky's NixOS config reads
   from this file to generate `authorized_keys` for the git user. Single
   source of truth.
2. **Imperatively:** for immediate effect (before milky's next rebuild),
   SSHes to milky and appends the new key to `authorized_keys` directly.
   Milky's next `nixos-rebuild switch` will converge to the same file.

This requires the admitting peer to have SSH access to milky as a user that
can write to the git user's `authorized_keys`. That access is bootstrapped
by the admin's initial manual milky setup.

---

## Stage 2 walkthrough — bundle path

On the new host, no peer available. This path works by **temporarily adopting
a trusted host's identity** from the bundle, then rotating to a fresh
per-host key once the new machine is functional.

```bash
sudo mount /dev/disk/by-label/RECOVERY /mnt/usb
nix run github:ausbxuse/nix-config#enroll -- --bundle /mnt/usb/recovery-bundle.tar
```

The script:

1. Extracts the bundle to a tmpfs under `/run/user/1000/recovery`
2. Generates a fresh user SSH key for the new host
3. Lists the backed-up hosts under `host-keys/` and asks which identity to
   adopt temporarily (e.g. "razy" if razy is gone, "timy" otherwise).
   Installs that host's SSH key into `/etc/ssh/ssh_host_ed25519_key` on the
   new machine.
4. Uses the bundle's `nix-secrets-clone/` as the nix-secrets source:
   `nixos-rebuild switch --flake ... --override-input nix-secrets path:<bundle>/nix-secrets-clone`
5. sops-nix decrypts `secrets.yaml` on activation using the adopted host key.
6. Copies `vault.kdbx` and keyfile into `~/vault/`.
7. **Rotates away from the adopted identity**: generates a fresh host SSH
   key, writes its derived age key into `machines/defs.nix` via
   `admit-host --set-host-key`, runs `sops updatekeys`, then removes the
   adopted host's `sops.ageKey` entry and rotates again. The new machine
   now has its own unique identity.
8. Queues changes to push to the VPS git remote once reachable:
   - Writes `~/.local/state/enroll-pending-push/` with commits to push
   - A systemd timer attempts the push daily until it succeeds
9. Prints: "Bundle-enrolled host. Syncthing pairing with milky is
   **manual** — no peer was available to propagate. Run `just syncthing-pair
milky` once network/VPS is reachable."
10. Unmounts and wipes the tmpfs.

At no point does the adopted host key persist as a "backup identity" — it is
replaced before the script exits. The bundle is strictly bootstrap.

---

## Adding a new host in steady state

The peer path above. In summary:

1. Add the new host to `machines/defs.nix` (regular host entry; leave
   `sops.ageKey` absent for now)
2. On the new host: `nix run .#install` (stage 1)
3. On the new host: `nix run .#enroll` (stage 2, peer path) — this
   derives the host's age key and calls
   `admit-host --set-host-key <hostname> <age-key>` on the peer
4. Wait for syncthing to sync vault

Total time: roughly 5 minutes after stage 1 finishes.

---

## Day-to-day: editing secrets

On any trusted host:

```bash
cd ~/src/private/nix-secrets
sops secrets.yaml         # uses host SSH key → age for decryption
git commit -a -m "add new secret"
git push
```

Then on consuming hosts:

```bash
sudo nixos-rebuild switch --flake ~/src/public/nix-config#<host>
```

No admin key required. Works offline as long as you are editing a copy you
can decrypt.

## Rotating a host SSH key safely

Host SSH key rotation must be done with an overlap window. Because each
host's sops age identity is derived from its SSH host key, cutting over to a
new host key before re-encrypting secrets would lock that host out of
`secrets.yaml`. The safe procedure is:

1. Generate a new host SSH keypair **alongside** the current one; do not
   install it yet.
2. Derive the new age recipient with `ssh-to-age < new_ssh_host_ed25519_key.pub`.
3. On a surviving trusted host, update `machines/defs.nix` so the rotating
   host temporarily lists **both** the old and new host age recipients.
4. Regenerate `nix-secrets/.sops.yaml` and run `sops updatekeys -y secrets.yaml`.
   At this point the host can decrypt with either key.
5. Install the new host SSH key on the target host, rebuild/reboot, and
   verify that decryption still works.
6. Remove the old host recipient from `machines/defs.nix`, regenerate
   `.sops.yaml`, and run `sops updatekeys -y secrets.yaml` again.
7. Delete the old host SSH key from disk and refresh the recovery bundle so it
   now contains only the new key.

Rotation is therefore **overlap, verify, remove** — never cut over in one step.

---

## Disaster recovery scenarios

### Scenario A: one host lost, others fine

1. On any surviving trusted host, remove the dead host from `machines/defs.nix`
2. `just rotate-secrets` — regenerates `.sops.yaml`, runs `sops updatekeys`
3. Push both repos
4. Other hosts rebuild on next cycle

No secret value actually needs to change — you are just removing a recipient
from the encrypted file. But you **should** rotate any secret that was
materialized onto the lost host's disk (API keys, tokens). Keep a list in
`~/vault/notes/secrets-on-host-<hostname>.md` per host to make this tractable.

### Scenario A2: reinstall an existing host from the bundle

This is distinct from losing a host permanently. If the machine being
reinstalled is still the **same logical host** (same hostname, same place in
`machines/defs.nix`), restore its saved SSH host key from the recovery bundle
before first boot:

1. Boot installer ISO on the target machine
2. Decrypt the recovery bundle
3. Restore `host-keys/<hostname>/ssh_host_ed25519_key{,.pub}` into the new
   installation as `/etc/ssh/ssh_host_ed25519_key{,.pub}`
4. Run `nix run .#install` with an explicit restore-host-key path or
   equivalent mechanism
5. Boot the installed system; the host retains the same derived age identity
6. Rebuild normally; secrets decrypt immediately without peer re-admission

If the old host key is suspected compromised, do **not** restore it. Treat the
reinstall as a new host admission and remove the old recipient from
`.sops.yaml`.

### Scenario B: VPS (milky) lost

- nix-secrets remote is gone — recover from any trusted host's local clone
  by pushing to a replacement VPS
- syncthing introducer is gone — mesh keeps working peer-to-peer; new hosts
  need manual pairing until new introducer is up
- Bundle push queue will pile up harmlessly until replacement VPS exists

### Scenario C: bundle lost, hosts fine

- Run `just backup-bundle` on any trusted host with USB access; a fresh
  bundle is written with current host keys.
- Nothing else to do — no admin key to regenerate, because there is no
  admin key.

### Scenario D: everything lost except the bundle

The worst case, and the reason the bundle exists.

1. Boot installer ISO (from the bundle USB's `NIXOS_INSTALL` partition)
2. Mount the recovery bundle USB, extract the bundle
3. `nix run .#install --restore-host-key <bundle>/host-keys/razy/ssh_host_ed25519_key`
   — the new hardware takes over razy's identity temporarily
4. `nix run .#enroll -- --bundle /mnt/usb/recovery-bundle.tar`
   - Script rebuilds with `--override-input nix-secrets path:<bundle>/nix-secrets-clone`
   - sops-nix decrypts secrets using the restored razy key
   - Script then rotates to a fresh host SSH key so the recovered machine
     no longer shares identity with the "old razy" record
5. Re-push nix-secrets and nix-config from the bundle's clones to a
   freshly enrolled git remote on a new VPS once it exists.
6. Build remaining personal hosts from this one via the peer path.

Recovery time from zero: 30–60 minutes assuming hardware is available.

### Scenario E: sops-nix activation fails on first boot (lockout recognition)

Symptom: after a reinstall or host-key change, `nixos-rebuild switch` builds
successfully but the activation step fails with a sops decryption error.
This means the current host SSH key is **not** in `.sops.yaml`'s recipient
list.

Diagnosis:

```bash
cat /etc/ssh/ssh_host_ed25519_key.pub | ssh-to-age
# Compare to entries in nix-secrets/.sops.yaml
```

Recovery depends on available resources:

- **Another trusted host reachable** → run
  `admit-host --set-host-key <hostname> <age-key>` there with this host's
  current age key, push, then retry `nixos-rebuild switch`.
- **Recovery bundle available** → proceed as Scenario D/A2: restore a known
  key from `host-keys/`, rebuild, then rotate to a fresh key.
- **Neither available** → you are locked out. The encrypted file is
  cryptographically intact but no known key can decrypt it. Rebuild all
  affected secrets from their upstream sources (new API tokens, new
  passwords, etc.). This is why the bundle + host-key backup combination
  must **always** exist.

This scenario is the reason the "30-second safety step" in the README is a
hard prerequisite to any other work on this system.

### Known sops-nix limitation: initrd secrets

sops-nix does not fully support initrd secrets — `nixos-rebuild switch`
installs the bootloader before running sops-nix's activation hook, so any
secret needed in early boot (initrd) is not available. This design avoids
relying on initrd secrets; if a future service requires one, it needs a
different mechanism (e.g. a clear-text file in the initrd from a TPM-unlocked
source, or manual LUKS unlock).

---

## Phase F: vault bootstrap (KeePassXC + syncthing)

Phase F adds syncthing-managed folders and installs KeePassXC. The module
at `modules/home/syncthing.nix` is already imported by the `personal-gnome`
profile, so after a rebuild a host gets:

- `keepassxc` installed
- Syncthing running with four declared folders:
  - `vault` — KeePassXC database, keyfile, notes (versioned, 10 copies)
  - `media` — `~/Media` (bidirectional, excludes `Phone/`)
  - `documents` — `~/Documents` (bidirectional)
  - `phone-dcim` — `~/Media/Phone` (receive-only, phone ingest)
- `phone-media-sort` systemd timer that copies files from `~/Media/Phone`
  into `~/Media/{Pictures,Videos,Audio}` by MIME type every 15 minutes
- Directories pre-created: `~/vault`, `~/Media/{Phone,Pictures,Videos,Audio}`,
  `~/Documents`

### Introducer model

Razy is the syncthing introducer (`syncthing.introducer = true` in
`defs.nix`). Clients only declare razy as a peer; razy introduces them to
each other automatically. This means:

- New enrolled hosts are auto-discovered by all existing clients
- No need to list every host's device ID for peering purposes
- Razy must be online for initial introductions; after that, peers
  remember each other and sync directly

### F.1 — automated path (enrolled hosts)

For hosts enrolled via `nix run .#enroll`, syncthing identity is
handled automatically:

1. enroll generates a fresh cert/key on the admitting peer (razy)
2. Encrypts cert/key into `nix-secrets/secrets.yaml` as
   `syncthing-cert-<hostname>` and `syncthing-key-<hostname>`
3. Records the device ID in `machines/defs.nix` under
   `syncthing.deviceId`
4. On first rebuild, `modules/home/syncthing.nix` pins the identity via
   sops — reinstalls retain the same device ID

No manual steps needed.

### F.2 — manual path (existing hosts like razy)

For the first host (razy) or any host that was set up before enroll
existed, pin the existing syncthing identity manually:

```bash
# 1. Grab the existing cert/key and device ID
CERT=~/.local/state/syncthing/cert.pem
KEY=~/.local/state/syncthing/key.pem
syncthing cli show system | jq -r .myID
# or: open http://127.0.0.1:8384 → Actions → Show ID

# 2. Wrap as JSON strings for sops
jq -Rs . < "$CERT" > /tmp/cert.json
jq -Rs . < "$KEY"  > /tmp/key.json

# 3. Encrypt into nix-secrets
cd ~/src/private/nix-secrets
sops set secrets.yaml '["syncthing-cert-razy"]' "$(cat /tmp/cert.json)"
sops set secrets.yaml '["syncthing-key-razy"]'  "$(cat /tmp/key.json)"
rm /tmp/cert.json /tmp/key.json

# 4. Record device ID in hosts.nix
```

```nix
razy = {
  # ...
  syncthing.deviceId = "ABCDEFG-HIJKLMN-...";  # full 56-char ID
  syncthing.introducer = true;
  # ...
};
```

```bash
# 5. Rebuild and verify
sudo nixos-rebuild switch --flake .#razy
systemctl --user restart syncthing
syncthing cli show system | jq -r .myID   # should match step 1
```

If the device ID changes, the pinned cert was not loaded. Check
`journalctl --user -u syncthing` and
`ls -l /run/user/1000/secrets/syncthing-*-razy`.

### F.3 — phone setup

The phone is not managed by this repo. Manual setup:

1. Install Syncthing on the phone (F-Droid or Play Store)
2. Copy the phone's device ID from the Syncthing app
3. On razy's Syncthing web UI (`127.0.0.1:8384`), add the phone as a
   device and share `phone-dcim` (point it at the phone's DCIM directory)
4. Optionally share `vault` for mobile KeePassXC access
5. Accept the share on the phone
6. Set the phone's DCIM folder to **send only** in the phone's Syncthing
   settings — this prevents desktop-side moves from propagating back

Since razy is the introducer, other hosts auto-discover the phone and
receive `phone-dcim` as a receive-only folder. The `phone-media-sort`
timer on each host sorts incoming files into the organized media tree.

**Freeing phone space:** files moved out of `~/Media/Phone` by the sort
service won't re-download (receive-only folders don't auto-revert local
changes). Delete old files on the phone directly whenever you need space;
the sorted copies on desktops are unaffected.

### F.4 — vault creation (first time only)

After razy's first rebuild with syncthing running, create the vault:

1. Open KeePassXC
2. Create a new database at `~/vault/vault.kdbx`
3. Set a strong master password
4. Add a keyfile at `~/vault/vault.kdbx.key`
5. The vault syncs to all peers automatically

---

## Testing in a VM

### Phase A — manual E2E runbook (works today)

A fully automated test is Phase B+; the runbook below exercises every real
component end-to-end in ~5 minutes using `nix run .#nixos-system-install-test`
(which keeps the installed VM running for inspection) plus `admit-host`.

**0. Commit the Phase A changes** so razy's repo state matches what you'll
push into the VM:

```bash
cd ~/src/public/nix-config
git add machines/defs.nix \
        scripts/admit-host.sh scripts/enroll.sh pkgs/default.nix flake.nix \
        Justfile modules/home/sops.nix modules/nixos/sops.nix \
        modules/profiles/nixos/minimal.nix secrets/nix-secrets/
git commit -m 'phase A: staging defs + admit-host tooling'
( cd ~/src/private/nix-secrets && git add hosts.nix .sops.yaml secrets.yaml home.nix system.nix \
    && git commit -m 'phase A: switch to per-host age keys' )
```

**1. Boot + install a clean VM** (uses the public stub nix-secrets, so no
decryption on first boot — install just works):

```bash
cd ~/src/public/nix-config
KEEP_VM=1 nix run .#nixos-system-install-test
```

Wait for "installed NixOS VM running". SSH target: `127.0.0.1:2224`, user
`zhenyu`, password `nixos`, hostname `custom-nixos`.

**2. Grab the VM's freshly-generated host pubkey and derive its age identity:**

```bash
VM_HOST_PUB=$(sshpass -p nixos ssh -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null -p 2224 zhenyu@127.0.0.1 \
  'cat /etc/ssh/ssh_host_ed25519_key.pub')
VM_AGE_PUB=$(echo "$VM_HOST_PUB" | nix run nixpkgs#ssh-to-age --)
echo "VM age pubkey: $VM_AGE_PUB"
```

**3. Admit the VM from razy** — patches `machines/defs.nix` to set
`custom-nixos.sops.ageKey`, regenerates `.sops.yaml`, re-encrypts `secrets.yaml`
for all recipients (razy + custom-nixos). Requires `custom-nixos` to
already exist in `defs.nix`.

```bash
cd ~/src/public/nix-config
sudo -E nix run .#admit-host -- --set-host-key custom-nixos "$VM_AGE_PUB"
```

**4. Push the updated private nix-secrets into the VM:**

```bash
sshpass -p nixos rsync -a --delete \
  -e "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p 2224" \
  ~/src/private/nix-secrets/ zhenyu@127.0.0.1:/tmp/nix-secrets/
```

**5. Rebuild inside the VM with the real secrets input:**

```bash
sshpass -p nixos ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  -p 2224 zhenyu@127.0.0.1 bash <<'EOF'
set -euxo pipefail
cd ~/src/public/nix-config 2>/dev/null || cd /etc/nixos
sudo nixos-rebuild switch --flake .#custom-nixos \
  --override-input nix-secrets path:/tmp/nix-secrets \
  --show-trace
sudo ls -la /run/secrets/
EOF
```

**Pass criteria:**

- Step 5 `nixos-rebuild` succeeds → proves sops-nix activation with the VM's
  freshly-generated host key works end-to-end.
- `ls /run/secrets/` shows decrypted files owned by root.

**6. Teardown** (critical — don't leave `custom-nixos` as a trusted host):

```bash
cd ~/src/public/nix-config
# Remove the custom-nixos host entry from ~/src/private/nix-secrets/hosts.nix
# (or, if it was never admitted, from public machines/defs.nix),
# then rotate:
sudo -E nix run .#admit-host
pkill -f 'qemu.*nixos-installer' || true
```

Steps 2–5 are exactly what the Phase B `enroll` script will fold into
`nix run .#enroll -- custom-nixos@127.0.0.1`.

### Phase B+ — automated test (future)

The nix-config repo already has a VM test harness
(`tests/nixos-installer-vm.nix`). Once `enroll` exists, the flow will be:

1. VM boots the installer ISO
2. Test runs `nix run .#install` to a target disk
3. VM reboots into the installed system
4. Test runs `nix run .#enroll` with a mocked peer:
   - A fake "peer" is a second VM that has a pre-seeded nix-secrets
   - The test drives `enroll` on the new VM, which calls
     `admit-host --set-host-key` on the peer VM in lockstep
5. Test asserts:
   - New VM's age key is in `.sops.yaml` after enroll
   - `secrets.yaml` decryptable by new VM's host key
   - `vault.kdbx` materializes in `~/vault/`
   - `ANTHROPIC_API_KEY` is exported in the user's shell
   - The new VM can itself enroll a third VM (validates the
     "every trusted host can admit future hosts" property)

The bundle path can be tested similarly with a pre-built bundle fed into a
single VM — no peer needed for that test.

---

## Decisions locked in

| #   | Decision                  | Choice                                                                 |
| --- | ------------------------- | ---------------------------------------------------------------------- |
| 1   | User SSH key generation   | In `enroll`, not `install` (keeps stage 1 anonymous)                   |
| 2   | Bootstrap gap (peer path) | Option C: declarative + imperative (see section above)                 |
| 3   | Bundle location on USB    | Fixed: `/mnt/recovery/recovery-bundle.tar`                             |
| 4   | Bundle encryption         | None — drive hardware encryption is the trust boundary                 |
| 5   | `enroll` idempotency      | State file at `~/.local/state/enroll/state.json`                       |
| 6   | GitHub PAT                | Reuse existing `github` entry in `secrets.yaml`                        |
| 7   | Syncthing folders         | Three: `vault`, `media`, `documents`                                   |
| 8   | Trusted-peer whitelist    | None — every enrolled host can admit future hosts                      |
| 9   | Admin key on razy         | Move into bundle, delete from `~/.config/sops/age/keys.txt`            |
| 10  | USB drive triple-role     | Installer + recovery bundle + rolling backup                           |
| 11  | Backup trigger            | Auto on plug-in via udev + systemd                                     |
| 12  | GNOME automount conflict  | `UDISKS_IGNORE=1` for our drive's partitions                           |
| 13  | milky VPS setup           | TODO — separate follow-up, not blocking                                |
| 14  | Host key recovery         | Recovery bundle stores per-host SSH host keys for same-host reinstalls |
| 15  | Host key rotation         | Overlap old+new recipients, verify, then remove old key                |

## Final locked-in details

| #   | Item                                         | Choice                                                              |
| --- | -------------------------------------------- | ------------------------------------------------------------------- |
| 16  | Drive mixes encrypted/unencrypted partitions | Yes (confirmed)                                                     |
| 17  | Installer ISO scope                          | Spacy-style minimal GNOME + terminal + NetworkManager + offline installer |
| 18  | Canonical media path                         | `~/Media/{Pictures,Videos,Music}/`                                  |
| 19  | Drive size                                   | ~900 GB, dedicated mostly to media mirror                           |
