# Razer Blade 16 (`razy`) Bring-Up Notes

This document records the actual debugging and fix path for `razy`, a Razer Blade 16 (`RZ09-0581`) running NixOS from this repo.

It is intentionally practical rather than polished. The point is to explain:

- what was broken
- what hypotheses were wrong
- what finally worked
- which parts are real fixes vs temporary workarounds
- what we later removed again

## Scope

This bring-up covered multiple issues on a very new Panther Lake laptop:

- internal audio did not enumerate
- brightness/backlight control was unreliable
- GDM monitor scaling was not applied
- suspend/resume was unstable
- GNOME wallpaper state after lid resume was inconsistent

The hardest issue by far was internal audio.

## Machine

- Hostname: `razy`
- Vendor: `Razer`
- Product: `Blade 16 - RZ09-0581`
- Platform: Intel Panther Lake
- Internal audio shape discovered during debugging:
  - `rt721`
  - `rt1320`
  - PCH DMIC present

## Initial Audio Symptoms

The original failure mode was:

- GNOME/PipeWire showed dummy output or only NVIDIA HDMI audio
- no usable internal speaker or mic
- `aplay -l` and `arecord -l` often showed no internal card
- kernel logs showed SOF firmware booting, then failing during topology load

The most important early failure looked like this:

```text
loading topology: intel/sof-ipc4-tplg/sof-ptl-rt721-2ch.tplg
Topology: ABI 3:29:1 Kernel ABI 3:23:1
error: can't connect DAI alh-copier.Capture-SmartMic.0 stream Capture-SmartMic
failed to add widget type 28 name : alh-copier.Capture-SmartMic.0
sof_sdw: failed to instantiate card -22
```

At that point the system never brought up a real `sof-soundwire` card.

## Early Hypotheses And Dead Ends

Several ideas were reasonable, but turned out to be incomplete or wrong.

### 1. "Maybe this is just missing firmware/topology files"

This was partly true early on, but not the real final blocker.

There was no true upstream `sof-ptl-rt721-2ch.tplg` in the shipped firmware set, and we temporarily experimented with compatibility shims. That helped reveal later failures, but it was not the real fix.

### 2. "Maybe the SOF ABI mismatch is the whole problem"

The mismatch was real:

- topology ABI: `3:29:1`
- kernel ABI: `3:23:1`

We spent time checking:

- upstream Linux
- `thesofproject/linux`
- public `sof-bin` PTL releases
- reports from Ubuntu, Fedora, SOF upstream, and others

This was useful context, but it did not fully explain the observed behavior.

Why:

- some newer Intel laptops still enumerate a working card despite the same mismatch
- `razy` was failing on a much more specific path: `Capture-SmartMic.0`

So the ABI mismatch was suspicious, but not sufficient to explain the actual board failure.

### 3. "Maybe the right fix is a different monolithic PTL topology"

We tried multiple topology-focused experiments:

- plain `rt721`
- synthetic `rt721-2ch` shims
- the closest upstream `rt722 + rt1320` PTL topology

These were valuable diagnostics because they moved the failure:

- from `Capture-SmartMic`
- to `SDW0-Playback`

That proved the active topology really mattered.

But it also proved there was no packaged monolithic PTL topology in the current firmware set that matched this Razer board cleanly.

### 4. "Maybe this is purely user-space"

It was not.

We checked PipeWire, WirePlumber, ALSA visibility, module reloads, and various runtime probes. Those were useful for narrowing state, but the decisive failures were always in the kernel-side SOF/SoundWire topology path.

## What The Real Problem Turned Out To Be

The actual fix was not "find the correct monolithic topology file."

The real problem was that the system kept falling back onto the wrong monolithic PTL topology path instead of staying on split function topologies.

Two observations mattered:

1. The failing monolithic path kept pulling in `Capture-SmartMic`, even though runtime descriptors on this machine did not support the expected SmartMic path in the way the chosen topology expected.

2. The SOF function-topology selector was too brittle for this board:
   - unrelated links such as BT offload could force fallback
   - plain `SDW<port>-Playback/Capture` names were not being mapped back to the right SDCA function fragments, even though the BE IDs already implied the function type

Once we stopped fighting about fake monolithic files and instead forced the machine onto split function topologies, the audio stack finally came up.

## The Original Working Audio Workaround

The original workaround lived in:

- [machines/razy/nixos.nix](/home/zhenyu/src/public/nix-config/machines/razy/nixos.nix)
- a local kernel patch for the Razer-specific PTL SoundWire description and
  function-topology selection

The local patch was removed after nixos-unstable moved to Linux 7.2, which
contains the generic SoundWire support and topology-selection fixes needed by
this hardware.

That kernel cleanup exposed a separate userspace issue: `alsa-ucm-conf 1.2.16.1`
selects the combined `rt721+rt1320` codec layout but does not ship the combined
initializer it then imports. The current host configuration supplies only that
missing composition file; it does not restore any kernel patch or topology shim.

