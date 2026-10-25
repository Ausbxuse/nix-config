"""Speech prefixes and an actual Codex runtime with a localhost-only provider."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest

from bridge import BridgeError
from spoken_reply import ReplyStream, answer, command, complete_sentences, generate, reply_prefix


class SpokenReplyTests(unittest.TestCase):
    def test_only_complete_answer_sentences_are_released(self):
        updates = []; stream = ReplyStream(updates.append)
        stream.start({'type': 'reasoning', 'id': 'reasoning'})
        stream.update('reasoning', '{"reply":"Private reasoning. Another thought."}')
        stream.start({'type': 'agentMessage', 'id': 'progress', 'phase': 'commentary'})
        stream.update('progress', '{"reply":"Progress text. Still working."}')
        stream.start({'type': 'agentMessage', 'id': 'answer', 'phase': 'final_answer'})
        stream.update('other-thread-item', '{"reply":"Not this answer."}')
        stream.update('answer', '{"reply":"The checkpoint comparison is ready.')
        self.assertEqual(updates, [])
        stream.update('answer', ' It reports a score of 0.8')
        self.assertEqual(updates, ['The checkpoint comparison is ready.'])
        stream.update('answer', '5, which needs review."}')
        final = 'The checkpoint comparison is ready. It reports a score of 0.85, which needs review.'
        self.assertEqual(updates[-1], final)
        stream.complete({'type': 'agentMessage', 'id': 'answer', 'text': json.dumps({'reply': final})})
        self.assertTrue(stream.finished)

    def test_escapes_abbreviations_markup_and_unknown_phase_wait(self):
        self.assertEqual(reply_prefix('prefix {"reply":"Do not recover."}'), ('', False))
        self.assertEqual(reply_prefix('{"reply":"An incomplete \\u20'), ('', False))
        self.assertEqual(reply_prefix('{"reply":"A \\u201cquote\\u201d. More'), ('A “quote”. More', False))
        self.assertEqual(reply_prefix('{"reply":"Okay.","actions":[]'), ('', False))
        text = 'Dr. Zhao is checking version 2.3. It needs review.'
        self.assertEqual(complete_sentences(text), 'Dr. Zhao is checking version 2.3.')
        self.assertEqual(complete_sentences('It uses e.g. three runs. More'), 'It uses e.g. three runs.')
        for value in ('See `some code`. More', 'Read [the file](path). More', 'See https://a.b. More', 'One.\nTwo.'):
            self.assertEqual(complete_sentences(value), '')
        updates = []; stream = ReplyStream(updates.append)
        stream.start({'type': 'agentMessage', 'id': 'unknown'})
        stream.update('unknown', '{"reply":"Legacy phase. More text."}')
        stream.complete({'type': 'agentMessage', 'id': 'unknown', 'text': stream.text})
        self.assertEqual(updates, []); self.assertTrue(stream.finished)

    def test_changed_duplicate_extra_or_invalid_final_cannot_be_accepted(self):
        for value in ('{"reply":"first","reply":"second"}', '{"reply":"a","actions":[]}', '{"reply":null}', '[]'):
            with self.assertRaises(ValueError): answer(value)
        stream = ReplyStream(lambda value: None)
        stream.start({'type': 'agentMessage', 'id': 'answer', 'phase': 'final_answer'})
        stream.update('answer', '{"reply":"The first answer. Next')
        with self.assertRaises(BridgeError):
            stream.complete({'type': 'agentMessage', 'id': 'answer', 'text': '{"reply":"A changed answer."}'})
        with self.assertRaises(BridgeError):
            stream.start({'type': 'agentMessage', 'id': 'second', 'phase': 'final_answer'})

    def test_configured_mcp_servers_are_disabled_without_changing_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.toml'
            original = '[mcp_servers."fixture.with.dots"]\ncommand="false"\n'
            config.write_text(original)
            args = command('codex', {'CODEX_HOME': directory})
            self.assertIn('mcp_servers={"fixture.with.dots" = { command = "false", enabled = false }}', args)
            self.assertIn('features.hooks=false', args)
            self.assertEqual(config.read_text(), original)


def runtime_fixture(binary, fail=False, timeout=10):
    updates, tracked, requests = [], set(), []
    started = time.monotonic()
    class Provider(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            requests.append(request)
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
            def emit(value):
                self.wfile.write(('event: ' + value['type'] + '\ndata: ' + json.dumps(value) + '\n\n').encode()); self.wfile.flush()
            try:
                item = {'id': 'message_fixture', 'type': 'message', 'role': 'assistant', 'phase': 'final_answer', 'content': []}
                emit({'type': 'response.created', 'response': {'id': 'fixture', 'status': 'in_progress', 'output': []}})
                emit({'type': 'response.output_item.added', 'output_index': 0, 'item': item})
                emit({'type': 'response.content_part.added', 'item_id': item['id'], 'output_index': 0, 'content_index': 0,
                      'part': {'type': 'output_text', 'text': '', 'annotations': []}})
                parts = ['{"reply":"The checkpoint comparison is ready. The', ' second run is still working."}']
                for part in parts:
                    emit({'type': 'response.output_text.delta', 'item_id': item['id'], 'output_index': 0, 'content_index': 0, 'delta': part})
                    time.sleep(.4)
                item['content'] = [{'type': 'output_text', 'text': ''.join(parts), 'annotations': []}]
                emit({'type': 'response.output_item.done', 'output_index': 0, 'item': item})
                if fail:
                    emit({'type': 'response.failed', 'response': {'id': 'fixture', 'status': 'failed', 'error': {'code': 'server_error', 'message': 'Fixture failure'}}})
                else:
                    emit({'type': 'response.completed', 'response': {'id': 'fixture', 'status': 'completed', 'output': [item],
                          'usage': {'input_tokens': 8, 'output_tokens': 16, 'total_tokens': 24}}})
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *args): pass
    provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='hub-spoken-runtime-') as directory:
            root = Path(directory)
            (root / 'config.toml').write_text('model_provider="fixture"\nmodel="gpt-6.1-sol"\n'
                '[model_providers.fixture]\nname="fixture"\nwire_api="responses"\nrequires_openai_auth=false\n'
                'base_url="http://127.0.0.1:' + str(provider.server_port) + '/v1"\n'
                'stream_max_retries=0\nrequest_max_retries=0\n[analytics]\nenabled=false\n'
                '[mcp_servers.inert]\ncommand="false"\n')
            env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'SSL_CERT_FILE')}
            env['CODEX_HOME'] = directory
            def track(process, active):
                if active: tracked.add(process)
                else: tracked.remove(process)
            try:
                result, metrics = generate(binary, 'Reply using only the fixture.', 'Answer this read-only fixture.',
                    'gpt-6.1-sol', directory, env,
                    lambda text: updates.append((time.monotonic() - started, text)), track, threading.Event(), timeout)
            except BridgeError:
                if not fail and timeout >= 10: raise
                result, metrics = None, {}
            finally:
                assert not tracked, 'Owned process was not released'
            assert requests and len(requests) == 1, 'The model request was retried'
            assert requests[0].get('tools') in ([], None), 'Read-only speech exposed tools'
            # Newer runtimes place tool schemas in additional_tools input items,
            # not the legacy top-level tools list. Inspect both representations.
            additional = [tool for item in requests[0].get('input', []) if item.get('type') == 'additional_tools'
                          for tool in item.get('tools', [])]
            assert not any(tool.get('name') in ('collaboration', 'clock') for tool in additional), 'Unneeded agent/sleep tools were advertised'
            advertised = {tool.get('name') for group in additional for tool in group.get('tools', [])}
            advertised.update(re.findall(r'### `([^`]+)`', json.dumps(additional)))
            for name in ('spawn_agent', 'exec_command', 'view_image', 'create_goal'):
                assert name not in advertised, 'Unneeded tool advertised: ' + name
            messages = json.dumps([item for item in requests[0].get('input', []) if item.get('type') == 'message'])
            assert '## Skills' not in messages, 'Skill catalog leaked into a read-only answer'
            assert requests[0]['text']['format']['schema']['properties'] == {'reply': {'type': 'string'}}
            assert not list(root.rglob('rollout-*')), 'Ephemeral reply persisted a session log'
            return {'result': result, 'metrics': metrics, 'first_ms': round(updates[0][0] * 1000, 1) if updates else None,
                    'final_ms': round((time.monotonic() - started) * 1000, 1), 'updates': [text for _, text in updates]}
    finally:
        provider.shutdown(); provider.server_close()


@unittest.skipUnless(os.environ.get('AGENT_HUB_TEST_CODEX'), 'Set AGENT_HUB_TEST_CODEX for isolated speech integration')
class SpokenRuntimeTests(unittest.TestCase):
    def test_real_runtime_streams_only_readonly_answer_before_completion(self):
        result = runtime_fixture(os.environ['AGENT_HUB_TEST_CODEX'])
        self.assertGreater(result['final_ms'] - result['first_ms'], 600)
        self.assertEqual(result['updates'][0], 'The checkpoint comparison is ready.')
        self.assertEqual(result['result']['reply'], 'The checkpoint comparison is ready. The second run is still working.')
        self.assertEqual(result['result']['actions'], [])
        self.assertLessEqual(result['metrics']['runtime_ready_ms'], result['metrics']['turn_ready_ms'])
        self.assertLessEqual(result['metrics']['turn_ready_ms'], result['metrics']['first_delta_ms'])
        self.assertLessEqual(result['metrics']['first_delta_ms'], result['metrics']['model_ms'])

    def test_failed_turn_does_not_become_a_success_or_repeat_the_request(self):
        result = runtime_fixture(os.environ['AGENT_HUB_TEST_CODEX'], fail=True)
        self.assertIsNone(result['result'])
        self.assertTrue(result['updates'])


if __name__ == '__main__': unittest.main()
