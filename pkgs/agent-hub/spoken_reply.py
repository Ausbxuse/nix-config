"""Read-only supervisor answers, streamed through an owned Codex stdio process.

Only answer text is accepted. This process never attaches to a user's agent, and
does not expose tools or action/selection fields. Audio is still generated locally.
"""
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import time
import tomllib

from bridge import BridgeError


SCHEMA = {'type': 'object', 'properties': {'reply': {'type': 'string'}},
          'required': ['reply'], 'additionalProperties': False}
MAX_OUTPUT = 65536
MAX_SPOKEN = 2400
# Current Codex uses agents.enabled and features.view_image. The older
# features.multi_agent/tools.view_image settings alone leave schemas advertised.
# These overrides belong only to the hub's private answer process.
READ_ONLY_CONFIG = ('agents.enabled=false', 'features.goals=false',
                    'features.sleep_tool=false', 'features.view_image=false',
                    'skills.include_instructions=false',
                    'tools.experimental_request_user_input.enabled=false',
                    'include_collaboration_mode_instructions=false')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate reply field')
        result[key] = value
    return result


def answer(value):
    result = json.loads(value, object_pairs_hook=unique_object)
    if not isinstance(result, dict) or set(result) != {'reply'} or not isinstance(result['reply'], str):
        raise ValueError('Invalid spoken answer')
    return result['reply']


def reply_prefix(value):
    """Decode only the top-level reply string; never recover text from malformed JSON."""
    match = re.match(r'^\s*\{\s*"reply"\s*:\s*(")', value)
    if not match:
        return '', False
    fragment = value[match.start(1):]
    try:
        text, end = json.JSONDecoder().raw_decode(fragment)
        if not isinstance(text, str) or fragment[end:].strip() not in ('', '}'):
            return '', False
        return text, True
    except ValueError:
        try:
            # An incomplete escape waits for the next delta. No repair, guessed
            # quote, or replacement character can reach the speaker.
            return json.loads(fragment + '"'), False
        except ValueError:
            return '', False


def complete_sentences(text, closed=False):
    """A conservative prefix: punctuation plus the start of the next sentence.

    Keep abbreviations, decimal values and incomplete markup in the same clip.
    Rich/code answers wait for completion and use the normal speech formatter.
    """
    if any(char in text for char in '`[]<>\n') or re.search(r'https?://', text):
        return ''
    end = 0
    for match in re.finditer(r'[.!?]["\u201d\u2019)]*(?=\s+\S|$)', text):
        boundary = match.end()
        if boundary == len(text) and not closed:
            continue
        before = text[:match.start()]
        word = re.search(r'(\S+)$', before)
        word = word[1] if word else ''
        if text[match.start()] == '.' and (re.fullmatch(r'(?:[A-Za-z]\.)*[A-Za-z]|\d+', word)
                or word.lower() in ('dr', 'mr', 'mrs', 'ms', 'prof', 'vs', 'etc', 'fig', 'no', 'e.g', 'i.e')):
            continue
        if boundary > MAX_SPOKEN:
            break
        end = boundary
    return text[:end]


class ReplyStream:
    def __init__(self, publish):
        self.publish = publish
        self.text = ''
        self.spoken = ''
        self.item = None
        self.phase = None
        self.finished = False

    def start(self, item):
        if item.get('type') != 'agentMessage':
            return
        if item.get('phase') in ('commentary', 'partial_answer'):
            return
        if self.item is not None:
            raise BridgeError('Supervisor returned more than one answer. No commands were sent.')
        self.item, self.phase = item['id'], item.get('phase')

    def update(self, identity, delta):
        if identity != self.item:
            return
        if self.finished or not isinstance(delta, str) or len(self.text) + len(delta) > MAX_OUTPUT:
            raise BridgeError('Supervisor answer exceeded the response limit.')
        self.text += delta
        # Unknown phases remain compatible, but cannot be spoken early.
        if self.phase != 'final_answer':
            return
        text, closed = reply_prefix(self.text)
        if any(0xD800 <= ord(c) <= 0xDFFF for c in text):
            return
        prefix = complete_sentences(text, closed)
        if prefix and prefix != self.spoken:
            if not prefix.startswith(self.spoken):
                raise BridgeError('Supervisor changed its answer while speaking.')
            self.spoken = prefix
            self.publish(prefix)

    def complete(self, item):
        if item.get('id') != self.item or item.get('type') != 'agentMessage':
            return
        text = item.get('text', '')
        if not isinstance(text, str) or len(text) > MAX_OUTPUT:
            raise BridgeError('Invalid supervisor answer.')
        try:
            reply = answer(text)
        except ValueError as exc:
            raise BridgeError('Supervisor returned an invalid answer. No commands were sent.') from exc
        if not reply.startswith(self.spoken):
            raise BridgeError('Supervisor changed its answer while speaking.')
        self.text, self.finished = text, True


