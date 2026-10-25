"""Delivery recovery uses fixtures and never sends to an existing agent."""
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

from bridge import BridgeError
from delivery import Deliveries, supervisor_request
from server import Hub


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        with patch.object(Hub, 'save_connection'):
            self.hub = Hub(self.temp.name)
        self.action = dict(type='send', agent_id='fixture:closed-agent', message='Check the result', command_id='web-fixture-request-1')

    def tearDown(self):
        self.hub.close()
        self.temp.cleanup()

    def test_completed_send_survives_restart_and_read_never_executes(self):
        with patch.object(self.hub, 'local_action', return_value={'ok': True, 'message': 'Delivered'}) as execute:
            result = self.hub.action(self.action)
            self.hub.deliveries = Deliveries(self.hub)
            self.assertEqual(self.hub.delivery_receipt(dict(kind='agent', request=self.action)), {'status': 'complete', 'result': result})
            self.assertEqual(self.hub.action(self.action), result)
            execute.assert_called_once()
            with self.assertRaisesRegex(BridgeError, 'different contents'):
                self.hub.delivery_receipt(dict(kind='agent', request={**self.action, 'message': 'Different instruction'}))

    def test_inflight_interrupted_and_missing_have_distinct_outcomes(self):
        store = self.hub.deliveries
        self.assertEqual(store.read(self.action['command_id'], self.action)['status'], 'unknown')
        started, finish = threading.Event(), threading.Event()
        def operation():
            started.set()
            finish.wait(2)
            return {'ok': True}
        thread = threading.Thread(target=lambda: store.execute_once(self.action['command_id'], self.action, operation))
        thread.start()
        try:
            self.assertTrue(started.wait(2))
            self.assertEqual(store.read(self.action['command_id'], self.action)['status'], 'sending')
            restarted = Deliveries(self.hub)
            self.assertEqual(restarted.read(self.action['command_id'], self.action)['status'], 'uncertain')
            with self.assertRaisesRegex(BridgeError, 'uncertain or failed'):
                restarted.execute_once(self.action['command_id'], self.action, lambda: self.fail('Duplicate send'))
        finally:
            finish.set()
            thread.join(2)
        self.assertEqual(store.read(self.action['command_id'], self.action)['status'], 'complete')

    def test_interrupted_execution_is_not_repeated(self):
        with patch.object(self.hub, 'local_action', side_effect=RuntimeError('Connection lost after writing')) as execute:
            with self.assertRaises(RuntimeError):
                self.hub.action(self.action)
            self.assertEqual(self.hub.delivery_receipt(dict(kind='agent', request=self.action))['status'], 'uncertain')
            with self.assertRaises(BridgeError):
                self.hub.action(self.action)
            execute.assert_called_once()

    def test_supervisor_acceptance_is_durable_and_deduplicated(self):
        request = supervisor_request('Which result is ready?', request_id='web-fixture-supervisor-1')
        with patch.object(self.hub, 'supervise') as supervise:
            first = self.hub.start_supervisor(request['message'], request_id=request['request_id'])
            # The first job is still marked busy; its exact retry returns its receipt.
            second = self.hub.start_supervisor(request['message'], request_id=request['request_id'])
            self.assertEqual(first, second)
            self.hub.deliveries = Deliveries(self.hub)
            self.assertEqual(self.hub.delivery_receipt(dict(kind='supervisor', request=request)), {'status': 'complete', 'result': first})
            self.assertEqual(len(self.hub.snapshot()['messages']), 1)
            self.assertEqual(supervise.call_count, 1)
            with self.assertRaisesRegex(BridgeError, 'different contents'):
                self.hub.start_supervisor('Changed instruction', request_id=request['request_id'])

    def test_receipt_inputs_are_bounded_to_known_delivery_types(self):
        for value in [{}, {'kind': 'agent', 'request': {}}, {'kind': 'agent', 'request': {**self.action, 'type': 'launch'}},
                      {'kind': 'agent', 'request': {**self.action, 'command_id': 'invalid!'}}]:
            with self.subTest(value=value), self.assertRaises(BridgeError):
                self.hub.delivery_receipt(value)


if __name__ == '__main__':
    unittest.main()
