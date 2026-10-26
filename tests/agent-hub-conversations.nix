{
  lib,
  runCommand,
  python3,
  nodejs,
  tmux,
}: let
  source = lib.cleanSourceWith {
    src = ../pkgs/agent-hub;
    filter = path: type:
      lib.cleanSourceFilter path type
      && !(builtins.elem (baseNameOf path) ["node_modules" "__pycache__"]);
  };
in
  runCommand "agent-hub-conversations" {
    nativeBuildInputs = [python3 nodejs tmux];
  } ''
    export PYTHONDONTWRITEBYTECODE=1
    export XDG_CACHE_HOME="$TMPDIR/cache"
    export XDG_STATE_HOME="$TMPDIR/state"
    cd ${source}
    # Synthetic logs, private tmux sockets and loopback HTTP only. No Codex
    # binary, model credentials or running user agents are needed.
    python3 -m unittest \
      test_transcripts test_transcript_replay test_claude_sessions \
      test_terminal_notices test_terminal_capture test_live
    node --test --test-reporter=dot test_history.cjs test_reader.cjs test_live.cjs
    touch "$out"
  ''
