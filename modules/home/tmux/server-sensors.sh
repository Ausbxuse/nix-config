#!/usr/bin/env bash
# tmux status segment for always-on servers: fastest fan and room loudness.
# Prints nothing for a value whose source is missing, so non-server hosts
# show no extra segment. CPU temperature is already in statusbar/cpu.

fan=0
have_fan=0
for f in /sys/class/hwmon/hwmon*/fan*_input; do
  [ -r "$f" ] || continue
  read -r rpm < "$f" 2>/dev/null || continue
  [[ "$rpm" =~ ^[0-9]+$ ]] || continue
  have_fan=1
  [ "$rpm" -gt "$fan" ] && fan=$rpm
done

mic_db=""
mic_file=/var/lib/server-mic/mic.prom
if [ -r "$mic_file" ]; then
  up=$(awk '$1 == "server_mic_up" { print $2 }' "$mic_file")
  ts=$(awk '$1 == "server_mic_last_sample_timestamp_seconds" { print $2 }' "$mic_file")
  # Samples arrive every 30 s; hide a reading older than two minutes.
  if [ "$up" = 1 ] && [ -n "$ts" ] && [ $(( $(date +%s) - ${ts%.*} )) -lt 120 ]; then
    mic_db=$(awk '$1 == "server_mic_rms_dbfs" { printf "%d", $2 }' "$mic_file")
  fi
fi

out=""
# Round to 0.1k RPM; the raw value moves on every 2 s refresh.
[ "$have_fan" = 1 ] && out="F$(( (fan + 50) / 1000 )).$(( (fan + 50) % 1000 / 100 ))k"
[ -n "$mic_db" ] && out="${out:+$out }${mic_db}dB"
[ -n "$out" ] && printf '%s ' "$out"
