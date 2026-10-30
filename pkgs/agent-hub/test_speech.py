"""Local transcription boundaries; no microphone, agents, or model required."""
import io
import importlib.util
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave
from http.client import HTTPConnection
from concurrent.futures import ThreadPoolExecutor

from speech import LocalSpeech, SpeechError, WhisperWorker, AudioCache, MAX_BYTES
from server import Handler, ThreadingHTTPServer


def recording(seconds=1, level=.1, rate=16000):
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        wav.writeframes(b''.join(struct.pack('<h', round(level * 32767 * math.sin(i * 2 * math.pi * 220 / rate)))
                                 for i in range(round(seconds * rate))))
    return output.getvalue()


class SpeechTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.speech = LocalSpeech()
        self.addCleanup(self.speech.close)
        self.speech.model = Path(temp.name) / 'model.bin'
        self.speech.model.touch()
        self.speech.executable = '/unused/agent-hub-whisper'

    def test_transcript_reuses_exact_audio_but_never_an_earlier_prefix(self):
        worker = self.speech.worker = Mock()
        worker.transcribe.return_value = ' supervisor,  show the LAP agent. [BLANK_AUDIO] '
        result = self.speech.transcribe(recording())
        self.assertEqual(result['text'], 'supervisor, show the LAP agent.')
        self.assertEqual(result['duration_ms'], 1000)
        self.assertFalse(result['cached'])
        self.assertTrue(self.speech.transcribe(recording())['cached'])
        worker.transcribe.assert_called_once()
        self.assertFalse(self.speech.transcribe(recording(seconds=1.2))['cached'])
        self.assertFalse(self.speech.transcribe(recording(level=.2))['cached'])
        self.assertEqual(worker.transcribe.call_count, 3)
        self.assertEqual(list(self.speech.model.parent.iterdir()), [self.speech.model])

    def test_silence_and_invalid_audio_never_start_model(self):
        with patch.object(self.speech, '_get_worker') as run:
            self.assertEqual(self.speech.transcribe(recording(level=0))['text'], '')
            for audio in [b'not audio' * 10, recording(rate=48000), recording()[:-20], b'x' * (MAX_BYTES + 1), recording(seconds=30.1)]:
                with self.assertRaises(SpeechError):
                    self.speech.transcribe(audio)
            run.assert_not_called()

    def test_final_reuses_running_caption_audio_after_superseding_its_result(self):
        entered, finish = threading.Event(), threading.Event()
        worker = self.speech.worker = Mock()
        def transcribe(pcm):
            entered.set()
            self.assertTrue(finish.wait(2))
            return 'Show the LAP agent.'
        worker.transcribe.side_effect = transcribe
        work = dict(client='phone-voice-client', sequence=1, turn=1, mode='interim')
        audio = recording()
        with ThreadPoolExecutor(max_workers=2) as pool:
            partial = pool.submit(self.speech.transcribe, audio, work)
            self.assertTrue(entered.wait(1))
            final = pool.submit(self.speech.transcribe, audio, {**work, 'sequence':2, 'mode':'final'})
            try:
                with self.speech.lock.condition:
                    self.assertTrue(self.speech.lock.condition.wait_for(lambda: len(self.speech.lock.pending) == 2, 1))
            finally:
                finish.set()
            with self.assertRaises(SpeechError) as caught:
                partial.result(1)
            self.assertEqual(caught.exception.status, 409)
            result = final.result(1)
            self.assertEqual(result['text'], 'Show the LAP agent.')
            self.assertTrue(result['cached'])
            worker.transcribe.assert_called_once()

    def test_busy_timeout_and_failed_process_are_recoverable(self):
        self.speech.lock.acquire()
        with self.assertRaises(SpeechError) as caught:
            self.speech.transcribe(recording())
        self.assertEqual(caught.exception.status, 429)
        self.speech.lock.release()
        self.speech.worker = Mock()
        for failure, status in [(TimeoutError(), 504), (OSError('missing executable'), 503)]:
            self.speech.worker.transcribe.side_effect = failure
            with self.assertRaises(SpeechError) as error:
                self.speech.transcribe(recording())
            self.assertEqual(error.exception.status, status)
            self.assertTrue(self.speech.lock.acquire(blocking=False))
            self.speech.lock.release()

    def test_voice_upload_keeps_host_origin_client_and_token_checks(self):
        transcribe = Mock(return_value={'text': 'supervisor, next'})
        speak = Mock(return_value=recording())
        warmup = Mock(return_value={'ok': True})
        cancel = Mock(return_value={'ok': True})
        hub = SimpleNamespace(csrf='test-token', client_allowed=lambda address: True,
                              speech=SimpleNamespace(transcribe=transcribe, speak=speak, warmup=warmup, cancel=cancel))
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.hub = hub
        server.allowed_hosts = {'127.0.0.1'}
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        def request(headers=None, audio=None, path='/api/voice/transcribe'):
            connection = HTTPConnection(*server.server_address, timeout=3)
            try:
                connection.request('POST', path, body=audio or recording(),
                                   headers={'Content-Type': 'audio/wav', 'X-Hub-Token': 'test-token', **(headers or {})})
            except BrokenPipeError:
                pass  # A size rejection may close the connection before upload completes.
            result = connection.getresponse()
            status = result.status
            result.read()
            connection.close()
            return status
        self.assertEqual(request({'X-Hub-Token': 'wrong'}), 403)
        self.assertEqual(request({'Origin': 'https://other.invalid'}), 403)
        self.assertEqual(request({'Host': 'other.invalid'}), 403)
        hub.client_allowed = lambda address: False
        self.assertEqual(request(), 403)
        hub.client_allowed = lambda address: True
        self.assertEqual(request({'Content-Type': 'application/json'}), 415)
        self.assertEqual(request(audio=b'x' * (MAX_BYTES + 1)), 413)
        transcribe.assert_not_called()
        self.assertEqual(request(), 200)
        transcribe.assert_called_once()
        for headers in [{'X-Hub-Token': 'wrong'}, {'Origin': 'https://other.invalid'}, {'Host': 'other.invalid'}]:
            self.assertEqual(request(headers, b'{"text":"hello"}', '/api/voice/speak'), 403)
        hub.client_allowed = lambda address: False
        self.assertEqual(request(audio=b'{"text":"hello"}', path='/api/voice/speak'), 403)
        hub.client_allowed = lambda address: True
        speak.assert_not_called()
        self.assertEqual(request(audio=b'{"text":"hello"}', path='/api/voice/speak'), 200)
        speak.assert_called_once_with('hello')
        speak.side_effect = SpeechError('Voice busy', 429)
        self.assertEqual(request(audio=b'{"text":"hello"}', path='/api/voice/speak'), 429)
        for headers in [{'X-Hub-Token': 'wrong'}, {'Origin': 'https://other.invalid'}, {'Host': 'other.invalid'}]:
            self.assertEqual(request(headers, b'{}', '/api/voice/warmup'), 403)
        hub.client_allowed = lambda address: False
        self.assertEqual(request(audio=b'{}', path='/api/voice/warmup'), 403)
        warmup.assert_not_called()
        hub.client_allowed = lambda address: True
        self.assertEqual(request(audio=b'{}', path='/api/voice/warmup'), 200)
        warmup.assert_called_once_with()
        work = dict(client='phone-voice-client', sequence=2, turn=1, mode='final')
        self.assertEqual(request({'X-Hub-Speech-Work':json.dumps(work)}), 200)
        self.assertEqual(transcribe.call_args.kwargs, {'work':work})
        self.assertEqual(request({'X-Hub-Speech-Work':'{invalid'}), 400)
        cancellation = {'kind':'asr', 'client':work['client'], 'sequence':2}
        for headers in [{'X-Hub-Token':'wrong'}, {'Origin':'https://other.invalid'}, {'Host':'other.invalid'}]:
            self.assertEqual(request(headers, json.dumps(cancellation), '/api/voice/cancel'), 403)
        hub.client_allowed = lambda address: False
        self.assertEqual(request(audio=json.dumps(cancellation), path='/api/voice/cancel'), 403)
        hub.client_allowed = lambda address: True
        cancel.assert_not_called()
        self.assertEqual(request(audio=json.dumps(cancellation), path='/api/voice/cancel'), 200)
        cancel.assert_called_once_with(cancellation)

    @unittest.skipUnless(importlib.util.find_spec('numpy'), 'PCM conversion needs the optional local voice dependencies')
    def test_local_playback_reuses_cpu_model_and_returns_wav_without_audio_files(self):
        speech = self.speech
        speech.voice_available = True
        speech.voice_model = speech.model
        speech.voice_voices = speech.model.with_suffix('.voices')
        speech.voice_voices.touch()
        voice = SimpleNamespace(create=Mock(return_value=([0., .25, -.25] * 800, 24000)))
        constructor, inference = Mock(return_value=voice), Mock(return_value='cpu-session')
        options = SimpleNamespace()
        modules = {'kokoro_onnx': SimpleNamespace(Kokoro=SimpleNamespace(from_session=constructor)),
                   'onnxruntime': SimpleNamespace(SessionOptions=lambda: options, InferenceSession=inference)}
        with patch.dict(sys.modules, modules):
            for text in ['The checkpoint is ready.', 'Which agent do you mean?', 'The checkpoint is ready.']:
                audio = speech.speak(text)
                with wave.open(io.BytesIO(audio)) as wav:
                    self.assertEqual((wav.getnchannels(), wav.getframerate(), wav.getnframes()), (1, 24000, 2400))
                    self.assertEqual(wav.readframes(3), b'\x00\x00\x00\x20\x00\xe0')
        constructor.assert_called_once()
        inference.assert_called_once()
        self.assertEqual(inference.call_args.kwargs['providers'], ['CPUExecutionProvider'])
        self.assertLessEqual(options.intra_op_num_threads, 4)
        self.assertEqual(voice.create.call_count, 2, 'repeated replies reuse bounded memory audio')
        self.assertEqual(voice.create.call_args.kwargs['voice'], 'af_heart')
        self.assertEqual(sorted(p.name for p in speech.model.parent.iterdir()), ['model.bin', 'model.voices'])

    def test_remote_voice_speaks_first_and_kokoro_takes_what_it_cannot(self):
        speech = self.speech
        speech.remote = 'http://127.0.0.1:8799'
        speech.voice_available = True
        speech.voice_model = speech.voice_voices = speech.model
        replies = {'Ready.': b'RIFF remote audio', 'Late.': None}
        asked = []
        def remote(path, body, timeout):
            text = json.loads(body)['text']
            asked.append((path, text, timeout))
            return replies[text]
        speech._remote = remote
        kokoro = Mock(return_value=b'RIFF kokoro audio')
        with patch.object(speech, '_get_voice', side_effect=AssertionError('the remote voice answered')):
            self.assertEqual(speech.speak('Ready.'), b'RIFF remote audio')
        self.assertEqual(asked, [('/speak', 'Ready.', 5)])
        self.assertEqual(speech.speak('Ready.'), b'RIFF remote audio', 'kept like any clip')
        with patch.object(speech, '_get_voice', side_effect=OSError('no kokoro here')):
            with self.assertRaises(SpeechError):
                speech.speak('Late.')
        self.assertEqual(asked[-1][:2], ('/speak', 'Late.'), 'asked first, then Kokoro was tried')

    def test_playback_rejects_unbounded_input_and_busy_requests(self):
        for text in [None, '', ' ', 'x' * 401, 'bad\x00text']:
            with self.assertRaises(SpeechError) as result:
                self.speech.speak(text)
            self.assertEqual(result.exception.status, 400)
        self.speech.voice_available = True
        self.speech.voice_model = self.speech.voice_voices = self.speech.model
        self.speech.voice_lock.acquire()
        self.speech.audio.put('Ready.', b'cached audio')
        self.assertEqual(self.speech.speak('  Ready.  '), b'cached audio', 'a ready clip never waits behind model inference')
        with self.assertRaises(SpeechError) as result:
            self.speech.speak('Hello')
        self.assertEqual(result.exception.status, 429)
        self.speech.voice_lock.release()

    def test_warmup_is_nonblocking_and_coalesces_concurrent_calls(self):
        started, release = threading.Event(), threading.Event()
        def start():
            started.set()
            release.wait(2)
        self.speech.worker = Mock(start=Mock(side_effect=start))
        self.speech.warmup()
        self.assertTrue(started.wait(1))
        for _ in range(10):
            self.speech.warmup()
        self.speech.worker.start.assert_called_once()
        release.set()
        self.assertTrue(self.speech.lock.acquire(timeout=2))
        self.speech.lock.release()
        self.speech.close()
        self.speech.warmup()
        self.speech.worker.start.assert_called_once()

    def test_memory_cache_expires_and_bounds_both_entries_and_bytes(self):
        cache = AudioCache(limit=5, entries=2, seconds=10)
        with patch('speech.time.monotonic', return_value=100):
            cache.put('a', b'12'); cache.put('b', b'12')
            self.assertEqual(cache.get('a'), b'12')
            cache.put('c', b'123')
            self.assertIsNone(cache.get('b'))
            self.assertEqual(len(cache.items), 2)
            cache.put('too large', b'123456')
            self.assertEqual(len(cache.items), 0)
            cache.put('a', b'12')
        with patch('speech.time.monotonic', return_value=111):
            self.assertIsNone(cache.get('a'))


