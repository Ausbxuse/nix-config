"""Local Whisper transcription and Kokoro speech. Audio is never logged."""

from array import array
from collections import OrderedDict
import hashlib
import io
import importlib.util
import math
import os
from pathlib import Path
import re
import select
import shutil
import struct
import subprocess
import sys
import threading
import time
import wave

from speech_work import SpeechError, SpeechLane

MAX_SECONDS = 30
MAX_BYTES = MAX_SECONDS * 16000 * 2 + 4096
MAX_SPEECH_CHARS = 400


class WhisperWorker:
    """A bounded, restartable pipe to a resident model; no listening socket."""
    def __init__(self, executable, model, threads):
        self.command = [executable, str(model), str(threads)]
        self.process = None
        self.guard = threading.Lock()
        self.closed = False

    @staticmethod
    def read(process, size, deadline):
        result = bytearray()
        while len(result) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([process.stdout], [], [], remaining)[0]:
                raise TimeoutError()
            chunk = os.read(process.stdout.fileno(), size - len(result))
            if not chunk:
                raise OSError('Speech worker stopped')
            result.extend(chunk)
        return bytes(result)

    def start(self):
        with self.guard:
            if self.closed:
                raise OSError('Speech worker closed')
            if self.process and self.process.poll() is None:
                return self.process
            self.reset()
            process = self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        try:
            os.set_blocking(process.stdin.fileno(), False)
            if self.read(process, 4, time.monotonic() + 10) != b'HVW1':
                raise OSError('Invalid speech worker')
            return process
        except (OSError, ValueError):
            self.reset()
            raise

    def transcribe(self, pcm):
        try:
            process = self.start()
            deadline = time.monotonic() + 20
            message = memoryview(struct.pack('<I', len(pcm) // 2) + pcm)
            while message:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([], [process.stdin], [], remaining)[1]:
                    raise TimeoutError()
                count = os.write(process.stdin.fileno(), message)
                message = message[count:]
            size, = struct.unpack('<I', self.read(process, 4, deadline))
            if size > 48000:
                raise ValueError('Speech result is too large')
            return self.read(process, size, deadline).decode('utf-8')
        except (OSError, ValueError):
            self.reset()
            raise

    def reset(self):
        process, self.process = self.process, None
        if process:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            process.stdin.close()
            process.stdout.close()

    def close(self):
        with self.guard:
            self.closed = True
            self.reset()


class AudioCache:
    """Small, short-lived memory cache. Nothing is persisted or logged."""
    def __init__(self, limit=4 * 1024 * 1024, entries=16, seconds=120):
        self.items = OrderedDict()
        self.limit, self.entries, self.seconds = limit, entries, seconds
        self.lock = threading.RLock()

    def get(self, key):
        with self.lock:
            now = time.monotonic()
            for stale in [key for key, (until, _) in self.items.items() if until <= now]:
                del self.items[stale]
            if key in self.items:
                self.items.move_to_end(key)
                return self.items[key][1]

    def put(self, key, value):
        with self.lock:
            self.items[key] = (time.monotonic() + self.seconds, value)
            self.items.move_to_end(key)
            while len(self.items) > self.entries or sum(len(value) for _, value in self.items.values()) > self.limit:
                self.items.popitem(last=False)


class LocalSpeech:
    def __init__(self):
        self.model = Path(os.environ.get('AGENT_HUB_WHISPER_MODEL', '/nonexistent'))
        self.executable = os.environ.get('AGENT_HUB_WHISPER_WORKER') or shutil.which('agent-hub-whisper')
        self.lock = SpeechLane('asr')
        self.worker = None
        self.closed = False
        self.lifecycle = threading.Lock()
        self.transcripts = AudioCache(limit=48000, entries=4, seconds=15)
        self.voice_model = Path(os.environ.get('AGENT_HUB_VOICE_MODEL', '/nonexistent'))
        self.voice_voices = Path(os.environ.get('AGENT_HUB_VOICE_VOICES', '/nonexistent'))
        self.voice_speaker = os.environ.get('AGENT_HUB_VOICE_SPEAKER', 'af_heart')
        self.voice_available = importlib.util.find_spec('kokoro_onnx') is not None
        self.voice_lock = SpeechLane('tts')
        self.voice = None
        self.audio = AudioCache()

    def status(self):
        return {'available': bool(self.executable and self.model.is_file()),
                'engine': 'whisper.cpp', 'model': 'base.en', 'language': 'en',
                'max_seconds': MAX_SECONDS, 'interim': True,
                'playback': {'available': self.voice_available and self.voice_model.is_file() and self.voice_voices.is_file(),
                             'engine': 'kokoro', 'voice': self.voice_speaker, 'local': True}}

    def _get_worker(self):
        with self.lifecycle:
            if self.closed:
                raise OSError('Speech is closed')
            if self.worker is None:
                self.worker = WhisperWorker(self.executable, self.model, min(8, os.cpu_count() or 1))
            return self.worker

    def _get_voice(self):
        from kokoro_onnx import Kokoro
        import onnxruntime as ort
        if self.closed:
            raise OSError('Speech is closed')
        if self.voice is None:
            options = ort.SessionOptions()
            options.intra_op_num_threads = min(4, os.cpu_count() or 1)
            options.inter_op_num_threads = 1
            # Four threads beat two in local profiling; six brought no gain.
            # Reuse the CPU model between sentences; never wake the laptop GPU.
            self.voice = Kokoro.from_session(ort.InferenceSession(str(self.voice_model), sess_options=options,
                                            providers=['CPUExecutionProvider']), str(self.voice_voices))
        return self.voice

    def warmup(self):
        """Start loading while the caller speaks, without delaying capture."""
        def load(lock, function):
            try:
                function()
            except (ImportError, OSError, ValueError, RuntimeError):
                pass  # A real request reports errors and retries startup.
            finally:
                lock.release()
        status = self.status()
        for available, lock, function in [
            (status['available'], self.lock, lambda: self._get_worker().start()),
            (status['playback']['available'], self.voice_lock, self._get_voice),
        ]:
            if available and not self.closed and lock.acquire(blocking=False):
                threading.Thread(target=load, args=(lock, function), daemon=True).start()
        return {'ok': True}

    def close(self):
        self.lock.close()
        self.voice_lock.close()
        with self.lifecycle:
            self.closed = True
            if self.worker:
                self.worker.close()

    def cancel(self, value):
        lane = {'asr': self.lock, 'tts': self.voice_lock}.get(value.get('kind'))
        if lane is None:
            raise SpeechError('Invalid speech work type.')
        return lane.cancel(value)

    def speak(self, text, work=None):
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_SPEECH_CHARS or '\x00' in text:
            raise SpeechError(f'Speak between 1 and {MAX_SPEECH_CHARS} characters.')
        if not self.status()['playback']['available']:
            raise SpeechError('Local voice playback is not installed on this hub.', 503)
        text = text.strip()
        # A ready clip must not wait for an unrelated sentence's inference.
        cached = self.audio.get(text)
        with self.voice_lock.request(work, ready=cached is not None):
            return cached if cached is not None else self._speak(text)

    def _speak(self, text):
        try:
            cached = self.audio.get(text)
            if cached is not None:
                return cached
            import numpy as np
            voice = self._get_voice()
            samples, rate = voice.create(text, voice=self.voice_speaker, speed=1.02, lang='en-us',
                                         sentence_pause=.2, clause_pause=.08)
            # Match the previous rounding/clipping in one vectorized operation.
            pcm = np.clip(np.rint(np.asarray(samples, dtype=np.float64) * 32767), -32768, 32767).astype('<i2')
            output = io.BytesIO()
            with wave.open(output, 'wb') as wav:
                wav.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
                wav.writeframes(pcm.tobytes())
            audio = output.getvalue()
            self.audio.put(text, audio)
            return audio
        except (ImportError, OSError, ValueError, RuntimeError) as exc:
            raise SpeechError('Local voice playback failed. The reply is available as text.', 503) from exc

    def transcribe(self, audio, work=None):
        if not self.status()['available']:
            raise SpeechError('Local transcription is not installed on this hub.', 503)
        if not 44 <= len(audio) <= MAX_BYTES:
            raise SpeechError('Audio is empty or too large.', 413)
        try:
            with wave.open(io.BytesIO(audio), 'rb') as wav:
                if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1, 2, 16000, 'NONE'):
                    raise SpeechError('Audio must be 16 kHz mono PCM WAV.')
                frames = wav.getnframes()
                if not 0 < frames <= MAX_SECONDS * 16000:
                    raise SpeechError('Record up to 30 seconds per message.', 413)
                pcm = wav.readframes(frames)
                if len(pcm) != frames * 2:
                    raise SpeechError('Audio upload was incomplete.')
        except (wave.Error, EOFError) as exc:
            raise SpeechError('Audio could not be read. Tap the microphone to retry.') from exc
        duration_ms = round(frames / 16)
        samples = array('h', pcm)
        if sys.byteorder != 'little':
            samples.byteswap()
        rms = math.sqrt(sum(sample * sample for sample in samples) / frames) / 32768
        if duration_ms < 250 or rms < 0.003:
            return {'text': '', 'duration_ms': duration_ms, 'elapsed_ms': 0}
        key = hashlib.sha256(pcm).digest()
        cached = self.transcripts.get(key)
        with self.lock.request(work, ready=cached is not None):
            return self._transcribe(pcm, key, duration_ms, cached)

    def _transcribe(self, pcm, key, duration_ms, text=None):
        started = time.monotonic()
        try:
            if text is None:
                text = self.transcripts.get(key)
            cached = text is not None
            if not cached:
                text = self._get_worker().transcribe(pcm)
                text = re.sub(r'\[(?:BLANK_AUDIO|silence|no speech)\]', '', text, flags=re.I)
                text = re.sub(r'\s+', ' ', text).strip()[:12000]
                self.transcripts.put(key, text)
            return {'text': text, 'duration_ms': duration_ms, 'cached': cached,
                    'elapsed_ms': round((time.monotonic() - started) * 1000)}
        except TimeoutError as exc:
            raise SpeechError('Transcription took too long. Try a shorter message.', 504) from exc
        except (OSError, ValueError) as exc:
            raise SpeechError('Local transcription failed. Tap the microphone to retry.', 503) from exc
