{lib, pkgs}: let
  whisper = pkgs.whisper-cpp.override {
    cudaSupport = false;
    rocmSupport = false;
    vulkanSupport = false;
    withSDL = false;
    withFFmpegSupport = false;
  };
  speechModel = pkgs.fetchurl {
    url = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin";
    sha256 = "a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002";
  };
  # whisper-cpp links the ggml shipped by llama-cpp; older versions bundled it.
  ggml = lib.findFirst (p: (p.pname or "") == "llama-cpp") whisper whisper.propagatedBuildInputs;
  whisperWorker = pkgs.runCommandCC "agent-hub-whisper" {} ''
    mkdir -p "$out/bin"
    $CXX -std=c++17 -O2 -Wall -Wextra ${./whisper-worker.cpp} \
      -I${whisper}/include -I${lib.getDev ggml}/include \
      -L${whisper}/lib -L${ggml.out}/lib \
      -Wl,-rpath,${whisper}/lib -Wl,-rpath,${ggml.out}/lib \
      -lwhisper -lggml -lggml-base -o "$out/bin/agent-hub-whisper"
  '';
  kokoro = pkgs.python3Packages.callPackage ./kokoro.nix {};
  python = pkgs.python3.withPackages (pythonPackages: [kokoro pythonPackages.websocket-client pythonPackages.pillow]);
  voiceModel = pkgs.fetchurl {
    url = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx";
    sha256 = "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5";
  };
  voiceVoices = pkgs.fetchurl {
    url = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin";
    sha256 = "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d";
  };
  source = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      ./server.py
      ./bridge.py
      ./branching.py
      ./agent_switch.py
      ./codex_account.py
      ./federation.py
      ./transcripts.py
      ./screen_reader.py
      ./message_content.py
      ./claude_sessions.py
      ./claude_models.py
      ./controller_backup.py
      ./encrypted_backup.py
      ./backup_status.py
      ./assistant.py
      ./handoff-schema.json
      ./handoff-instructions.txt
      ./updates.py
      ./static_assets.py
      ./live.py
      ./delivery.py
      ./continuity.py
      ./archive.py
      ./research.py
      ./research-schema.json
      ./research-instructions.txt
      ./media.py
      ./thumbnails.py
      ./native.py
      ./terminal_queue.py
      ./approvals.py
      ./attachments.py
      ./plans.py
      ./projects.py
      ./task_facts.py
      ./lookup.py
      ./lookup-instructions.txt
      ./notifications.py
      ./recovery.py
      ./workflow.py
      ./coordination.py
      ./tls.py
      ./speech.py
      ./speech_work.py
      ./spoken_reply.py
      ./usage.py
      ./supervisor-schema.json
      ./static
    ];
  };
in
  pkgs.writeShellApplication {
    name = "agent-hub";
    runtimeInputs = [pkgs.tmux pkgs.openssh pkgs.procps pkgs.openssl pkgs.cacert whisper];
    text = ''
      export SSL_CERT_FILE="''${SSL_CERT_FILE:-${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt}"
      export AGENT_HUB_WHISPER_MODEL="''${AGENT_HUB_WHISPER_MODEL:-${speechModel}}"
      export AGENT_HUB_WHISPER_WORKER="''${AGENT_HUB_WHISPER_WORKER:-${whisperWorker}/bin/agent-hub-whisper}"
      export AGENT_HUB_VOICE_MODEL="''${AGENT_HUB_VOICE_MODEL:-${voiceModel}}"
      export AGENT_HUB_VOICE_VOICES="''${AGENT_HUB_VOICE_VOICES:-${voiceVoices}}"
      export AGENT_HUB_FFMPEG="${pkgs.ffmpeg-headless}/bin/ffmpeg"
      export AGENT_HUB_FFPROBE="${pkgs.ffmpeg-headless}/bin/ffprobe"
      export AGENT_HUB_PDFTOPPM="${pkgs.poppler-utils}/bin/pdftoppm"
      export AGENT_HUB_PRLIMIT="${pkgs.util-linux}/bin/prlimit"
      exec ${python}/bin/python3 ${source}/server.py "$@"
    '';
    meta = {
      description = "Local web cockpit and a unified supervisor for tmux agents";
      mainProgram = "agent-hub";
      platforms = lib.platforms.linux;
    };
  }
