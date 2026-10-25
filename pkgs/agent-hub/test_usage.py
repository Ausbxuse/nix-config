"""Quota accuracy, privacy, persistence, and a read-only app-server exchange."""
import json
import io
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from usage import (ClaudeUsageMonitor, NoUsageRedirect, UsageMonitor, UsageUnavailable,
                   claude_limits, read_claude_limits, read_claude_cached_limits, read_codex_limits, weekly_limits)


def window(percent, minutes=10080, reset=2000):
    return {'usedPercent':percent,'windowDurationMins':minutes,'resetsAt':reset}


class UsageTests(unittest.TestCase):
    def test_account_change_discards_an_old_inflight_quota_read(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor = UsageMonitor(directory, clock=lambda:1000)
            started, finish = threading.Event(), threading.Event()
            old = weekly_limits({'rateLimits':{'primary':window(91)}})
            new = weekly_limits({'rateLimits':{'primary':window(4)}})
            def read_old():
                started.set()
                finish.wait(2)
                return old
            with patch.object(monitor, 'read', side_effect=read_old):
                task = threading.Thread(target=monitor.refresh)
                task.start()
                try:
                    self.assertTrue(started.wait(2))
                    monitor.invalidate()
                    self.assertEqual(monitor.snapshot()['weekly'], [])
                finally:
                    finish.set(); task.join(2)
            self.assertEqual(monitor.snapshot()['weekly'], [])
            self.assertEqual(UsageMonitor(directory,clock=lambda:1000).snapshot()['weekly'], [])
            with patch.object(monitor, 'read', return_value=new):monitor.refresh()
            self.assertEqual(monitor.snapshot()['weekly'][0]['used_percent'], 4)

    def test_weekly_window_can_be_primary_or_secondary(self):
        for primary, secondary in ((window(61),None),(window(4,300),window(61))):
            with self.subTest(primary=primary,secondary=secondary):
                weekly=weekly_limits({'rateLimits':{'limitId':'codex','primary':primary,'secondary':secondary}})
                self.assertEqual(len(weekly),1)
                self.assertEqual(weekly[0]['used_percent'],61)
                self.assertEqual(weekly[0]['resets_at'],2000)

    def test_multiple_buckets_stay_separate_and_private_fields_are_dropped(self):
        value={'accountId':'PRIVATE_ACCOUNT','rateLimitResetCredits':{'credits':[{'id':'PRIVATE_CAPABILITY'}]},
               'rateLimits':{'primary':window(99)},'rateLimitsByLimitId':{
               'codex':{'primary':window(73),'credits':{'balance':'PRIVATE_BALANCE'}},
               'reserve':{'limitName':'Reserve','primary':window(0)}}}
        weekly=weekly_limits(value)
        self.assertEqual([x['used_percent'] for x in weekly],[73,0])
        self.assertNotIn('PRIVATE',json.dumps(weekly))

    def test_unknown_windows_are_not_invented(self):
        self.assertEqual(weekly_limits({'rateLimits':{'primary':window(10,300),'secondary':None}}),[])
        for percent in (True,-1,float('nan'),float('inf'),'90'):
            with self.subTest(percent=percent):
                self.assertEqual(weekly_limits({'rateLimits':{'primary':window(percent)}}),[])
        weekly=weekly_limits({'rateLimits':{'primary':window(0,reset=None)}})
        self.assertEqual(weekly[0]['used_percent'],0)
        self.assertIsNone(weekly[0]['resets_at'])

    def test_failure_and_restart_keep_last_report_marked_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            monitor=UsageMonitor(directory,clock=lambda:1000)
            sample=weekly_limits({'rateLimits':{'primary':window(100)}})
            with patch('usage.read_codex_limits',return_value=sample): monitor.refresh()
            self.assertFalse(monitor.snapshot()['stale'])
            with patch('usage.read_codex_limits',side_effect=UsageUnavailable()): monitor.refresh()
            self.assertTrue(monitor.snapshot()['stale'])
            restored=UsageMonitor(directory,clock=lambda:1100)
            self.assertEqual(restored.snapshot()['weekly'][0]['used_percent'],100)
            self.assertTrue(restored.snapshot()['stale'])
            self.assertEqual(restored.snapshot()['observed_at'],1000)
            self.assertEqual((Path(directory)/'usage.json').stat().st_mode & 0o777,0o600)

    def test_empty_report_replaces_old_quota_and_bad_cache_is_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'usage.json'
            path.write_text('{"observed_at":"nan","weekly":[]}')
            monitor=UsageMonitor(directory,clock=lambda:1000)
            self.assertEqual(monitor.snapshot()['observed_at'],0)
            with patch('usage.read_codex_limits',return_value=[]): monitor.refresh()
            self.assertEqual(monitor.snapshot()['status'],'not_reported')
            self.assertEqual(monitor.snapshot()['weekly'],[])

    def test_reader_only_initializes_and_reads_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'fake-codex'
            path.write_text('''#!/usr/bin/env python3
import json,sys
from pathlib import Path
assert sys.argv[1:]==['app-server','--stdio']
methods=[]
for line in sys.stdin:
 v=json.loads(line);methods.append(v['method'])
 if v['method']=='initialize': print(json.dumps({'id':1,'result':{}}),flush=True)
 elif v['method']=='initialized': pass
 elif v['method']=='account/rateLimits/read':
  print(json.dumps({'id':2,'result':{'rateLimits':{'primary':{'usedPercent':42,'windowDurationMins':10080,'resetsAt':2000}}}}),flush=True)
 else: raise RuntimeError('A mutating method was sent')
Path('methods.json').write_text(json.dumps(methods))
''')
            path.chmod(0o700)
            result=read_codex_limits(str(path),directory,timeout=3)
            self.assertEqual(result[0]['used_percent'],42)
            self.assertEqual(json.loads((Path(directory)/'methods.json').read_text()),['initialize','initialized','account/rateLimits/read'])


class ClaudeUsageTests(unittest.TestCase):
    def response(self):
        return {'five_hour': {'utilization': 26, 'resets_at': '2030-01-01T12:00:00Z'},
                'seven_day': {'utilization': 17, 'resets_at': '2030-01-07T12:00:00+00:00'},
                'seven_day_sonnet': {'utilization': 0, 'resets_at': None},
                'seven_day_opus': None, 'extra_usage': {'balance': 'PRIVATE_BALANCE'},
                'account_id': 'PRIVATE_ACCOUNT'}

    def credentials(self, directory, expires=4_000_000_000_000):
        path = Path(directory) / '.credentials.json'
        path.write_text(json.dumps({'claudeAiOauth': {'accessToken': 'PRIVATE_ACCESS',
                         'refreshToken': 'PRIVATE_REFRESH', 'expiresAt': expires}}))
        os.utime(path, (900, 900))
        return path

    def test_claude_windows_and_privacy(self):
        result = claude_limits(self.response())
        self.assertEqual([row['window_minutes'] for row in result], [300, 10080, 10080])
        self.assertEqual([row['used_percent'] for row in result], [26, 17, 0])
        self.assertEqual(result[0]['resets_at'], 1893499200)
        self.assertEqual(result[2]['label'], 'Claude Sonnet')
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_missing_and_invalid_limits_are_not_zero_usage(self):
        self.assertEqual(claude_limits({'five_hour': None, 'seven_day': None}), [])
        for used in (None, True, -1, float('nan'), float('inf'), '26'):
            self.assertEqual(claude_limits({'five_hour': {'utilization': used}}), [])
        with self.assertRaises(UsageUnavailable):
            claude_limits({'error': 'PRIVATE_AUTH_ERROR'})
        for reset in ('invalid', '2030-01-01T12:00:00', [], float('nan')):
            result = claude_limits({'seven_day': {'utilization': 0, 'resets_at': reset}})
            self.assertIsNone(result[0]['resets_at'])

    def test_missing_or_expired_credentials_make_no_network_request(self):
        with tempfile.TemporaryDirectory() as directory, patch('usage.build_opener') as network:
            with self.assertRaises(UsageUnavailable) as error:
                read_claude_limits(Path(directory) / 'missing')
            self.assertEqual(error.exception.reason, 'not_signed_in')
            with self.assertRaises(UsageUnavailable) as error:
                read_claude_limits(self.credentials(directory, expires=900_000), now=1000)
            self.assertEqual(error.exception.reason, 'auth_required')
            network.assert_not_called()

    def test_reader_uses_only_usage_get_and_keeps_credentials_unchanged(self):
        with tempfile.TemporaryDirectory() as directory, patch('usage.build_opener') as network:
            path = self.credentials(directory)
            original = path.read_bytes()
            network.return_value.open.return_value = io.BytesIO(json.dumps(self.response()).encode())
            report = read_claude_limits(path, now=1000)
            request = network.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, 'https://api.anthropic.com/api/oauth/usage')
            self.assertEqual(request.get_method(), 'GET')
            self.assertEqual(request.get_header('Authorization'), 'Bearer PRIVATE_ACCESS')
            self.assertIsNone(request.data)
            self.assertIsInstance(network.call_args.args[0], NoUsageRedirect)
            self.assertEqual(path.read_bytes(), original)
            self.assertNotIn('PRIVATE', json.dumps(report))
            # Credential renewal by Claude Code is picked up without restarting the hub.
            data = json.loads(path.read_text())
            data['claudeAiOauth']['accessToken'] = 'PRIVATE_RENEWED'
            path.write_text(json.dumps(data))
            network.return_value.open.return_value = io.BytesIO(json.dumps(self.response()).encode())
            read_claude_limits(path, now=1000)
            self.assertEqual(network.return_value.open.call_args.args[0].get_header('Authorization'), 'Bearer PRIVATE_RENEWED')

    def test_http_errors_are_sanitized_and_backoff_respects_retry_after(self):
        with tempfile.TemporaryDirectory() as directory, patch('usage.build_opener') as network:
            path = self.credentials(directory)
            for code, reason, delay in [(401, 'auth_required', 60), (403, 'auth_required', 60),
                                        (429, 'rate_limited', 900), (500, 'unavailable', 60)]:
                body = io.BytesIO(b'PRIVATE_UPSTREAM_BODY')
                network.return_value.open.side_effect = HTTPError('url', code, 'PRIVATE_ERROR', {'Retry-After': '900'}, body)
                with self.assertRaises(UsageUnavailable) as error:
                    read_claude_limits(path, now=1000)
                self.assertEqual(error.exception.reason, reason)
                self.assertEqual(error.exception.retry_after, delay)
                self.assertNotIn('PRIVATE', str(error.exception))
                self.assertTrue(body.closed)
            with self.assertRaises(UsageUnavailable):
                NoUsageRedirect().redirect_request(None, None, 302, '', {}, 'https://untrusted.example')

    def test_provider_caches_and_failure_states_are_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            self.credentials(directory)
            codex = UsageMonitor(directory, clock=lambda: 1000)
            claude = ClaudeUsageMonitor(directory, config_dir=directory, clock=lambda: 1000)
            with patch('usage.read_codex_limits', return_value=weekly_limits({'rateLimits': {'primary': window(61)}})):
                codex.refresh()
            with patch('usage.read_claude_limits', return_value=claude_limits(self.response())):
                claude.refresh()
            claude.clock = lambda: 1060
            with patch('usage.read_claude_limits', side_effect=UsageUnavailable('rate_limited', 900)):
                claude.refresh()
            self.assertEqual(claude.delay, 15)
            self.assertEqual(claude.next_api_read, 1960)
            self.assertTrue(claude.snapshot()['stale'])
            self.assertEqual(claude.snapshot()['reason'], 'rate_limited')
            self.assertFalse(codex.snapshot()['stale'])
            self.assertEqual(codex.snapshot()['weekly'][0]['used_percent'], 61)
            restored = ClaudeUsageMonitor(directory, config_dir=directory, clock=lambda: 1100)
            self.assertEqual(restored.snapshot()['limits'][0]['used_percent'], 26)
            self.assertEqual(restored.snapshot()['limits'][0]['window_minutes'], 300)
            self.assertEqual(restored.snapshot()['limits'][0]['id'], 'five_hour')
            self.assertTrue(restored.snapshot()['stale'])
            self.assertEqual((Path(directory) / 'claude-usage.json').stat().st_mode & 0o777, 0o600)
            self.assertNotIn('PRIVATE', (Path(directory) / 'claude-usage.json').read_text())
            claude.clock = lambda: 1960
            with patch('usage.read_claude_limits', return_value=[]):
                claude.refresh()
            self.assertEqual(claude.delay, 15)
            self.assertEqual(claude.snapshot()['status'], 'not_reported')
            self.assertNotIn('reason', claude.snapshot())

    def test_statusline_cache_keeps_its_age_and_only_exports_quota_fields(self):
        with tempfile.TemporaryDirectory() as directory, patch('usage.read_claude_limits') as network:
            credentials = self.credentials(directory)
            original = credentials.read_bytes()
            cache = Path(directory) / 'statusline.json'
            cache.write_text(json.dumps({'fetchedAtMs': 995_000, 'utilization': self.response()}))
            now = [1000]
            monitor = ClaudeUsageMonitor(directory, config_dir=directory, clock=lambda:now[0], cache_path=cache)
            monitor.refresh()
            self.assertEqual(monitor.snapshot()['observed_at'], 995)
            self.assertEqual(monitor.snapshot()['limits'][1]['used_percent'], 17)
            self.assertNotIn('PRIVATE', json.dumps(monitor.snapshot()))
            self.assertEqual((Path(directory)/'claude-usage.json').stat().st_mode & 0o777, 0o600)
            now[0] = 1100
            monitor.refresh()
            self.assertEqual(monitor.snapshot()['observed_at'], 995, 'Checking a file cannot make an old observation fresh')
            self.assertEqual(credentials.read_bytes(), original)
            network.assert_not_called()

    def test_local_report_recovers_during_api_backoff_without_another_request(self):
        with tempfile.TemporaryDirectory() as directory:
            self.credentials(directory)
            cache = Path(directory)/'statusline.json'
            now = [1000]
            monitor = ClaudeUsageMonitor(directory, config_dir=directory, clock=lambda:now[0], cache_path=cache)
            with patch('usage.read_claude_limits', side_effect=UsageUnavailable('rate_limited', 900)) as network:
                monitor.refresh()
                now[0] = 1015
                monitor.refresh()
                network.assert_called_once()
                cache.write_text(json.dumps({'fetchedAtMs': 1015_000, 'utilization': self.response()}))
                monitor.refresh()
                self.assertFalse(monitor.snapshot()['stale'])
                self.assertNotIn('reason', monitor.snapshot())
                now[0] = 1200
                monitor.refresh()
                self.assertTrue(monitor.snapshot()['stale'])
                network.assert_called_once()
                now[0] = 1900
                monitor.refresh()
                self.assertEqual(network.call_count, 2)

    def test_statusline_cache_rejects_old_future_malformed_and_other_login_data(self):
        with tempfile.TemporaryDirectory() as directory:
            credentials = self.credentials(directory)
            cache = Path(directory)/'statusline.json'
            for stamp in (True, None, '995000', float('nan'), 800_000, 1_061_000):
                with self.subTest(stamp=stamp):
                    cache.write_text(json.dumps({'fetchedAtMs':stamp,'utilization':self.response()}))
                    self.assertIsNone(read_claude_cached_limits(cache,credentials,1000))
            cache.write_text(json.dumps({'fetchedAtMs':995_000,'utilization':self.response()}))
            os.utime(credentials, (999,999))
            self.assertIsNone(read_claude_cached_limits(cache,credentials,1000))
            cache.write_text('{')
            self.assertIsNone(read_claude_cached_limits(cache,credentials,1000))
            cache.write_text(' ' * 1_000_001)
            self.assertIsNone(read_claude_cached_limits(cache,credentials,1000))
            monitor = ClaudeUsageMonitor(directory, config_dir=directory, clock=lambda:1000)
            self.assertIsNone(monitor.shared_cache, 'A custom profile must not borrow the default account cache')

    def test_changed_login_does_not_keep_the_previous_accounts_quota(self):
        with tempfile.TemporaryDirectory() as directory:
            credentials = self.credentials(directory)
            now = [1000]
            monitor = ClaudeUsageMonitor(directory, config_dir=directory, clock=lambda:now[0])
            with patch('usage.read_claude_limits', return_value=claude_limits(self.response())):
                monitor.refresh()
            os.utime(credentials,(1001,1001))
            now[0]=1060
            with patch('usage.read_claude_limits', side_effect=UsageUnavailable('auth_required')):
                monitor.refresh()
            self.assertEqual(monitor.snapshot()['limits'],[])
