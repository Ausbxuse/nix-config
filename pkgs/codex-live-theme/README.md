Codex live terminal palette
==========================

Builds the release selected by `codex-cli-nix` with a small native TUI patch,
then installs that executable into sadjow's complete package layout.

The existing crossterm reader delivers OSC 10/11 replies alongside keyboard and
paste events. Codex requests the current palette on focus and once per second,
commits complete foreground/background pairs, retains the last valid palette
when replies are missing, and refreshes syntax, status and transcript caches.
Queries stop while Codex has handed the terminal to another program.

When upgrading sadjow, refresh the source/vendor hashes and review both patches
against its selected release. Remove this package override once upstream
provides asynchronous palette refresh and invalidates the rendered UI caches.

`test_runtime.py /path/to/bin/codex` exercises the packaged frontend in a real
PTY using Python with `pyte`. Run it from a trusted working directory. It keeps
one frontend alive through repeated switches, injects typing and paste around
fragmented color replies, and checks missing-response recovery. It uses a local
test provider and never submits a model turn or changes the saved configuration.
