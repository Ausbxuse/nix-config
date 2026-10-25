"""Host-wide Codex device sign-in. Tokens never leave Codex's auth manager."""
import hashlib
import json
import re
import time
from urllib.parse import urlsplit

from bridge import BridgeError
from native import NativeCodex, NativeError


class CodexAccount(NativeCodex):
    methods = frozenset(('account/read', 'account/login/start', 'account/login/cancel'))

    def __init__(self, changed=lambda: None, **kwargs):
        super().__init__(timeout=12, **kwargs)
        self.changed = changed
        self.pending = None
        self.completions = {}
        self.starting = False

    def event(self, value):
        super().event(value)
        if value.get('method') == 'account/login/completed':
            params = value.get('params', {})
            identity = params.get('loginId')
            if isinstance(identity, str) and (self.starting or self.pending and identity == self.pending['id']):
                # Do not forward upstream error strings: they can contain auth
                # URLs or provider internals. The browser gets a fixed status.
                self.completions[identity] = params.get('success') is True
                while len(self.completions) > 8:
                    self.completions.pop(next(iter(self.completions)))

    @staticmethod
    def account(value):
        account = value.get('account') if isinstance(value, dict) else None
        account = account if isinstance(account, dict) else {}
        kind = account.get('type')
        if kind not in ('apiKey', 'chatgpt', 'amazonBedrock'):
            return {'type': 'none', 'label': 'Not signed in'}
        email = account.get('email')
        label = email[:200] if kind == 'chatgpt' and isinstance(email, str) and email else {
            'chatgpt': 'ChatGPT', 'apiKey': 'API key', 'amazonBedrock': 'Amazon Bedrock'}[kind]
        plan = account.get('planType')
        return {'type': kind, 'label': label,
                'plan': plan[:60] if isinstance(plan, str) and re.fullmatch(r'[a-z0-9_]+', plan) else ''}

    def status(self):
        with self.lock:
            if not self.available():
                return {'available': False, 'account': None, 'login': None,
                        'note': 'Open a Codex agent on this host to manage its account.'}
            try:
                account = self.account(self.call('account/read', {'refreshToken': False}))
            except NativeError:
                raise BridgeError('Could not read the Codex account. Refresh to try again.') from None
            version = hashlib.sha256(json.dumps([account, self.socket_stamp]).encode()).hexdigest()
            login = None
            if self.pending:
                state = self.completions.get(self.pending['id'])
                if (state is None and self.pending['status'] == 'pending' and account['type'] == 'chatgpt'
                        and account != self.pending['previous_account']):
                    state = True  # Recover after a socket reconnect lost the notification.
                if state is not None and self.pending['status'] == 'pending':
                    self.pending['status'] = 'complete' if state else 'failed'
                    if state and not self.pending.get('reported'):
                        self.pending['reported'] = True
                        self.changed()
                elif self.pending['status'] == 'pending' and time.monotonic() - self.pending['started'] > 15 * 60:
                    self.pending['status'] = 'expired'
                login = {key: self.pending[key] for key in ('id', 'status', 'url', 'code')}
                if login['status'] != 'pending':
                    login.pop('code'); login.pop('url')
            return {'available': True, 'account': account, 'version': version, 'login': login}

    def start(self, version):
        with self.lock:
            current = self.status()
            if not current['available'] or current['version'] != version:
                raise BridgeError('The host account changed. Refresh before signing in.')
            if current['login'] and current['login']['status'] == 'pending':
                return current
            self.completions.clear()
            self.starting = True
            try:
                result = self.call('account/login/start', {'type': 'chatgptDeviceCode'})
            except NativeError:
                raise BridgeError('Device sign-in could not start. Enable device code login in your ChatGPT security settings, then try again.') from None
            finally:
                self.starting = False
            result = result if isinstance(result, dict) else {}
            url, code, identity = result.get('verificationUrl', ''), result.get('userCode', ''), result.get('loginId', '')
            try:
                parsed = urlsplit(url) if isinstance(url, str) else None
                valid_url = (parsed and parsed.scheme == 'https'
                             and parsed.hostname in ('auth.openai.com', 'auth0.openai.com', 'auth.chatgpt.com', 'chatgpt.com')
                             and not parsed.username and not parsed.password and parsed.port in (None, 443))
            except ValueError:
                valid_url = False
            if (result.get('type') != 'chatgptDeviceCode' or not valid_url
                    or not isinstance(code, str) or not re.fullmatch(r'[A-Za-z0-9 -]{4,32}', code)
                    or not isinstance(identity, str) or not 1 <= len(identity) <= 200):
                raise BridgeError('Codex returned an unsupported sign-in response. Use its terminal login instead.')
            self.pending = dict(id=identity, status='pending', url=url, code=code, started=time.monotonic(), previous_account=current['account'])
            return {**current, 'login': {key: self.pending[key] for key in ('id', 'status', 'url', 'code')}}

    def cancel(self, identity):
        with self.lock:
            if not self.pending or self.pending['id'] != identity:
                raise BridgeError('This sign-in request is no longer active.')
            if self.pending['status'] == 'pending':
                try:
                    self.call('account/login/cancel', {'loginId': identity})
                except NativeError:
                    raise BridgeError('Could not confirm cancellation. Refresh the account status.') from None
                self.pending['status'] = 'cancelled'
            return self.status()