def command(executable, env):
    # App-server has no exec-style --ignore-user-config option. Disable each
    # configured MCP server explicitly; keep account/provider configuration.
    config = Path(env.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'config.toml'
    servers = {}
    if config.exists():
        with config.open('rb') as source:
            servers = tomllib.load(source).get('mcp_servers', {})
    settings = ['notify=[]', 'features.apps=false', 'features.multi_agent=false',
                'features.shell_tool=false', 'features.apply_patch_freeform=false',
                'features.code_mode=false', 'tools.view_image=false',
                'web_search="disabled"', 'features.hooks=false',
                'skills.max_context_tokens=1', 'project_doc_max_bytes=0']
    settings += READ_ONLY_CONFIG
    settings += ['mcp_servers={' + ', '.join(json.dumps(key) + ' = { command = "false", enabled = false }' for key in servers) + '}']
    return [executable, 'app-server', '--stdio'] + [part for setting in settings for part in ('-c', setting)]


def generate(executable, prompt, instructions, model, cwd, env, publish, track, stop, timeout=180):
    """One ephemeral, read-only request. Failure never retries the model request."""
    started = time.monotonic()
    process = subprocess.Popen(command(executable, env), stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               cwd=cwd, env=env, bufsize=0, start_new_session=True)
    track(process, True)
    selector = selectors.DefaultSelector()
    stream = ReplyStream(publish)
    buffer = bytearray()
    thread_id = turn_id = None
    metrics = {'prompt_characters': len(prompt), 'instruction_characters': len(instructions)}
    count = 0

    def send(value):
        process.stdin.write((json.dumps(value, ensure_ascii=False) + '\n').encode())
        process.stdin.flush()

    def receive():
        nonlocal count
        while not stop.is_set() and time.monotonic() - started < timeout:
            if b'\n' in buffer:
                line, _, tail = buffer.partition(b'\n'); buffer[:] = tail
                count += len(line)
                if count > 4 * 1024 * 1024:
                    raise BridgeError('Supervisor stream exceeded the response limit.')
                return json.loads(line)
            if selector.select(.2):
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    raise BridgeError('Supervisor connection ended before the reply completed.')
                buffer.extend(chunk)
                if len(buffer) > 1024 * 1024:
                    raise BridgeError('Supervisor stream exceeded the response limit.')
        raise BridgeError('Supervisor stopped before the reply completed. Your agents are still running.')

    def observe(value):
        params = value.get('params', {})
        if 'id' in value and 'method' in value:
            # There are no client tools or approvals in this read-only reader.
            send({'id': value['id'], 'error': {'code': -32601, 'message': 'Not available to the supervisor'}})
            raise BridgeError('Supervisor requested an unavailable tool. No commands were sent.')
        if not thread_id or params.get('threadId') != thread_id:
            return
        method = value.get('method')
        if method == 'thread/tokenUsage/updated':
            usage = params.get('tokenUsage', {}).get('total', {})
            metrics.update({target: usage[key] for key, target in (('inputTokens', 'input_tokens'), ('outputTokens', 'output_tokens')) if type(usage.get(key)) is int})
            return
        if not turn_id or params.get('turnId', params.get('turn', {}).get('id')) != turn_id:
            return
        if method == 'item/started':
            item = params.get('item', {})
            if item.get('type') not in ('userMessage', 'agentMessage', 'reasoning'):
                raise BridgeError('Supervisor attempted to use an unavailable tool. No commands were sent.')
            stream.start(item)
        elif method == 'item/agentMessage/delta':
            if params.get('itemId') == stream.item and stream.phase == 'final_answer':
                metrics.setdefault('first_delta_ms', round((time.monotonic() - started) * 1000, 1))
            stream.update(params.get('itemId'), params.get('delta'))
        elif method == 'item/completed':
            stream.complete(params.get('item', {}))
        elif method == 'turn/completed':
            turn = params.get('turn', {})
            if turn.get('status') != 'completed' or not stream.finished:
                raise BridgeError('Supervisor could not complete the reply. ' + str((turn.get('error') or {}).get('message', ''))[:500])
            return answer(stream.text)

    def rpc(identity, method, params):
        send({'id': identity, 'method': method, 'params': params})
        while True:
            value = receive()
            if value.get('id') == identity and 'method' not in value:
                if 'error' in value:
                    raise BridgeError('Supervisor connection failed: ' + str(value['error'].get('message', ''))[:500])
                return value['result']
            observe(value)

    try:
        selector.register(process.stdout, selectors.EVENT_READ)
        rpc(1, 'initialize', {'clientInfo': {'name': 'agent_hub_supervisor', 'version': '1'},
                              'capabilities': {'experimentalApi': True}})
        send({'method': 'initialized'})
        thread = rpc(2, 'thread/start', {'cwd': str(cwd), 'model': model, 'ephemeral': True,
                     'approvalPolicy': 'never', 'sandbox': 'read-only', 'baseInstructions': instructions,
                     'developerInstructions': 'This is a read-only spoken answer. Do not call tools or take actions. '
                     'Return exactly one JSON object with only a reply string, as required by the output schema. '
                     'Use plain prose and complete sentences. Start with a short sentence, usually 8–16 words, '
                     'then add necessary detail. Keep the facts and uncertainty.'})
        thread_id = thread['thread']['id']
        metrics['runtime_ready_ms'] = round((time.monotonic() - started) * 1000, 1)
        # Do not drop events that can precede the turn/start response.
        send({'id': 3, 'method': 'turn/start', 'params': {'threadId': thread_id,
              'input': [{'type': 'text', 'text': prompt, 'text_elements': []}],
              'effort': 'low', 'outputSchema': SCHEMA}})
        pending = []
        while turn_id is None:
            value = receive()
            if value.get('id') == 3 and 'method' not in value:
                if 'error' in value:
                    raise BridgeError('Supervisor could not start the reply.')
                turn_id = value['result']['turn']['id']
                metrics['turn_ready_ms'] = round((time.monotonic() - started) * 1000, 1)
            else:
                pending.append(value)
                if len(pending) > 512:
                    raise BridgeError('Supervisor did not confirm the request.')
        while True:
            value = pending.pop(0) if pending else receive()
            result = observe(value)
            if result is not None:
                metrics['model_ms'] = round((time.monotonic() - started) * 1000, 1)
                return {'reply': result, 'actions': [], 'select_agent_id': '', 'agent_choices': []}, metrics
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()
        selector.close()
        process.stdin.close(); process.stdout.close()
        track(process, False)
