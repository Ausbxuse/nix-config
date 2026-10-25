"""Optional integration with a real Codex binary and a local, inert provider.

No installed daemon, user agent, credentials or remote model is used. Set
AGENT_HUB_TEST_CODEX to a Codex binary to exercise its actual queue semantics.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import json
import os
from pathlib import Path
import selectors
import subprocess
import tempfile
import threading
import time
import unittest


def exercise(binary):
    held, release = threading.Event(), threading.Event()
    requests = []

    class LocalProvider(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0)))))
            held.set()
            release.wait(20)
            try:
                self.send_error(503, 'Fixture complete')
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    provider = ThreadingHTTPServer(('127.0.0.1', 0), LocalProvider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='hub-native-runtime-') as directory:
            root = Path(directory)
            image = root / 'fixture.png'
            image.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAACAAAAAUCAYAAADskT9PAAAALUlEQVR4nGMUM4+qYBhAwDSQlo86YNQBow4YdcCoA0YdMOqAUQeMOoCBgYEBAB1SAUdW4ElUAAAAAElFTkSuQmCC'))
            (root / 'config.toml').write_text(
                'model_provider = "fixture"\nmodel = "fixture"\n'
                'sandbox_mode = "read-only"\napproval_policy = "never"\n'
                '[model_providers.fixture]\nname = "fixture"\n'
                f'base_url = "http://127.0.0.1:{provider.server_port}/v1"\n'
                'wire_api = "responses"\nrequires_openai_auth = false\n'
                '[analytics]\nenabled = false\n'
            )
            # The installed daemon and agents are untouched. Even accidental
            # generation can reach only the held local fixture provider.
            env = {key: value for key, value in os.environ.items()
                   if key in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'SSL_CERT_FILE')}
            env['CODEX_HOME'] = str(root)
            with (root / 'stderr.log').open('w+') as log:
                process = subprocess.Popen(
                    [binary, 'app-server', '--stdio'], cwd=root, env=env,
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, bufsize=0,
                )
                selector = selectors.DefaultSelector()
                try:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    counter, buffer = 0, bytearray()

                    def rpc(method, params, allow_error=False):
                        nonlocal counter
                        counter += 1
                        identity = counter
                        request = {'id': identity, 'method': method, 'params': params}
                        process.stdin.write((json.dumps(request) + '\n').encode())
                        process.stdin.flush()
                        deadline = time.monotonic() + 10
                        while time.monotonic() < deadline:
                            if b'\n' not in buffer:
                                if not selector.select(.1):
                                    continue
                                chunk = os.read(process.stdout.fileno(), 65536)
                                if not chunk:
                                    raise RuntimeError('Fixture runtime stopped')
                                buffer.extend(chunk)
                            if b'\n' not in buffer:
                                continue
                            line, _, tail = buffer.partition(b'\n')
                            buffer[:] = tail
                            value = json.loads(line)
                            if value.get('id') == identity and 'method' not in value:
                                if value.get('error') and not allow_error:
                                    raise RuntimeError(value['error'])
                                return value
                        raise RuntimeError('Fixture RPC timeout for ' + method)

                    rpc('initialize', {
                        'clientInfo': {'name': 'hub_fixture', 'version': '1'},
                        'capabilities': {'experimentalApi': True},
                    })
                    process.stdin.write(b'{"method":"initialized"}\n')
                    process.stdin.flush()
                    thread = rpc('thread/start', {
                        'cwd': directory, 'modelProvider': 'fixture', 'model': 'fixture', 'ephemeral': False,
                    })['result']['thread']['id']

                    # Explicit settings updates must not start a turn or reset
                    # the thread's execution/approval configuration.
                    before_settings = rpc('thread/read', {'threadId':thread})['result']['thread']
                    rpc('thread/settings/update', {'threadId':thread,'model':'gpt-6-luna','effort':'max'})
                    after_settings = rpc('thread/read', {'threadId':thread})['result']['thread']
                    assert after_settings['model'] == 'gpt-6-luna'
                    assert after_settings['reasoningEffort'] == 'max'
                    assert after_settings['status'] == before_settings['status']
                    assert not held.is_set(), 'Changing settings started inference'

                    def text(value):
                        return [{'type': 'text', 'text': value, 'text_elements': []}]

                    def queue():
                        return rpc('thread/queue/list', {'threadId': thread, 'limit': 32})['result']['data']

                    def status():
                        return rpc('thread/read', {'threadId': thread, 'includeTurns': False})['result']['thread']['status']

                    turn = rpc('turn/start', {
                        'threadId': thread, 'input': text('Held fixture request; local provider only') + [{'type':'localImage','path':str(image)}],
                    })['result']['turn']
                    assert held.wait(5), 'Fixture did not reach the local provider'
                    assert requests[0]['model'] == 'gpt-6-luna', 'The next turn ignored the selected model'
                    assert requests[0]['reasoning']['effort'] == 'max', 'The next turn ignored the selected effort'
                    images = [part for item in requests[0]['input'] for part in item.get('content', []) if isinstance(part, dict) and part.get('type') == 'input_image']
                    assert images and images[0]['image_url'].startswith('data:image/'), 'Image bytes did not reach the local provider'
                    rpc('thread/queue/add', {
                        'threadId': thread, 'input': text('Fixture input; no real model or agent work') + [{'type':'localImage','path':str(image)}],
                        'clientUserMessageId': 'fixture-user-1',
                    })
                    before = queue()
                    assert len(before) == 1
                    assert any(part['type']=='localImage' or part['type']=='image' and part.get('url', '').startswith('data:image/') for part in before[0]['input']), 'Queued image was lost'
                    queue_id = before[0]['id']
                    rpc('thread/queue/update', {
                        'threadId': thread, 'queuedSubmissionId': queue_id,
                        'input': text('Updated fixture input'),
                    })
                    assert queue()[0]['input'][0]['text'] == 'Updated fixture input'
                    rpc('thread/queue/delete', {'threadId': thread, 'queuedSubmissionId': queue_id})
                    assert queue() == []
                    missing = rpc('thread/queue/update', {
                        'threadId': thread, 'queuedSubmissionId': queue_id, 'input': text('Must not run'),
                    }, allow_error=True)
                    assert 'error' in missing
                    rpc('thread/queue/add', {
                        'threadId': thread, 'input': text('Second held fixture input'),
                        'clientUserMessageId': 'fixture-user-2',
                    })
                    queued = queue()[0]['id']
                    rpc('turn/interrupt', {'threadId': thread, 'turnId': turn['id']})
                    for _ in range(30):
                        if not queue():
                            break
                        if status()['type'] == 'idle':
                            rpc('thread/queue/start', {'threadId': thread, 'queuedSubmissionId': queued})
                        time.sleep(.05)
                    assert not queue()
                    executing = rpc('thread/queue/update', {
                        'threadId': thread, 'queuedSubmissionId': queued,
                        'input': text('Must not edit executing work'),
                    }, allow_error=True)
                    assert 'error' in executing
                    from transcripts import input_text_parts
                    rollout = Path(rpc('thread/read', {'threadId':thread})['result']['thread']['path'])
                    image_inputs = [r['payload']['content'] for r in map(json.loads, rollout.read_text().splitlines())
                                    if r.get('type') == 'response_item' and r.get('payload', {}).get('role') == 'user'
                                    and any('Held fixture request' in p.get('text', '') for p in r['payload'].get('content', []))]
                    assert len(image_inputs) == 1
                    assert input_text_parts(image_inputs[0]) == ['Held fixture request; local provider only']
                    turns = rpc('thread/read', {'threadId':thread, 'includeTurns':True})['result']['thread']['turns']
                    native_inputs = next(item['content'] for item in turns[0]['items'] if item['type'] == 'userMessage')
                    assert '\n'.join(part.get('text', '') for part in native_inputs if part.get('type') == 'text') == 'Held fixture request; local provider only'
                    return {
                        'isolated_runtime': True, 'image_input': True, 'image_queue': True, 'image_history': True, 'queue_add': True, 'queue_update': True,
                        'queue_delete': True, 'missing_id_rejected': True, 'executing_id_rejected': True,
                        'status': status(), 'held_local_provider': held.is_set(),
                    }
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    selector.close()
                    process.stdin.close()
                    process.stdout.close()
    finally:
        release.set()
        provider.shutdown()
        provider.server_close()


class NativeRuntimeTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('AGENT_HUB_TEST_CODEX'),
                         'Set AGENT_HUB_TEST_CODEX for the isolated runtime integration')
    def test_current_runtime_rejects_editing_removed_or_executing_input(self):
        result = exercise(os.environ['AGENT_HUB_TEST_CODEX'])
        for key in ('image_input', 'image_queue', 'image_history', 'queue_add', 'queue_update', 'queue_delete', 'missing_id_rejected',
                    'executing_id_rejected', 'held_local_provider'):
            self.assertTrue(result[key], key)
        self.assertEqual(result['status']['type'], 'active')


if __name__ == '__main__':
    unittest.main()
