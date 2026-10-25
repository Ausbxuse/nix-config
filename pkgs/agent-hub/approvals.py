"""Exact, explicit approval decisions on separately attached native connections.

No policy/configuration overrides, session-wide grants, new turns or tool calls.
Only pending requests received from the verified runtime can become UI actions.
"""
from collections import OrderedDict
import copy
import hashlib
import json
import secrets
import threading
import time

from native import NativeCodex, NativeError


METHODS = {
    'item/commandExecution/requestApproval': 'command',
    'item/fileChange/requestApproval': 'files',
    'item/permissions/requestApproval': 'permissions',
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def present(request, changes=None):
    """Return complete approval evidence or omit Allow; never truncate and allow."""
    method, params = request['method'], request['params']
    kind = METHODS[method]
    result = {'kind': kind, 'reason': params.get('reason') or '', 'cwd': params.get('cwd') or '',
              'decisions': ['accept', 'decline', 'cancel'], 'scope': 'once', 'note': ''}
    complete = all(isinstance(result[key], str) and len(result[key]) <= limit
                   for key, limit in (('reason', 12000), ('cwd', 2000)))
    complete &= all(params.get(key) is None or isinstance(params[key], str) for key in ('reason', 'cwd', 'environmentId'))
    result['environment'] = params.get('environmentId') or ''
    if kind == 'command':
        decisions = params.get('availableDecisions')
        if decisions is not None:
            result['decisions'] = [decision for decision in result['decisions'] if isinstance(decisions, list) and decision in decisions]
        result['command'] = params.get('command') or ''
        result['network'] = params.get('networkApprovalContext')
        result['access'] = params.get('additionalPermissions')
        result['kind'] = 'network' if result['network'] else 'stdin' if params.get('kind') == 'stdin' else 'command'
        complete &= params.get('kind', 'command') in ('command', 'stdin')
        complete &= isinstance(result['command'], str) and len(result['command']) <= 16000 and bool(result['command'] or result['network'])
    elif kind == 'files':
        result['changes'] = changes or []
        complete &= bool(changes) and len(changes) <= 60 and not params.get('grantRoot')
        valid_changes = isinstance(changes, list) and all(isinstance(change, dict) and isinstance(change.get('path'), str) and
                        isinstance(change.get('diff'), str) and isinstance(change.get('kind'), dict) and change['kind'].get('type') in ('add', 'update', 'delete')
                        for change in result['changes'])
        complete &= valid_changes
        if not valid_changes:
            result['changes'] = []
        if params.get('grantRoot'):
            result['note'] = 'This request grants writes for the session. Review it in the terminal.'
    else:
        result['scope'], result['access'] = 'turn', params.get('permissions')
        result['decisions'] = ['accept', 'decline']
        complete &= isinstance(result['access'], dict) and bool(result['access'])
    if len(json.dumps(result)) > 120000:
        # A large/incomplete preview never authorizes hidden work.
        result = {**result, 'command': '', 'changes': [], 'access': None, 'network': None, 'reason': '', 'cwd': ''}
        complete = False
    if not complete:
        result['decisions'] = [value for value in result['decisions'] if value != 'accept']
        result['note'] = result['note'] or 'Open the terminal to review the complete request.'
    for key in ('reason', 'cwd', 'command', 'environment'):
        if key in result and not isinstance(result[key], str):
            result[key] = ''
    return result


class ApprovalClient(NativeCodex):
    methods = frozenset(('thread/read', 'thread/resume'))
    capabilities = {'experimentalApi': True, 'optOutNotificationMethods': [
        'item/agentMessage/delta', 'item/reasoning/textDelta', 'item/reasoning/summaryTextDelta',
        'item/reasoning/summaryPartAdded', 'item/commandExecution/outputDelta', 'item/fileChange/outputDelta',
        'rawResponseItem/completed', 'thread/tokenUsage/updated',
    ]}

    def __init__(self, path=None):
        super().__init__(path)
        self.pending, self.resolved, self.changes = OrderedDict(), set(), {}
        self.binding, self.epoch = None, secrets.token_hex(16)
        self.touched = time.monotonic()

    def _close(self):
        super()._close()
        self.pending.clear(); self.resolved.clear(); self.changes.clear()
        self.binding, self.epoch = None, secrets.token_hex(16)

    def event(self, value):
        super().event(value)
        params = value.get('params', {})
        if not isinstance(params, dict) or not self.binding or params.get('threadId') != self.binding['thread_id']:
            return
        method = value.get('method')
        if method in METHODS and type(value.get('id')) in (int, str):
            if (not isinstance(params.get('turnId'), str) or not isinstance(params.get('itemId'), str) or
                    len(json.dumps(params)) > 128000):
                return
            identity = json.dumps(value['id'])
            self.resolved.discard(identity)
            self.pending[identity] = {'id':value['id'], 'method':method, 'params':copy.deepcopy(params)}
            while len(self.pending) > 16:
                self.pending.popitem(last=False)
        elif method == 'serverRequest/resolved':
            identity = json.dumps(params.get('requestId'))
            self.pending.pop(identity, None)
            self.resolved.add(identity)
            if len(self.resolved) > 128:
                self.resolved = {identity}
        elif method in ('turn/completed', 'thread/closed'):
            turn = (params.get('turn') or {}).get('id')
            self.pending = OrderedDict((key, request) for key, request in self.pending.items()
                                       if method != 'thread/closed' and request['params']['turnId'] != turn)
        elif method in ('item/started', 'item/updated'):
            item = params.get('item') or {}
            if item.get('type') == 'fileChange' and len(self.changes) < 64 and len(json.dumps(item)) <= 128000:
                self.changes[(params.get('turnId'), item.get('id'))] = item.get('changes', [])

    def resume(self, binding, hydrate=False):
        response = self.call('thread/resume', {'threadId': binding['thread_id'], 'path': binding['path'], 'excludeTurns': True,
            **({'initialTurnsPage': {'limit': 1, 'itemsView': 'full', 'sortDirection': 'desc'}} if hydrate else {})})
        current = response.get('thread', {})
        if (self.binding != binding or current.get('id') != binding['thread_id'] or
                current.get('cwd') != binding['cwd'] or current.get('path') != binding['path']):
            self.close()
            raise NativeError('Approval thread changed. Refresh before acting.')
        return response

    def drain(self, duration=.025, until=None):
        import websocket
        end = time.monotonic() + duration
        for _ in range(256):
            if until is not None and until in self.resolved:
                return True
            if time.monotonic() >= end:
                break
            self.ws.settimeout(max(.001, end-time.monotonic()))
            try:
                value = self.receive()
            except websocket.WebSocketTimeoutException:
                break
            if 'method' in value:
                self.event(value)
        return until is not None and until in self.resolved

    def sync(self, binding, hydrate=False):
        self.touched = time.monotonic()
        thread = self.thread(binding, fresh=True)
        status = thread['status']
        if status.get('type') != 'active' or 'waitingOnApproval' not in (status.get('activeFlags') or []):
            self.close()
            return []
        if self.binding != binding:
            # The installed protocol rejoins a loaded thread. Omitted options
            # preserve the owner's model, sandbox and approval settings.
            self.binding = copy.deepcopy(binding)
            self.resume(binding)
        self.drain()
        files = [request for request in self.pending.values() if METHODS[request['method']] == 'files']
        if files and (hydrate or any((r['params']['turnId'], r['params']['itemId']) not in self.changes for r in files)):
            response = self.resume(binding, hydrate=True)
            self.changes.clear()
            for turn in (response.get('initialTurnsPage') or {}).get('data', []):
                for item in turn.get('items', []):
                    if item.get('type') == 'fileChange' and item.get('status') == 'inProgress' and len(self.changes) < 64:
                        self.changes[(turn['id'], item['id'])] = item.get('changes', [])
            self.drain()
        rows = []
        for request in self.pending.values():
            params = request['params']
            evidence = present(request, self.changes.get((params['turnId'], params['itemId'])))
            rows.append({**evidence, 'id': digest([self.epoch, request['id'], params['threadId'], params['turnId'], params['itemId']])[:32],
                         'digest': digest([request, evidence])})
        return rows

    def answer(self, binding, identity, expected, decision):
        with self.lock:
            rows = self.sync(binding, hydrate=True)
            row = next((row for row in rows if row['id'] == identity and row['digest'] == expected), None)
            if not row or decision not in row['decisions']:
                raise NativeError('This approval changed or was already answered. Refresh before acting.')
            request = next(request for request in self.pending.values()
                           if digest([self.epoch, request['id'], request['params']['threadId'], request['params']['turnId'], request['params']['itemId']])[:32] == identity)
            result = ({'permissions':request['params']['permissions'] if decision == 'accept' else {}, 'scope':'turn'}
                      if row['kind'] == 'permissions' else {'decision':decision})
            key = json.dumps(request['id'])
            try:
                self.ws.send(json.dumps({'id':request['id'], 'result':result}))
                if not self.drain(self.timeout, until=key):
                    raise NativeError('Decision sent; confirmation is pending. Refresh before acting again.')
            except Exception:
                self.close()
                raise NativeError('Decision sent; confirmation is unavailable. Refresh before acting again.') from None
            # Leave the owner's connection and work intact; this observer is done.
            self.close()
            return {'ok':True, 'message':'Approval request resolved'}


class Approvals:
    def __init__(self, path=None):
        self.path, self.clients, self.lock = path, OrderedDict(), threading.RLock()
        self.stop, self.reaper = threading.Event(), None

    def expire(self):
        with self.lock:
            now = time.monotonic()
            for key, client in list(self.clients.items()):
                if now-client.touched > 30:
                    self.clients.pop(key).close()

    def maintain(self):
        while not self.stop.wait(10):
            self.expire()

    def _client(self, binding):
        self.expire()
        if self.stop.is_set():
            raise NativeError('Approval observer stopped')
        if self.reaper is None:
            self.reaper = threading.Thread(target=self.maintain, name='hub-approval-expiry', daemon=True)
            self.reaper.start()
        identity = binding['thread_id']
        if identity not in self.clients:
            self.clients[identity] = ApprovalClient(self.path)
        self.clients.move_to_end(identity)
        while len(self.clients) > 8:
            self.clients.popitem(last=False)[1].close()
        return self.clients[identity]

    def read(self, binding):
        with self.lock:
            client = self._client(binding)
            try:
                with client.lock:
                    items = client.sync(binding)
                return {'status':'available', 'thread_id':binding['thread_id'], 'items':items, 'version':digest(items)[:24]}
            except Exception:
                client.close()
                raise NativeError('Approval details are unavailable. Refresh or use the terminal.') from None

    def answer(self, binding, identity, expected, decision):
        with self.lock:
            client = self.clients.get(binding['thread_id'])
            if not client:
                raise NativeError('Refresh the approval before answering it.')
            return client.answer(binding, identity, expected, decision)

    def forget(self, thread_id):
        with self.lock:
            client = self.clients.pop(thread_id, None)
            if client:
                client.close()

    def close(self):
        self.stop.set()
        with self.lock:
            for client in self.clients.values():
                client.close()
            self.clients.clear()
