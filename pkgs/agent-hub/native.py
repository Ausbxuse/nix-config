"""Native Codex status and exact queued-input controls over its private socket.

Connecting never loads/resumes a thread or starts a daemon. A transcript/terminal
match must precede every use of thread metadata. Explicit model changes affect
only the verified thread's subsequent turns.
"""
from collections import OrderedDict
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import threading
import time

from bridge import BridgeError


class NativeError(BridgeError):
    pass


class NativeResponseError(NativeError):
    pass


class NativeInputUnavailable(NativeError):
    """Delivery could not start; no input operation was attempted."""


NativeImageUnavailable = NativeInputUnavailable


class NativeCodex:
    methods = frozenset(('thread/read', 'thread/queue/list', 'thread/queue/update', 'thread/queue/delete',
                         'model/list', 'thread/settings/update', 'turn/start', 'thread/queue/add'))
    capabilities = {'experimentalApi': True}

    def __init__(self, path=None, timeout=.6):
        self.path = Path(path or Path.home() / '.codex/app-server-control/app-server-control.sock')
        self.timeout, self.retry_at = timeout, 0
        self.ws, self.counter, self.socket_stamp = None, 0, None
        self.lock, self.cache = threading.RLock(), OrderedDict()
        self.model_cache = None
        self.queue_cache = {}

    def socket_info(self):
        try:
            # Codex keeps a stable alias to a per-daemon socket. Both the alias
            # and resolved private socket must belong to this user.
            if self.path.lstat().st_uid != os.getuid():
                return None
            path = self.path.resolve(strict=True)
            info = path.stat()
            if stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077:
                return path, (info.st_dev, info.st_ino)
        except OSError:
            pass
        return None

    def available(self):
        return time.monotonic() >= self.retry_at and self.socket_info() is not None

    def _close(self):
        old, self.ws = self.ws, None
        self.socket_stamp = None
        self.cache.clear()
        self.model_cache = None
        self.queue_cache.clear()
        if old:
            try: old.close(timeout=.1)
            except Exception: pass

    def close(self):
        with self.lock:
            self._close()

    def receive(self):
        raw = self.ws.recv()
        if not raw or len(raw) > 16 * 1024 * 1024:
            raise NativeError('Native connection ended or returned an oversized response')
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise NativeError('Invalid native response')
        return value

    def event(self, value):
        # Read-only connections never answer unsolicited server requests.
        if value.get('method') == 'thread/status/changed':
            self.cache.pop(value.get('params', {}).get('threadId'), None)
        if value.get('method') == 'thread/queue/changed':
            self.queue_cache.pop(value.get('params', {}).get('threadId'), None)

    def _exchange(self, method, params):
        self.counter += 1
        identity = 'hub-native-' + str(self.counter)
        self.ws.send(json.dumps({'id': identity, 'method': method, 'params': params}))
        deadline = time.monotonic() + self.timeout
        for _ in range(256):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NativeError('Native runtime did not answer in time')
            self.ws.settimeout(remaining)
            value = self.receive()
            if 'method' in value:
                self.event(value)
                continue
            if value.get('id') != identity:
                continue
            if 'error' in value:
                raise NativeResponseError(str(value['error'].get('message', 'Native request failed'))[:300])
            if not isinstance(value.get('result'), dict):
                raise NativeError('Invalid native response')
            return value['result']
        raise NativeError('Native runtime did not answer the request')

    def call(self, method, params):
        # Keep the allowed surface explicit. Never resume or create threads.
        if method not in self.methods:
            raise NativeError('Unsupported native operation')
        with self.lock:
            if not self.available():
                raise NativeError('Native runtime unavailable')
            try:
                path, stamp = self.socket_info()
                if self.ws is not None and stamp != self.socket_stamp:
                    self._close()
                if self.ws is None:
                    import websocket
                    transport = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    transport.settimeout(self.timeout)
                    try:
                        transport.connect(str(path))
                        _, uid, _ = struct.unpack('3i', transport.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                        if uid != os.getuid():
                            raise NativeError('Native runtime belongs to another user')
                        self.ws = websocket.create_connection('ws://localhost/', socket=transport, timeout=self.timeout,
                                                              suppress_origin=True, enable_multithread=True)
                        self.socket_stamp = stamp
                    except Exception:
                        transport.close()
                        raise
                    self._exchange('initialize', {'clientInfo': {'name':'agent_hub', 'version':'1'},
                                                  'capabilities': self.capabilities})
                    self.ws.send(json.dumps({'method':'initialized'}))
                return self._exchange(method, params)
            except NativeResponseError:
                raise
            except Exception:
                self._close()
                self.retry_at = time.monotonic() + 5
                raise NativeError('Native connection failed. Refresh before trying again.') from None

    def thread(self, binding, fresh=False):
        identity = binding.get('thread_id', '')
        if not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', identity):
            raise NativeError('Thread identity could not be verified')
        with self.lock:
            cached = self.cache.get(identity)
            if not fresh and cached and time.monotonic() - cached['at'] < .35:
                value = cached['value']
                self.cache.move_to_end(identity)
            else:
                source = self.call('thread/read', {'threadId':identity, 'includeTurns':False}).get('thread', {})
                value = {key: source.get(key) for key in ('id', 'cwd', 'path', 'status', 'ephemeral', 'canAcceptDirectInput',
                                                        'model', 'modelProvider', 'reasoningEffort')}
                self.cache[identity] = {'at':time.monotonic(), 'value':value}
                while len(self.cache) > 64:
                    self.cache.popitem(last=False)
            if (value['id'] != identity or value['cwd'] != binding.get('cwd') or not value.get('path') or
                    Path(value['path']).resolve() != Path(binding.get('path', '')).resolve()):
                raise NativeError('Runtime thread no longer matches this pane')
            if not isinstance(value.get('status'), dict):
                raise NativeError('Runtime did not provide a structured status')
            return copy.deepcopy(value)

    def send_images(self, binding, message, paths, command_id, queued=False):
        return self.send_input(binding, message, paths, command_id, queued)

    def send_input(self, binding, message, paths, command_id, queued=False):
        if not isinstance(message, str) or not message.strip() or len(message) > 12000 or '\x00' in message:
            raise NativeError('Use a message between 1 and 12,000 characters.')
        if queued and message.lstrip().startswith(('/', '!')):
            raise NativeError('Queue a plain-text instruction. Use the terminal for slash or shell commands.')
        with self.lock:
            try:
                thread = self.thread(binding, fresh=True)
            except NativeError as exc:
                raise NativeInputUnavailable(str(exc)) from exc
            status = thread['status']
            if thread.get('ephemeral') or thread.get('canAcceptDirectInput') is not True:
                raise NativeInputUnavailable('This agent cannot accept direct input right now.')
            active = status.get('type') == 'active'
            if status.get('activeFlags') or status.get('type') not in ('idle', 'active', 'systemError'):
                raise NativeError('Resolve this agent’s pending decision before sending.')
            if active and not queued:
                raise NativeError('This agent is working. Queue the message or wait for its reply.')
            params = {'threadId': binding['thread_id'], 'clientUserMessageId': command_id,
                      'input': [{'type':'text', 'text':message, 'text_elements':[]}] +
                               [{'type':'localImage', 'path':path} for path in paths]}
            # The runtime decodes and copies local images before it replies.
            # Keep the short status timeout, but allow this one explicit write
            # enough time. Never retry a write with an uncertain result.
            timeout = self.timeout
            try:
                self.timeout = max(timeout, 5)
                self.call('thread/queue/add' if active else 'turn/start', params)
            finally:
                self.timeout = timeout
            self.cache.pop(binding['thread_id'], None)
            self.queue_cache.pop(binding['thread_id'], None)
            return 'queued' if active else 'sent'

    def observe(self, binding, terminal, fresh=False):
        try:
            value = self.thread(binding, fresh=fresh)
        except (NativeError, OSError, ValueError, TypeError, KeyError):
            if fresh:
                return {**terminal, 'state':'unknown', 'summary':'Live runtime status unavailable',
                        'completion_confirmed':False, 'activity_confirmed':False}
            return terminal
        status = value['status']
        result = {**terminal, 'runtime_status': {'source':'codex', 'state':status.get('type')}}
        if value.get('model'):
            result['model_settings'] = {'model':value['model'], 'effort':value.get('reasoningEffort')}
        if status.get('type') == 'active':
            flags = status.get('activeFlags')
            result['completion_confirmed'] = False
            if isinstance(flags, list) and flags and all(flag in ('waitingOnApproval', 'waitingOnUserInput') for flag in flags):
                result.update(state='waiting', summary='Needs approval in the agent' if 'waitingOnApproval' in flags else 'Needs input in the agent',
                              activity_confirmed=False, activity_phase='', activity_elapsed=None, capacity_error=False)
            else:
                # An active thread is never a completed handoff. New runtime
                # flags must not resurrect an older terminal completion.
                result.update(state='running', summary='Working', activity_confirmed=True, capacity_error=False,
                              activity_phase=terminal.get('activity_phase') or 'Working')
        elif status.get('type') == 'systemError':
            # Native status confirms the failure but carries no reason. Keep a
            # current terminal capacity error so the host's retry loop sees it.
            # Active work/approval above still supersedes stale terminal text.
            capacity = bool(terminal.get('capacity_error'))
            result.update(state='blocked', summary=terminal['summary'] if capacity else 'Agent runtime error',
                          activity_confirmed=False, activity_phase='', activity_elapsed=None,
                          capacity_error=capacity, completion_confirmed=False)
        elif status.get('type') != 'idle':
            return terminal
        # Idle is not completion. Terminal/lifecycle evidence still decides
        # whether the agent needs input, ended a turn, or has not started one.
        return result

    def models(self):
        with self.lock:
            if self.model_cache and time.monotonic() - self.model_cache['at'] < 300:
                return copy.deepcopy(self.model_cache['models'])
            models, cursor = [], None
            for _ in range(8):
                result = self.call('model/list', {'limit':32, 'includeHidden':False, **({'cursor':cursor} if cursor else {})})
                rows = result.get('data')
                if not isinstance(rows, list) or len(rows) > 32:
                    raise NativeError('The runtime returned an invalid model list')
                for row in rows:
                    model = row.get('model')
                    efforts = [e.get('reasoningEffort') for e in row.get('supportedReasoningEfforts', [])]
                    if not isinstance(model, str) or not model or not all(isinstance(e, str) and e for e in efforts):
                        raise NativeError('The runtime returned invalid model settings')
                    models.append({'id':model, 'label':row.get('displayName') or model, 'efforts':efforts,
                                   'default_effort':row.get('defaultReasoningEffort')})
                cursor = result.get('nextCursor')
                if not cursor:
                    break
            if cursor or not models or len({row['id'] for row in models}) != len(models):
                raise NativeError('Could not read the complete model list. Refresh and try again.')
            self.model_cache = {'at':time.monotonic(), 'models':models}
            return copy.deepcopy(models)

    @staticmethod
    def settings_version(binding, thread):
        return hashlib.sha256(json.dumps([binding['thread_id'], thread.get('model'),
            thread.get('reasoningEffort')], sort_keys=True).encode()).hexdigest()

    def model_settings(self, binding):
        thread = self.thread(binding, fresh=True)
        if (thread.get('ephemeral') or thread['status'].get('type') == 'notLoaded' or
                thread.get('canAcceptDirectInput') is not True or not thread.get('model')):
            raise NativeError('This Codex session does not expose model controls. Update or reopen its CLI.')
        return {'runtime':'codex', 'session':binding['thread_id'], 'model':thread['model'],
                'effort':thread.get('reasoningEffort'), 'version':self.settings_version(binding, thread),
                'models':self.models(), 'note':'Applies to the next turn.'}

    def change_model(self, binding, expected, model, effort):
        with self.lock:
            current = self.model_settings(binding)
            if not isinstance(expected, str) or expected != current['version']:
                raise NativeError('Model settings changed in another window. Refresh before applying.')
            choice = next((row for row in current['models'] if row['id'] == model), None)
            if not choice or (effort not in choice['efforts'] if choice['efforts'] else effort is not None):
                raise NativeError('Choose a model and effort supported by this agent.')
            if current['model'] == model and current['effort'] == effort:
                return current
            # Do not resume a session, start a turn, interrupt work, alter shared
            # defaults, or pass any permission/instruction fields from the UI.
            self.call('thread/settings/update', {'threadId':binding['thread_id'], 'model':model,
                                                 **({'effort':effort} if effort is not None else {})})
            self.cache.pop(binding['thread_id'], None)
            result = self.model_settings(binding)
            if result['model'] != model or choice['efforts'] and result['effort'] != effort:
                raise NativeError('The runtime did not confirm this change. Refresh to check its settings.')
            return result

    @staticmethod
    def queued_item(binding, item):
        if (not isinstance(item, dict) or not isinstance(item.get('id'), str) or not 0 < len(item['id']) <= 160 or
                not isinstance(item.get('input'), list) or not all(isinstance(part, dict) for part in item['input'])):
            raise NativeError('Invalid queued input')
        text = '\n'.join(part.get('text', '') for part in item['input'] if part.get('type') == 'text' and isinstance(part.get('text'), str))
        editable = (bool(text) and all(
            part.get('type') == 'text' and isinstance(part.get('text'), str) and not part.get('text_elements') or
            part.get('type') == 'image' and isinstance(part.get('url'), str) or
            part.get('type') == 'localImage' and isinstance(part.get('path'), str)
            for part in item['input']) and len(text) <= 12000)
        digest = hashlib.sha256(json.dumps([binding['thread_id'], item], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return {'id':item['id'], 'digest':digest, 'text':text[:12000], 'editable':editable,
                'has_attachments':any(part.get('type') != 'text' for part in item['input'])}

    def queue_inputs(self, binding):
        items, cursor = [], None
        # Keep image bytes inside this process; browsers receive display fields.
        for _ in range(16):
            reply = self.call('thread/queue/list', {'threadId':binding['thread_id'], 'limit':1, **({'cursor':cursor} if cursor else {})})
            if not isinstance(reply.get('data'), list) or len(reply['data']) > 32:
                raise NativeError('Invalid native queue')
            for item in reply['data']:
                self.queued_item(binding, item)
            items.extend(reply['data'])
            cursor = reply.get('nextCursor')
            if not cursor:
                break
        if len({item['id'] for item in items}) != len(items):
            raise NativeError('Native queue changed while reading it. Refresh before editing.')
        return items, cursor

    def queue(self, binding, fresh=False):
        with self.lock:
            thread = self.thread(binding)
            if thread.get('ephemeral') or thread['status'].get('type') == 'notLoaded' or thread.get('canAcceptDirectInput') is not True:
                raise NativeError('This runtime does not expose an editable queue')
            cached = self.queue_cache.get(binding['thread_id'])
            if not fresh and cached and time.monotonic() - cached['at'] < 2:
                return copy.deepcopy(cached['value'])
            inputs, cursor = self.queue_inputs(binding)
            items = [self.queued_item(binding, item) for item in inputs]
            version = hashlib.sha256(json.dumps([binding['thread_id'], items, bool(cursor)], sort_keys=True).encode()).hexdigest()[:24]
            value = {'status':'available', 'thread_id':binding['thread_id'], 'items':items, 'version':version, 'more':bool(cursor)}
            if binding['thread_id'] not in self.queue_cache and len(self.queue_cache) >= 64:
                self.queue_cache.pop(next(iter(self.queue_cache)))
            self.queue_cache[binding['thread_id']] = {'at':time.monotonic(), 'value':copy.deepcopy(value)}
            return value

    def change_queue(self, binding, queue_id, expected, message=None):
        if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
            raise NativeError('Refresh the queued message before editing it')
        if message is not None and (not isinstance(message, str) or not message.strip() or len(message) > 12000 or '\x00' in message):
            raise NativeError('Use a message between 1 and 12,000 characters')
        with self.lock:
            thread = self.thread(binding, fresh=True)
            if thread.get('ephemeral') or thread['status'].get('type') == 'notLoaded' or thread.get('canAcceptDirectInput') is not True:
                raise NativeError('This runtime does not expose an editable queue')
            inputs, _ = self.queue_inputs(binding)
            original = next((item for item in inputs if item['id'] == queue_id), None)
            if not original:
                raise NativeError('This input already left the queue. Check the conversation before sending anything else.')
            item = self.queued_item(binding, original)
            if item['digest'] != expected:
                raise NativeError('This queued message changed. Refresh before editing it.')
            params = {'threadId':binding['thread_id'], 'queuedSubmissionId':queue_id}
            if message is not None:
                if not item['editable']:
                    raise NativeError('Edit inputs with mentions or special text spans in the terminal.')
                params['input'] = [{'type':'text', 'text':message, 'text_elements':[]}] + [
                    copy.deepcopy(part) for part in original['input'] if part.get('type') != 'text']
            # The runtime atomically looks up this queued ID; a dequeued/running
            # submission cannot be edited by this method. Never retry on failure.
            timeout = self.timeout
            try:
                self.timeout = max(timeout, 5)
                self.call('thread/queue/update' if message is not None else 'thread/queue/delete', params)
            finally:
                self.timeout = timeout
                self.queue_cache.pop(binding['thread_id'], None)
