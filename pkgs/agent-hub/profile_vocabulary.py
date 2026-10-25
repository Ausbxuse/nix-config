"""Compare a small Whisper vocabulary and TTS spellings using synthetic audio.

Uses the packaged CPU models. No microphone, agent, account, or cloud request.
This is a reproducible regression screen, not evidence about a user's accent or
subjective pronunciation quality. Detailed results contain only these fixtures.
"""
import argparse
import json
from pathlib import Path
import re
import statistics
import subprocess
import tempfile
import time

import numpy as np

from speech import LocalSpeech, WhisperWorker


VOCABULARY = 'NeMo, quantile, mean_std, LAP, gpu-004, razy, tmux, Codex.'
FIXTURES = [
    ('nemo', 'Supervisor, which agent is preparing the NeMo quantile checkpoints?'),
    ('lap', 'Show me the LAP agent on gpu-004.'),
    ('statistics', 'Compare the mean standard deviation baseline with quantile.'),
    ('razy', 'Ask the agent on razy to pause after this evaluation.'),
    ('tmux', 'Rename the tmux window to checkpoint comparison.'),
    ('codex', 'Tell Codex to compare the checkpoints using seeds three and seven.'),
    ('negative', "Don't queue anything. Tell me the current result."),
    ('numbers', 'Use zero point zero five, not zero point five.'),
    ('ordinary', 'Save this for later, and show me the next reply.'),
    ('end', 'Stop the microphone and end this call.'),
]
SPELLINGS = {'NeMo': 'Nemo', 'LAP': 'lap', 'gpu-004': 'G P U zero zero four', 'tmux': 'tee mux'}


def words(text):
    text = text.lower().replace('\u2019', "'")
    text = re.sub(r'\bgpu[.\s-]*0[.\s-]*0[.\s-]*4\b', 'gpu zero zero four', text)
    text = re.sub(r'\bt[\s-]+mux\b', 'tmux', text)
    for old, new in [('g p u', 'gpu'), ('gpu-004', 'gpu zero zero four'), ('gpu 004', 'gpu zero zero four'),
                     ('0.05', 'zero point zero five'), ('0.5', 'zero point five'), ('3', 'three'), ('7', 'seven')]:
        text = text.replace(old, new)
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text)


def distance(left, right):
    row = list(range(len(right) + 1))
    for i, a in enumerate(left):
        next_row = [i + 1]
        for j, b in enumerate(right):
            next_row.append(min(row[j + 1] + 1, next_row[j] + 1, row[j] + (a != b)))
        row = next_row
    return row[-1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--whisper-root', type=Path, required=True, help='Packaged whisper.cpp with include/ and lib/')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    speech = LocalSpeech()
    if not speech.model.is_file() or not speech.status()['playback']['available']:
        parser.error('Use the hub Python environment and set its Whisper/Kokoro model variables.')
    results = []
    workers = {}
    try:
        with tempfile.TemporaryDirectory(prefix='hub-vocabulary-') as directory:
            root = Path(directory); source = Path(__file__).with_name('whisper-worker.cpp').read_text()
            for old, new in [('if (argc != 3) return 1;', 'if (argc != 3 && argc != 4) return 1;'),
                             ('params.n_max_text_ctx = 0;', 'params.n_max_text_ctx = argc == 4 ? 64 : 0;\n    params.initial_prompt = argc == 4 ? argv[3] : nullptr;')]:
                if source.count(old) != 1: raise RuntimeError('Worker changed; review the benchmark adaptation.')
                source = source.replace(old, new)
            (root / 'worker.cpp').write_text(source)
            executable = root / 'worker'
            subprocess.run(['c++', '-std=c++17', '-O2', str(root / 'worker.cpp'),
                '-I' + str(args.whisper_root / 'include'), '-L' + str(args.whisper_root / 'lib'),
                '-Wl,-rpath,' + str(args.whisper_root / 'lib'), '-lwhisper', '-lggml', '-lggml-base', '-o', str(executable)], check=True)
            for mode in ('baseline', 'vocabulary'):
                worker = workers[mode] = WhisperWorker(str(executable), speech.model, 8)
                if mode == 'vocabulary': worker.command.append(VOCABULARY)
                worker.start()
            voice = speech._get_voice()
            for speaker in ('af_heart', 'am_michael'):
                for identity, expected in FIXTURES:
                    variants = {'original': expected}
                    spelled = expected
                    for old, new in SPELLINGS.items(): spelled = re.sub(r'\b' + re.escape(old) + r'\b', new, spelled)
                    if spelled != expected: variants['pronunciation'] = spelled
                    for variant, text in variants.items():
                        started = time.perf_counter()
                        audio, rate = voice.create(text, voice=speaker, speed=1.02, lang='en-us', sentence_pause=.2, clause_pause=.08)
                        tts_ms = round((time.perf_counter() - started) * 1000, 1)
                        # Browser sample rate and 200 ms of trailing silence.
                        target = np.arange(int(len(audio) * 16000 / rate)) * rate / 16000
                        samples = np.interp(target, np.arange(len(audio)), audio)
                        samples = np.concatenate((samples, np.zeros(3200)))
                        pcm = np.clip(np.rint(samples * 32767), -32768, 32767).astype('<i2').tobytes()
                        for mode, worker in workers.items():
                            started = time.perf_counter(); recognized = worker.transcribe(pcm).strip()
                            results.append(dict(id=identity, speaker=speaker, variant=variant, mode=mode,
                                expected=expected, recognized=recognized, errors=distance(words(expected), words(recognized)),
                                words=len(words(expected)), asr_ms=round((time.perf_counter() - started) * 1000, 1), tts_ms=tts_ms))
                print(json.dumps({'completed_voice': speaker, 'cases': len(results)}), flush=True)
            # Decoder-only controls intentionally bypass the browser's VAD and
            # host RMS check. They probe vocabulary leakage, not normal routing.
            rng = np.random.default_rng(23)
            for identity, samples in [('silence', np.zeros(32000)), ('noise', rng.normal(0, .015, 32000)),
                                       ('tone', .025 * np.sin(np.arange(32000) * 2 * np.pi * 220 / 16000))]:
                pcm = np.clip(np.rint(samples * 32767), -32768, 32767).astype('<i2').tobytes()
                for mode, worker in workers.items():
                    started = time.perf_counter(); recognized = worker.transcribe(pcm).strip()
                    results.append(dict(id=identity, speaker='control', variant='control', mode=mode,
                        expected='', recognized=recognized, errors=len(words(recognized)), words=0,
                        asr_ms=round((time.perf_counter() - started) * 1000, 1)))
            summary = []
            for variant in ('original', 'pronunciation', 'control'):
                for mode in workers:
                    rows = [r for r in results if r['variant'] == variant and r['mode'] == mode]
                    errors = sum(r['errors'] for r in rows); count = sum(r['words'] for r in rows)
                    summary.append(dict(variant=variant, mode=mode, cases=len(rows), errors=errors, words=count,
                        wer=round(errors/count, 3) if count else None, median_asr_ms=round(statistics.median(r['asr_ms'] for r in rows), 1)))
            args.output.write_text(json.dumps({'vocabulary': VOCABULARY, 'spellings': SPELLINGS, 'summary': summary, 'results': results}, indent=2) + '\n')
            print(json.dumps({'summary': summary}), flush=True)
    finally:
        for worker in workers.values(): worker.close()
        speech.close()


if __name__ == '__main__': main()