### Kernel-side machine description

The patch added a Razer-specific PTL machine description:

- DMI match for `Razer Blade 16 - RZ09-0581`
- custom PTL SoundWire machine entry for:
  - `rt721`
  - `rt1320`
  - link 3
- SSID quirk for:
  - `0x1a58:0x3010`
- quirk bits:
  - `SOC_SDW_SIDECAR_AMPS`
  - `SOC_SDW_PCH_DMIC`

This gave the older kernel an explicit board model instead of hoping a near match would work.

### Forcing split function topologies

The crucial step was changing the Razer PTL machine entry to use:

```c
.sof_tplg_filename = "sof-ptl-dummy.tplg",
.get_function_tplg_files = sof_sdw_get_tplg_files,
```

This intentionally avoided a broken monolithic fallback path and kept SOF on split function topologies.

### Making split topology selection robust enough

The patch also modified `sof-function-topology-lib.c` to do two important things:

1. Ignore `SSP*-BT` links during function-topology selection.

These links are unrelated to the internal speaker/mic path and were able to push topology selection onto the wrong fallback path.

2. Infer function fragments from BE IDs even when the dai links still have plain names like:

- `SDW3-Playback`
- `SDW3-Capture`

Specifically:

- jack BE IDs map to the generic SDCA jack fragment
- amp BE IDs map to the SDCA amp fragment

This was the missing bridge between the board's dai links and the split topology fragments that actually existed in firmware. Linux 7.2 now provides that bridge through generic SoundWire machine discovery, RT721/RT1320 codec metadata, and unconditional DAI type naming for function-topology selection.

## The First Known-Good Boot

The successful boot stopped loading the old fake monolithic path and instead loaded split fragments like:

```text
Topology file: function topologies
Using function topologies instead intel/sof-ipc4-tplg/sof-ptl-dummy-2ch.tplg
loading topology 0: intel/sof-ipc4-tplg/sof-sdca-jack-id0.tplg
loading topology 1: intel/sof-ipc4-tplg/sof-sdca-1amp-id2.tplg
loading topology 2: intel/sof-ipc4-tplg/sof-ptl-dmic-2ch-id3.tplg
loading topology 3: intel/sof-ipc4-tplg/sof-hdmi-pcm5-id5.tplg
```

The earlier failing nodes disappeared:

- no more `Capture-SmartMic.0`
- no more `SDW0-Playback`

And the user confirmed that audio worked.

## Why This Took So Long

The hard part was that several things were true at once:

- the laptop is a very new Intel platform
- the board needed a specific machine description
- the available packaged PTL topology names were misleading
- the topology ABI mismatch was real, but not the only issue
- several experimental changes moved the failure without actually fixing the board

So the debugging path had to separate:

- useful movement in the failure
- from real causal fixes

## What Was Removed Again

Once audio worked, several earlier workarounds were dropped because they were no longer needed or were clearly the wrong layer.

Removed:

- fake `sof-ptl-rt721-2ch.tplg` compatibility overlays
- topology renaming shims
- `bt_link_mask=0` workaround in modprobe config
- redundant typed-dailink forcing path
- the long initrd crypto module override that was only needed to make a custom dev kernel package build

The repository now uses the upstream generic kernel implementation and carries
no local audio kernel patch. A small ALSA UCM composition file remains until
the combined RT721/RT1320 initializer is available upstream.

## Kernel Choice

The first known-good audio boot happened with a SOF development kernel source:

- `thesofproject/linux`
- `topic/sof-dev`
- version `7.0.0-rc3`

That was later switched to a stock-packaged Linux 6.19 kernel plus the local
Razer patch. The current configuration uses nixos-unstable's stock Linux 7.2
kernel with no host-specific kernel override.

Linux 7.2 contains the generic replacements for the local changes, including:

- RT721 and RT1320 SoundWire codec descriptions
- corrected SDCA endpoint discovery and RT1320 amp identification
- robust default SoundWire machine construction
- DAI type metadata for split function-topology selection

The physical laptop has now booted Linux 7.2. Kernel logs confirm that generic
SoundWire machine discovery found the RT721 jack, RT1320 amplifiers, and DMIC,
then loaded the expected jack, amp, and PTL DMIC topology fragments.

## Current ALSA UCM Workaround

The boot also revealed an `alsa-ucm-conf 1.2.16.1` packaging/configuration gap.
The card reports this speaker layout:

```text
spk:rt721+rt1320
```

The generic `sof-soundwire` UCM then imports:

```text
/codecs/rt721+rt1320/init.conf
```

That file is absent upstream even though the individual RT721 and RT1320
initializers are present. WirePlumber therefore fell back to a stereo PCM,
selected the jack path, and exposed no usable microphone.

