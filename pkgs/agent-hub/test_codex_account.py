import copy
import unittest
from unittest.mock import Mock, patch

from bridge import BridgeError
from codex_account import CodexAccount
from native import NativeError


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.changed = Mock()
        self.account = CodexAccount(changed=self.changed)
        self.addCleanup(self.account.close)
        self.identity = {'type':'chatgpt', 'email':'first@example.test', 'planType':'plus', 'accessToken':'SECRET'}
        self.login = {'type':'chatgptDeviceCode', 'verificationUrl':'https://auth.openai.com/codex/device',
                      'userCode':'ABCD-1234', 'loginId':'fixture-login'}
        self.calls = []
        self.early = False
        def call(method, params):
            self.calls.append((method, params))
            if method == 'account/read':
                self.assertEqual(params, {'refreshToken':False})
                return {'account':copy.deepcopy(self.identity)}
            if method == 'account/login/start':
                self.assertEqual(params, {'type':'chatgptDeviceCode'})
                if self.early:
                    self.complete()
                return self.login
            if method == 'account/login/cancel':
                return {}
            self.fail(method)
        self.enterContext(patch.object(self.account, 'call', side_effect=call))
        self.enterContext(patch.object(self.account, 'available', return_value=True))

    def start(self):
        return self.account.start(self.account.status()['version'])

    def complete(self, success=True):
        self.account.event({'method':'account/login/completed', 'params':{
            'loginId':'fixture-login', 'success':success, 'error':'SECRET'}})

    def test_read_is_private_and_never_starts_auth(self):
        value = self.account.status()
        self.assertEqual(value['account']['label'], 'first@example.test')
        self.assertNotIn('SECRET', str(value))
        self.assertEqual([method for method, _ in self.calls], ['account/read'])
        self.changed.assert_not_called()

    def test_login_is_single_pending_flow_and_completion_invalidates_once(self):
        value = self.start()
        self.assertEqual(value['login']['code'], 'ABCD-1234')
        self.account.start(value['version'])
        self.assertEqual(sum(method == 'account/login/start' for method, _ in self.calls), 1)
        self.complete()
        self.identity['email'] = 'second@example.test'
        result = self.account.status()
        self.assertEqual(result['login']['status'], 'complete')
        self.assertNotIn('code', result['login'])
        self.assertNotIn('url', result['login'])
        self.account.status()
        self.changed.assert_called_once_with()

    def test_completion_arriving_before_start_response_is_kept(self):
        self.early = True
        self.start()
        self.assertEqual(self.account.status()['login']['status'], 'complete')
        self.changed.assert_called_once_with()

    def test_reconnect_can_recognize_new_account_but_logout_is_not_success(self):
        self.start()
        self.identity = None
        self.assertEqual(self.account.status()['login']['status'], 'pending')
        self.changed.assert_not_called()
        self.identity = {'type':'chatgpt', 'email':'new@example.test'}
        self.assertEqual(self.account.status()['login']['status'], 'complete')

    def test_expiry_failure_and_cancel_do_not_claim_an_account_change(self):
        self.start()
        self.complete(False)
        self.assertEqual(self.account.status()['login']['status'], 'failed')
        self.start()
        self.account.pending['started'] -= 1000
        self.assertEqual(self.account.status()['login']['status'], 'expired')
        self.start()
        with self.assertRaises(BridgeError):self.account.cancel('wrong-id')
        self.assertEqual(self.account.cancel('fixture-login')['login']['status'], 'cancelled')
        self.complete()
        self.assertEqual(self.account.status()['login']['status'], 'cancelled')
        self.changed.assert_not_called()

    def test_stale_version_and_untrusted_auth_urls_are_rejected(self):
        before = self.account.status()['version']
        self.identity['email'] = 'changed@example.test'
        with self.assertRaises(BridgeError):self.account.start(before)
        self.assertFalse(any(method == 'account/login/start' for method, _ in self.calls))
        for url in ('https://evil.test/', 'http://auth.openai.com/', 'https://auth.openai.com:bad/',
                    'https://name@auth.openai.com/', 'https://auth.openai.com:8443/', None, {}):
            with self.subTest(url=url):
                self.login['verificationUrl'] = url
                with self.assertRaises(BridgeError):self.start()

    def test_auth_error_does_not_export_provider_details_or_retry(self):
        version = self.account.status()['version']
        with patch.object(self.account, 'call', side_effect=[{'account':self.identity}, NativeError('SECRET auth token')]) as call:
            with self.assertRaises(BridgeError) as error:self.account.start(version)
            self.assertNotIn('SECRET', str(error.exception))
            self.assertEqual(call.call_count, 2)


if __name__ == '__main__':unittest.main()
