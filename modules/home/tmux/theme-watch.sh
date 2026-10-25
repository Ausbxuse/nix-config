#!/usr/bin/env bash
set -euo pipefail

if ! command -v gsettings >/dev/null 2>&1; then
  exit 0
fi

theme_cache="${XDG_CACHE_HOME:-$HOME/.cache}/tmux/theme"

# Wallpapers are set declaratively (modules/home/gnome/dconf.nix); GNOME picks
# picture-uri or picture-uri-dark from the color scheme on its own.

detect_theme() {
  local scheme
  scheme=$(gsettings get org.gnome.desktop.interface color-scheme 2>/dev/null | tr -d "'")
  case "$scheme" in
    *dark*) printf "dark" ;;
    *light*) printf "light" ;;
    *) printf "light" ;;
  esac
}

# GNOME 46 only ever loads a user theme's gnome-shell.css, so the shell
# (top bar, quick settings) stays dark in light mode. Switch between the Snappy
# and Snappy-Light user themes there; GNOME 47+ swaps stylesheets itself.
shell_needs_switch=0
case "$(gnome-shell --version 2>/dev/null)" in
  *" 45."* | *" 46."*) shell_needs_switch=1 ;;
esac

apply_shell_theme() {
  local want have
  [ "$shell_needs_switch" = 1 ] || return 0
  want=Snappy
  [ "$1" = light ] && want=Snappy-Light
  have=$(gsettings get org.gnome.shell.extensions.user-theme name 2>/dev/null | tr -d "'") || return 0
  case "$have" in
    Snappy | Snappy-Light) [ "$have" = "$want" ] || gsettings set org.gnome.shell.extensions.user-theme name "$want" || true ;;
  esac
}

apply_theme() {
  local theme
  theme=$(detect_theme)
  mkdir -p "$(dirname "$theme_cache")"
  printf "%s\n" "$theme" > "$theme_cache"

  apply_shell_theme "$theme"

  # Frosted top bar: pale in light mode, blur-my-shell's darker default otherwise.
  if [ "$theme" = light ]; then panel_pipeline=pipeline_panel_light; else panel_pipeline=pipeline_default; fi
  dconf write /org/gnome/shell/extensions/blur-my-shell/panel/pipeline "'$panel_pipeline'" 2>/dev/null || true


  # Claude Code reloads ~/.claude/themes live; swap the palette in place.
  claude_src="${XDG_DATA_HOME:-$HOME/.local/share}/claude-themes/snappy-$theme.json"
  if [ -f "$claude_src" ]; then
    mkdir -p "$HOME/.claude/themes"
    cp -f "$claude_src" "$HOME/.claude/themes/.snappy.json.tmp"
    chmod u+w "$HOME/.claude/themes/.snappy.json.tmp"
    mv -f "$HOME/.claude/themes/.snappy.json.tmp" "$HOME/.claude/themes/snappy.json"
  fi

  if tmux ls >/dev/null 2>&1; then
    tmux set-environment -g TMUX_COLOR_SCHEME "$theme" >/dev/null 2>&1 || true
    tmux source-file "$HOME/.config/tmux/tmux.conf" >/dev/null 2>&1 || true
  fi
}

apply_theme
# Home Manager rewrites the user-theme name to "Snappy" on every switch; put
# the variant matching the current scheme back.
if [ "$shell_needs_switch" = 1 ]; then
  gsettings monitor org.gnome.shell.extensions.user-theme name | while read -r _; do
    apply_shell_theme "$(detect_theme)"
  done &
fi
gsettings monitor org.gnome.desktop.interface color-scheme | while read -r _; do
  apply_theme
done
