"""Profile local voice with a supplied, non-private 16 kHz mono PCM WAV.

Run with the same model paths and Python environment as the hub. This creates
its own engines and does not send instructions to agents. It reports timings,
not transcripts, and disables caches for the main before/after measurements.
"""
import argparse
import io
import json
import statistics
import time
import wave
from pathlib import Path

from speech import LocalSpeech


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audio', type=Path)
    parser.add_argument('--trials', type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.trials <= 20:
        parser.error('--trials must be between 1 and 20')
    audio = args.audio.read_bytes()
    text = "I've queued that for the LAP agent. She'll pick it up when the current turn finishes."
    speech = LocalSpeech()
    if not speech.status()['available'] or not speech.status()['playback']['available']:
        parser.error('Set the hub model paths and AGENT_HUB_WHISPER_WORKER; use its Kokoro Python environment.')
    def elapsed(function):
        start = time.perf_counter()
        result = function()
        return round((time.perf_counter() - start) * 1000), result
    try:
        # Warmup runs in short-lived HTTP threads in production. Verify that
        # their exit does not kill the persistent native worker.
        start = time.perf_counter()
        speech.warmup()
        for lock in [speech.lock, speech.voice_lock]:
            if not lock.acquire(timeout=15):
                raise RuntimeError('Voice warmup did not finish')
            lock.release()
        print(json.dumps({'warmup_ms': round((time.perf_counter() - start) * 1000)}), flush=True)
        process = speech.worker.process
        if process is None or process.poll() is not None:
            raise RuntimeError('Warmup worker did not survive its spawning thread')
        results = []
        for _ in range(args.trials):
            speech.transcripts.items.clear(); speech.audio.items.clear()
            asr, transcript = elapsed(lambda: speech.transcribe(audio))
            tts, wav = elapsed(lambda: speech.speak(text))
            if speech.worker.process is not process or process.poll() is not None:
                raise RuntimeError('Speech worker restarted between requests')
            with wave.open(io.BytesIO(wav)) as reader:
                seconds = reader.getnframes() / reader.getframerate()
            result = {'asr_ms': asr, 'tts_ms': tts, 'audio_seconds': round(seconds, 2),
                      'transcribed': bool(transcript['text'])}
            results.append(result); print(json.dumps(result), flush=True)
        cached_asr, _ = elapsed(lambda: speech.transcribe(audio))
        cached_tts, _ = elapsed(lambda: speech.speak(text))
        print(json.dumps({'median_asr_ms': statistics.median(row['asr_ms'] for row in results),
                          'median_tts_ms': statistics.median(row['tts_ms'] for row in results),
                          'cached_asr_ms': cached_asr, 'cached_tts_ms': cached_tts,
                          'resident_worker': True}), flush=True)
    finally:
        speech.close()


if __name__ == '__main__':
    main()
