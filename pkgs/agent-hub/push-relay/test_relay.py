import base64
from contextlib import contextmanager
import json
import socket
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from pywebpush import WebPushException
from server import Relay, validate_subscription, notification_text, NoRedirectSession, PushHTTPSConnection, PushHTTPSPool


def subscription(suffix='one'):
    key=ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {'endpoint':'https://fcm.googleapis.com/fcm/send/'+suffix, 'keys':{'auth':base64.urlsafe_b64encode(b'a'*16).decode().rstrip('='), 'p256dh':base64.urlsafe_b64encode(key).decode().rstrip('=')}}


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.relay=Relay(self.directory.name, {'origin':'https://example.com','hub_url':'http://100.64.0.1:8765','publisher_token':'secret'*10,'contact':'mailto:test@example.com'})
    def tearDown(self): self.directory.cleanup()
    def enroll(self, name='one'):
        return self.relay.enroll({'ticket':self.relay.pair()['ticket'],'subscription':subscription(name)})

    def test_pair_is_one_use_and_rejects_expired(self):
        value={'ticket':self.relay.pair()['ticket'],'subscription':subscription()}
        device=self.relay.enroll(value)
        with self.assertRaises(ValueError): self.relay.enroll(value)
        self.assertTrue(self.relay.device(device))
        with self.relay.db() as db: db.execute('UPDATE tickets SET expires=0')
        bad={'ticket':self.relay.pair()['ticket'],'subscription':subscription('two')}
        with self.relay.db() as db: db.execute('UPDATE tickets SET expires=0')
        with self.assertRaises(ValueError): self.relay.enroll(bad)

    def test_invalid_endpoint_cannot_turn_relay_into_ssrf(self):
        for endpoint in ['http://127.0.0.1/admin','https://100.64.0.8','https://fcm.googleapis.com.attacker.test/a','https://fcm.googleapis.com@127.0.0.1/a','https://fcm.googleapis.com:8443/a','https://web.push.apple.com.evil/a']:
            value=subscription(); value['endpoint']=endpoint
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError): validate_subscription(value)

    def test_failed_delivery_retries_only_missing_device(self):
        self.enroll('one'); self.enroll('two')
        ok=Mock(status_code=201)
        failed=WebPushException('unavailable', response=Mock(status_code=503))
        with patch('server.webpush',side_effect=[ok,failed,ok]) as send:
            with self.assertRaises(ConnectionError): self.relay.publish({'id':'event','count':2})
            self.relay.publish({'id':'event','count':2})
            self.assertEqual(send.call_count,3)
            payload=json.loads(send.call_args.kwargs['data'])
            self.assertEqual(payload['title'],'Agent Hub')
            self.assertEqual(send.call_args.kwargs['ttl'],3600)
            self.assertEqual(send.call_args.kwargs['headers'], {'Urgency':'high','Topic':'agent-updates'})
            self.assertIn('?updates=1',payload['url'])
            self.relay.publish({'id':'event','count':2})
            self.assertEqual(send.call_count,3)

    def test_revoke_requires_browser_capability_and_does_not_affect_others(self):
        one=self.enroll('one'); two=self.enroll('two')
        with self.assertRaises(ValueError): self.relay.remove({'device':two['device'],'management':one['management']})
        self.relay.remove(one)
        with self.assertRaises(ValueError): self.relay.device(one)
        self.assertTrue(self.relay.device(two))

    def test_expired_subscription_removed_and_vapid_key_survives_restart(self):
        device=self.enroll()
        with patch('server.webpush',side_effect=WebPushException('gone',response=Mock(status_code=410))):
            self.relay.publish({'id':'event'})
        with self.assertRaises(ValueError): self.relay.device(device)
        second=Relay(self.directory.name,self.relay.config)
        self.assertEqual(second.public_key,self.relay.public_key)

    def test_redirects_disabled(self):
        from server import NoRedirectSession
        with patch('requests.Session.request',return_value=Mock()) as request:
            NoRedirectSession().get('https://fcm.googleapis.com/',allow_redirects=True)
            self.assertFalse(request.call_args.kwargs['allow_redirects'])

    def test_ipv4_preference_avoids_dead_ipv6_without_changing_other_sessions(self):
        from urllib3.poolmanager import PoolManager
        from urllib3.connectionpool import HTTPSConnectionPool
        provider = 'updates.push.services.mozilla.com'
        addresses = [(socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('2001:db8::1',443,0,0)),
                     (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.1',443))]
        six, four = Mock(), Mock()
        six.bind.side_effect = socket.gaierror('IPv4 source cannot bind an IPv6 socket')
        six.connect.side_effect = TimeoutError('unreachable IPv6')
        with NoRedirectSession() as session, patch('socket.getaddrinfo',return_value=addresses), \
                patch('socket.socket',side_effect=[six,four]):
            pool = session.get_adapter('https://').poolmanager.connection_from_url('https://'+provider)
            self.assertIsInstance(pool,PushHTTPSPool)
            connection = pool._new_conn()
            self.assertIs(connection._new_conn(),four)
            six.connect.assert_not_called()
            six.close.assert_called_once()
            four.connect.assert_called_once_with(('192.0.2.1',443))
            self.assertEqual(connection.host,provider)  # TLS still verifies the provider hostname.
            self.assertIsNone(connection.source_address)
            self.assertTrue(session.verify)
        self.assertIs(PoolManager().pool_classes_by_scheme['https'],HTTPSConnectionPool)

    def test_ipv6_only_provider_remains_reachable(self):
        addresses=[(socket.AF_INET6,socket.SOCK_STREAM,6,'',('2001:db8::1',443,0,0))]
        preferred, fallback=Mock(),Mock()
        preferred.bind.side_effect=socket.gaierror('No IPv4 address')
        with patch('socket.getaddrinfo',return_value=addresses), patch('socket.socket',side_effect=[preferred,fallback]):
            connection=PushHTTPSConnection('updates.push.services.mozilla.com',timeout=2)
            self.assertIs(connection._new_conn(),fallback)
            preferred.close.assert_called_once()
            fallback.connect.assert_called_once_with(('2001:db8::1',443,0,0))
            self.assertIsNone(connection.source_address)

    def test_explicit_source_bind_is_preserved(self):
        connection=PushHTTPSConnection('example.com',source_address=('127.0.0.1',0))
        with patch('urllib3.connection.HTTPSConnection._new_conn',return_value=Mock()) as connect:
            self.assertIs(connection._new_conn(),connect.return_value)
            connect.assert_called_once()
        self.assertEqual(connection.source_address,('127.0.0.1',0))

    def test_status_requires_publisher_authority_and_reveals_only_counts_and_time(self):
        import http.client
        import threading
        from http.server import ThreadingHTTPServer
        from server import Handler
        self.enroll()
        with patch('server.webpush',return_value=Mock(status_code=201)):
            self.relay.publish({'id':'sent'})
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.relay=self.relay
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            for authorization,expected in [('',403),('Bearer wrong',403),('Bearer '+self.relay.config['publisher_token'],200)]:
                connection=http.client.HTTPConnection(*server.server_address)
                connection.request('POST','/agent-alerts/status',body='{}',headers={'Content-Type':'application/json','Authorization':authorization})
                response=connection.getresponse();result=json.loads(response.read())
                self.assertEqual(response.status,expected)
                if expected==200:
                    self.assertEqual(set(result),{'subscribers','last_accepted_at'})
                    self.assertEqual(result['subscribers'],1)
                    self.assertGreater(result['last_accepted_at'],0)
                connection.close()
        finally:server.shutdown();server.server_close();thread.join()

    def test_gone_device_does_not_report_push_as_enabled(self):
        self.enroll()
        with patch('server.webpush',side_effect=WebPushException('gone',response=Mock(status_code=410))):
            result=self.relay.publish({'id':'gone'})
        self.assertEqual(result['subscribers'],0)

    def test_exact_notice_link_contains_no_agent_content_or_arbitrary_url(self):
        from urllib.parse import parse_qs,urlparse
        self.enroll()
        with patch('server.webpush',return_value=Mock(status_code=201)) as send:
            self.relay.publish({'id':'batch','count':1,'agent_id':'gpu:agent','notice_id':'notice_123','body':'PRIVATE','url':'https://attacker.test'})
            payload=json.loads(send.call_args.kwargs['data'])
            self.assertNotIn('PRIVATE',str(payload));self.assertNotIn('attacker',str(payload))
            self.assertEqual(parse_qs(urlparse(payload['url']).query)['notice'],['notice_123'])
            with self.assertRaises(ValueError):self.relay.publish({'id':'bad','notice_id':'x&redirect=evil'})

    def test_agent_and_host_reach_push_preview_with_exact_notice_link(self):
        from urllib.parse import parse_qs,urlparse
        self.enroll()
        with patch('server.webpush',return_value=Mock(status_code=201)) as send:
            self.relay.publish({'id':'named','count':1,'agent_id':'gpu:agent','notice_id':'notice_123',
                                'agents':[{'name':'NeMo checkpoints','host':'gpu-004','body':'PRIVATE'}], 'agent_count':1,
                                'title':'PRIVATE','body':'PRIVATE','url':'https://attacker.test'})
            payload=json.loads(send.call_args.kwargs['data'])
            self.assertEqual(payload['title'],'NeMo checkpoints · gpu-004')
            self.assertEqual(payload['body'],'New update. Tap to open.')
            self.assertNotIn('PRIVATE',str(payload));self.assertNotIn('attacker',str(payload))
            query=parse_qs(urlparse(payload['url']).query)
            self.assertEqual(query['agent'],['gpu:agent'])
            self.assertEqual(query['notice'],['notice_123'])
            self.assertEqual(payload['id'],'named')

    def test_grouped_preview_lists_hosts_and_remaining_agents(self):
        title,body=notification_text({'agents':[
            {'name':'NeMo','host':'razy'}, {'name':'NeMo','host':'gpu-004'}, {'name':'Motion trace','host':'gpu-004'}],
            'agent_count':5},6)
        self.assertEqual(title,'Agent Hub · 6 updates')
        self.assertEqual(body,'NeMo · razy\nNeMo · gpu-004\nMotion trace · gpu-004\n+2 more agents')
        title,body=notification_text({'agents':[{'name':'NeMo','host':'razy'}]*2},2)
        self.assertEqual(body,'NeMo · razy')

    def test_push_labels_are_plain_bounded_text_and_fit_unicode_payload(self):
        self.enroll()
        with patch('server.webpush',return_value=Mock(status_code=201)) as send:
            self.relay.publish({'id':'unicode','count':3,'agents':[
                {'name':str(i)+'🧪'*300,'host':'🧪'*300} for i in range(3)]})
            data=send.call_args.kwargs['data']
            self.assertLess(len(data.encode()),3500)
            self.assertIn('🧪',data)
            self.assertIn('…',data)
        title,_=notification_text({'agents':[{'name':'Task\n  one\x00\u202e','host':'gpu\t004'}]},1)
        self.assertEqual(title,'Task one · gpu 004')

    def test_invalid_agent_metadata_is_rejected_before_delivery(self):
        self.enroll()
        with patch('server.webpush') as send:
            for fields in [{'agents':'task'},{'agents':[None]},{'agents':[{'name':['Task']}]},
                           {'agents':[{'name':'Task','host':None}]},{'agents':[{}]*101},
                           {'agents':[{'name':'Task'}],'agent_count':0}, {'agent_count':True}]:
                with self.subTest(fields=fields),self.assertRaises(ValueError):
                    self.relay.publish({'id':'invalid',**fields})
            send.assert_not_called()
