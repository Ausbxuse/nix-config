"""Exact request evidence and explicit decisions; no installed agent commands."""
import copy
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from approvals import ApprovalClient, Approvals, present
from native import NativeError
from server import Hub
from test_native import BINDING, metadata


def request(method='commandExecution', **params):
    return {'id':17, 'method':f'item/{method}/requestApproval', 'params':{
        'threadId':BINDING['thread_id'], 'turnId':'turn1', 'itemId':'item1',
        **({'command':'printf fixture', 'cwd':BINDING['cwd']} if method=='commandExecution' else {}), **params}}


class ApprovalTests(unittest.TestCase):
    def client(self, item):
        client=ApprovalClient();client.ws=Mock();client.binding=copy.deepcopy(BINDING)
        client.thread=Mock(return_value={**metadata(), 'status':{'type':'active','activeFlags':['waitingOnApproval']}})
        client.drain=Mock(return_value=True);client.event(item)
        self.addCleanup(client.close)
        return client

    def test_only_complete_evidence_can_offer_allow_once(self):
        self.assertEqual(present(request())['scope'],'once')
        for item in [request(command=None),request(command='x'*16001),request(kind='unknown'),
                     request(reason={}),request(availableDecisions=['acceptForSession','cancel'])]:
            self.assertNotIn('accept',present(item)['decisions'])
        self.assertEqual(present(request(networkApprovalContext={'host':'fixture.invalid','protocol':'https'},command=''))['kind'],'network')
        valid=[{'path':'/fixture/result','diff':'+fixture\n','kind':{'type':'add'}}]
        self.assertIn('accept',present(request('fileChange'),valid)['decisions'])
        for change in (None,[{'path':'a','diff':'x','kind':None}]):
            self.assertNotIn('accept',present(request('fileChange'),change)['decisions'])
        self.assertNotIn('accept',present(request('fileChange',grantRoot='/fixture'),valid)['decisions'])

    def test_request_digest_stale_identity_and_decisions_checked_before_sending(self):
        client=self.client(request());row=client.sync(BINDING)[0]
        for identity,expected,decision in [(row['id'],'wrong','accept'),('wrong',row['digest'],'accept'),
                                           (row['id'],row['digest'],'acceptForSession')]:
            with self.assertRaises(NativeError):client.answer(BINDING,identity,expected,decision)
        client.ws.send.assert_not_called()
        client.event(request(command='printf changed'))
        with self.assertRaises(NativeError):client.answer(BINDING,row['id'],row['digest'],'accept')
        client.ws.send.assert_not_called()
        row=client.sync(BINDING)[0];transport=client.ws
        client.answer(BINDING,row['id'],row['digest'],'accept')
        self.assertEqual(json.loads(transport.send.call_args.args[0]),{'id':17,'result':{'decision':'accept'}})

    def test_permissions_are_exact_and_limited_to_this_turn(self):
        permissions={'network':{'enabled':True},'fileSystem':{'read':['/fixture/input']}}
        for decision in ('accept','decline'):
            client=self.client(request('permissions',permissions=permissions))
            row=client.sync(BINDING)[0];transport=client.ws
            self.assertEqual(row['scope'],'turn');self.assertNotIn('cancel',row['decisions'])
            client.answer(BINDING,row['id'],row['digest'],decision)
            self.assertEqual(json.loads(transport.send.call_args.args[0])['result'],
                             {'permissions':permissions if decision=='accept' else {},'scope':'turn'})

    def test_resolved_foreign_and_disconnected_requests_cannot_be_answered(self):
        client=self.client(request());row=client.sync(BINDING)[0]
        client.event(request(threadId='different'))
        self.assertEqual(len(client.pending),1)
        client.event({'method':'serverRequest/resolved','params':{'threadId':BINDING['thread_id'],'requestId':17}})
        with self.assertRaises(NativeError):client.answer(BINDING,row['id'],row['digest'],'accept')
        client.event(request());first=client.sync(BINDING)[0]['id'];client.close()
        client.binding=copy.deepcopy(BINDING);client.event(request())
        self.assertNotEqual(first,client.sync(BINDING)[0]['id'])

    def test_owner_resolution_and_missing_confirmation_never_retry(self):
        client=self.client(request());row=client.sync(BINDING)[0];transport=client.ws
        client.drain=Mock(side_effect=[False,False])
        with self.assertRaisesRegex(NativeError,'Decision sent'):client.answer(BINDING,row['id'],row['digest'],'accept')
        self.assertEqual(transport.send.call_count,1)
        self.assertIsNone(client.ws)
        manager=Approvals()
        with self.assertRaises(NativeError):manager.answer(BINDING,row['id'],row['digest'],'accept')

    def test_endpoint_reverifies_pane_and_deduplicates_decision(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(Hub,'save_connection'):
            hub=Hub(directory);self.addCleanup(hub.close)
            agent={'id':'local:fixture','runtime':'codex','live':True,'cwd':BINDING['cwd'],'pane':'%9'}
            connection={'kind':'local','id':'local','label':'Fixture'}
            action={'type':'approval_respond','agent_id':agent['id'],'thread_id':BINDING['thread_id'],
                    'approval_id':'a'*32,'approval_digest':'b'*64,'decision':'accept','command_id':'approval_fixture_123'}
            with patch.object(hub,'lookup',return_value=(connection,agent)),patch('bridge.verify_agent',return_value=agent),\
                 patch('bridge.capture',return_value='fixture'),patch.object(hub,'session_hints',return_value=[]),\
                 patch.object(hub.transcripts,'match',return_value={'binding':BINDING}) as match,patch.object(hub.approvals,'answer') as answer:
                hub.action(action);hub.action(action);self.assertEqual(answer.call_count,1)
                match.return_value={'binding':{**BINDING,'thread_id':'different'}}
                with self.assertRaises(NativeError.__bases__[0]):hub.action({**action,'command_id':'approval_fixture_124'})
                self.assertEqual(answer.call_count,1)


if __name__=='__main__':unittest.main()
