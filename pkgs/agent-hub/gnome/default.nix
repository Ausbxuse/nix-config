# Agent Hub state dots for the GNOME top bar (uuid agent-dots@local).
{runCommand}:
runCommand "gnome-shell-extension-agent-dots" {} ''
  mkdir -p $out/share/gnome-shell/extensions
  cp -r ${./extension} $out/share/gnome-shell/extensions/agent-dots@local
''