class WorkerTests(unittest.TestCase):
    def worker(self, program=None):
        worker = WhisperWorker('/unused', '/unused', 2)
        worker.command = [sys.executable, '-u', '-c', program or '''
import sys, struct
sys.stdout.buffer.write(b'HVW1'); sys.stdout.buffer.flush()
while header := sys.stdin.buffer.read(4):
    count, = struct.unpack('<I', header)
    pcm = sys.stdin.buffer.read(count * 2)
    result = str(len(pcm)).encode()
    sys.stdout.buffer.write(struct.pack('<I', len(result)) + result)
    sys.stdout.buffer.flush()
''']
        self.addCleanup(worker.close)
        return worker

    def test_resident_process_reuse_crash_recovery_and_shutdown(self):
        worker = self.worker()
        self.assertEqual(worker.transcribe(b'\x01\x00' * 16000), '32000')
        process = worker.process
        self.assertEqual(worker.transcribe(b'\x01\x00' * 32000), '64000')
        self.assertIs(worker.process, process)
        process.kill(); process.wait()
        self.assertEqual(worker.transcribe(b'\x01\x00' * 8000), '16000')
        self.assertIsNot(worker.process, process)
        replacement = worker.process
        worker.close()
        self.assertIsNotNone(replacement.poll())
        with self.assertRaises(OSError):
            worker.start()

    def test_timeout_reaps_worker_and_next_request_can_retry(self):
        worker = self.worker()
        process = worker.start()
        with patch('speech.select.select', return_value=([], [], [])), self.assertRaises(TimeoutError):
            worker.transcribe(b'\x00\x01' * 100)
        self.assertIsNotNone(process.poll())
        self.assertIsNone(worker.process)
        self.assertEqual(worker.transcribe(b'\x00\x01' * 100), '200')

    def test_bad_handshake_and_unbounded_reply_are_rejected_and_reaped(self):
        for program in ["import sys; sys.stdout.buffer.write(b'bad!')",
                        "import sys; sys.stdout.buffer.write(b'HVW1'+b'\\xff'*4); sys.stdout.buffer.flush(); sys.stdin.buffer.read(4)"]:
            worker = self.worker(program)
            with self.assertRaises((OSError, ValueError)):
                worker.transcribe(b'\x01\x00' * 100)
            self.assertIsNone(worker.process)


if __name__ == '__main__':
    unittest.main()
