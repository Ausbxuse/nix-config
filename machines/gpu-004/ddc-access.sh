#!/usr/bin/env bash
# One-time, with sudo: let the logged-in user talk to monitors over DDC/CI
# (ddcutil, the GNOME brightness sliders) without root. The rule is ddcutil's
# own uaccess rule; Ubuntu is not NixOS, so home-manager cannot install it.
set -euo pipefail
cat >/etc/udev/rules.d/60-ddcutil-i2c.rules <<'RULES'
SUBSYSTEM=="i2c-dev", KERNEL=="i2c-[0-9]*", ATTRS{class}=="0x03*", TAG+="uaccess"
SUBSYSTEM=="dri", KERNEL=="card[0-9]*", TAG+="uaccess"
RULES
udevadm control --reload-rules
udevadm trigger --subsystem-match=i2c-dev --action=change
