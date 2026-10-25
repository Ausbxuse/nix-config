"""Exercise late network readiness without restarting hosts or user agents."""
import errno
from http.server import ThreadingHTTPServer
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.request import urlopen

from server import Handler, bind_listener


class StartupTests(unittest.TestCase):
    def test_waits_through_a_long_tailnet_delay_then_serves_without_restarting(self):
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.return_value = False
        calls = 0
        def start():
            nonlocal calls
            calls += 1
            if calls <= 60:
                raise OSError(errno.EADDRNOTAVAIL, 'Cannot assign requested address')
            return ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        with self.assertLogs(level='WARNING') as logs:
            listener = bind_listener(start, ('100.64.0.1', 8765), stop)
        self.assertEqual(calls, 61)
        self.assertEqual(stop.wait.call_count, 60)
        self.assertEqual(len(logs.output), 2)  # One waiting line, one ready line.
        listener.hub = SimpleNamespace(client_allowed=lambda address: True, snapshot=lambda: {'ok': True})
        listener.allowed_hosts = {'127.0.0.1'}
        worker = threading.Thread(target=listener.serve_forever, daemon=True)
        worker.start()
        try:
            with urlopen(f'http://127.0.0.1:{listener.server_port}/api/state', timeout=2) as response:
                self.assertEqual(json.load(response), {'ok': True})
        finally:
            listener.shutdown(); listener.server_close(); worker.join(2)

    def test_existing_address_starts_immediately(self):
        stop = threading.Event()
        listener = object()
        factory = Mock(return_value=listener)
        self.assertIs(bind_listener(factory, ('100.64.0.1', 8765), stop), listener)
        factory.assert_called_once_with()

    def test_port_conflicts_permissions_and_bad_tls_are_not_hidden_as_network_delays(self):
        for error in (OSError(errno.EADDRINUSE, 'Port in use'), PermissionError(errno.EACCES, 'Denied'), ValueError('Bad TLS key')):
            with self.subTest(error=error):
                factory, stop = Mock(side_effect=error), Mock()
                stop.is_set.return_value = False
                with self.assertRaises(type(error)):
                    bind_listener(factory, ('100.64.0.1', 8443), stop)
                factory.assert_called_once_with()
                stop.wait.assert_not_called()

    def test_stop_cancels_wait_without_opening_another_listener(self):
        stop = threading.Event()
        factory = Mock(side_effect=OSError(errno.EADDRNOTAVAIL, 'Not ready'))
        def cancel(_):
            stop.set()
        with self.assertLogs(level='WARNING'):
            with patch.object(stop, 'wait', side_effect=cancel), self.assertRaises(InterruptedError):
                bind_listener(factory, ('100.64.0.1', 8443), stop)
        factory.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