The host-local file at
`machines/razy/alsa-ucm-conf/codecs/rt721+rt1320/init.conf` composes those two
existing initializers. `machines/razy/nixos.nix` adds it to an overridden
`alsa-ucm-conf` output and points WirePlumber at that UCM tree. With it loaded,
the HiFi profile exposes Speaker PCM 2, DMIC PCM 10, Headphones PCM 0, and
Headset Microphone PCM 1.

## Other `razy` Issues Fixed Along The Way

### Brightness

Brightness was fixed separately from audio.

The important setting is:

```nix
boot.kernelParams = [ "xe.enable_dpcd_backlight=1" ];
```

That is the actual backlight fix. `brightnessctl` is optional convenience, not part of the kernel fix itself.

### GDM scaling

GDM needed its own monitor configuration copied into the GDM home directory:

- [machines/razy/gdm-monitors.xml](/home/zhenyu/src/public/nix-config/machines/razy/gdm-monitors.xml)
- installed into `/var/lib/gdm/.config/monitors.xml`

This is separate from the user session GNOME scaling.

### Suspend instability

Suspend/resume was unstable while experimenting with the new GPU/kernel stack.

The immediate mitigation was:

```nix
hardware.nvidia.powerManagement.finegrained = lib.mkForce false;
```

That was a pragmatic debugging choice. The current host configuration has
since re-enabled fine-grained NVIDIA power management.

### Panther Lake `thermald`

`thermald` 2.5.12 marks Panther Lake CPU model `0xcc` as adaptive-only. This
laptop does not expose the INT3400 adaptive data vault that mode requires, so
the daemon rejects the platform and makes NixOS activation fail its unit health
check.

The host disables `thermald` instead of bypassing its CPUID safety check and
running the unsupported generic engine. Firmware/EC thermal control, kernel
thermal zones, `intel_pstate`, and TLP remain active.

### Blue wallpaper after lid resume

The blue background after resume was not caused by the static GNOME wallpaper settings anymore. The most likely remaining cause was the `azwallpaper` GNOME extension overriding wallpaper state and resuming badly.

That extension was disabled in:

- [modules/home/gnome/dconf.nix](/home/zhenyu/src/public/nix-config/modules/home/gnome/dconf.nix)

This leaves GNOME's declarative wallpaper settings as the only wallpaper source.

## Current Practical State

At the end of this debugging pass:

- Linux 7.2 detects the complete internal audio layout without a kernel patch
- the ALSA HiFi profile exposes internal speakers and microphones with the
  host-local UCM composition file
- the fake topology shims are gone
- the local audio patch and dedicated 6.19 kernel input are gone
- `razy` inherits the stock `linuxPackages_latest` kernel, currently Linux 7.2
- brightness works
- GDM scaling is configured
- NVIDIA fine-grained PM is enabled
- `thermald` is disabled because this firmware cannot provide its required
  Panther Lake adaptive data
- wallpaper handling is simpler again

## What Still Deserves Future Validation

### 1. Validate physical audio paths

Test speaker playback, internal-microphone capture, the headphone/headset jack,
and audio after suspend and resume before deleting the last known-good patched
system generation. Kernel enumeration, topology loading, and PipeWire endpoint
creation have already been validated on Linux 7.2.

### 2. Remove the UCM workaround after the upstream fix

Once `alsa-ucm-conf` ships a combined RT721/RT1320 initializer, remove the local
composition file and `ALSA_CONFIG_UCM2` override, then repeat the physical audio
tests.

## Files Most Relevant To `razy`

- [machines/razy/nixos.nix](/home/zhenyu/src/public/nix-config/machines/razy/nixos.nix)
- [machines/razy/alsa-ucm-conf/codecs/rt721+rt1320/init.conf](/home/zhenyu/src/public/nix-config/machines/razy/alsa-ucm-conf/codecs/rt721+rt1320/init.conf)
- [machines/razy/gdm-monitors.xml](/home/zhenyu/src/public/nix-config/machines/razy/gdm-monitors.xml)
- [machines/razy/power.nix](/home/zhenyu/src/public/nix-config/machines/razy/power.nix)
- [modules/home/gnome/dconf.nix](/home/zhenyu/src/public/nix-config/modules/home/gnome/dconf.nix)

## Short Version

The original audio fix was not "find the right PTL topology filename."

It required:

- give the kernel a real Razer-specific `rt721 + rt1320` PTL board description
- stop monolithic topology fallback
- force split function topologies
- make split topology selection tolerant of BT offload links
- infer jack/amp fragments from BE IDs when dai link names are still plain `SDW<port>-*`

Linux 7.2 now handles the board through generic SoundWire discovery and the
upstream RT721/RT1320 function-topology path, so the local implementation of
those kernel steps is no longer carried in this repository. The remaining
userspace workaround merely composes the individual codec initializers that
`alsa-ucm-conf` already ships.
