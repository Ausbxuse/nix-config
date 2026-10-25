import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch

from native import NativeCodex, NativeError

THREAD = '01234567-1234-1234-1234-0123456789ab'
BINDING = {'thread_id':THREAD, 'cwd':'/project', 'path':'/project/session.jsonl'}


def metadata(status=None):
    return {'id':THREAD, 'cwd':'/project', 'path':'/project/session.jsonl', 'ephemeral':False,
            'canAcceptDirectInput':True, 'status':status or {'type':'idle'}}


class NativeTests(unittest.TestCase):
    def test_model_change_is_thread_scoped_and_validated_against_runtime_catalog(self):
        thread = {**metadata({'type':'active','activeFlags':[]}), 'model':'sol', 'reasoningEffort':'low'}
        calls = []
        catalog = [{'model':'sol','displayName':'Sol','defaultReasoningEffort':'low',
                    'supportedReasoningEfforts':[{'reasoningEffort':'low'},{'reasoningEffort':'ultra'}]},
                   {'model':'luna','displayName':'Luna','defaultReasoningEffort':'medium',
                    'supportedReasoningEfforts':[{'reasoningEffort':'medium'},{'reasoningEffort':'max'}]}]
        def call(method, params):
            calls.append((method,params))
            if method == 'thread/read':return {'thread':thread.copy()}
            if method == 'model/list':return {'data':catalog,'nextCursor':None}
            if method == 'thread/settings/update':
                self.assertEqual(set(params), {'threadId','model','effort'})
                thread.update(model=params['model'],reasoningEffort=params['effort'])
                return {}
            self.fail('Unexpected runtime call: '+method)
        native = NativeCodex()
        with patch.object(native,'call',side_effect=call):
            before = native.model_settings(BINDING)
            self.assertEqual(before['effort'],'low')
            for model, effort in [('missing','low'),('luna','ultra'),('luna',None)]:
                with self.assertRaisesRegex(NativeError,'supported'):
                    native.change_model(BINDING,before['version'],model,effort)
            result = native.change_model(BINDING,before['version'],'luna','max')
            self.assertEqual((result['model'],result['effort']),('luna','max'))
            self.assertEqual(thread['status']['type'],'active')
            self.assertEqual(sum(method=='thread/settings/update' for method,_ in calls),1)
            self.assertEqual(sum(method=='model/list' for method,_ in calls),1)
            with self.assertRaisesRegex(NativeError,'changed'):
                native.change_model(BINDING,before['version'],'sol','low')
            self.assertEqual(sum(method=='thread/settings/update' for method,_ in calls),1)
            self.assertTrue(all(method in ('thread/read','model/list','thread/settings/update') for method,_ in calls))

    def test_model_write_is_not_retried_or_claimed_successful_after_disconnect(self):
        native = NativeCodex()
        thread = {**metadata(),'model':'sol','reasoningEffort':'low'}
        catalog = [{'model':'sol','supportedReasoningEfforts':[{'reasoningEffort':'low'},{'reasoningEffort':'high'}]}]
        def call(method,params):
            if method=='thread/read':return {'thread':thread}
            if method=='model/list':return {'data':catalog}
            raise NativeError('Disconnected')
        with patch.object(native,'call',side_effect=call) as rpc:
            current = native.model_settings(BINDING)
            with self.assertRaisesRegex(NativeError,'Disconnected'):
                native.change_model(BINDING,current['version'],'sol','high')
            self.assertEqual(sum(c.args[0]=='thread/settings/update' for c in rpc.call_args_list),1)

    def test_unloaded_and_unverified_sessions_cannot_change_models(self):
        for modified in ({'id':'another'}, {'status':{'type':'notLoaded'}}, {'canAcceptDirectInput':False}, {'ephemeral':True}):
            with patch.object(NativeCodex,'call',return_value={'thread':{**metadata(),'model':'sol',**modified}}) as rpc:
                with self.assertRaises(NativeError):NativeCodex().model_settings(BINDING)
                self.assertTrue(all(call.args[0]=='thread/read' for call in rpc.call_args_list))

    def test_thread_requires_exact_identity_directory_and_log_path(self):
        for patch_value in ({'id':'other'}, {'cwd':'/elsewhere'}, {'path':'/other.jsonl'}, {'path':None}):
            with self.subTest(value=patch_value), patch.object(NativeCodex, 'call', return_value={'thread':{**metadata(),**patch_value}}):
                with self.assertRaises(NativeError):NativeCodex().thread(BINDING)
        with patch.object(NativeCodex,'call') as call:
            with self.assertRaises(NativeError):NativeCodex().thread({**BINDING,'thread_id':'not-a-thread'})
            call.assert_not_called()

    def test_active_waiting_and_errors_are_structured_but_idle_is_not_completion(self):
        terminal={'state':'done','summary':'Turn ended','activity_confirmed':False,'capacity_error':True}
        for status,state,active in [({'type':'active','activeFlags':[]},'running',True),
                ({'type':'active','activeFlags':['newUnknownFlag']},'running',True),
                ({'type':'active','activeFlags':['waitingOnApproval','newUnknownFlag']},'running',True),
                ({'type':'active'},'running',True),
                ({'type':'active','activeFlags':['waitingOnApproval']},'waiting',False),
                ({'type':'active','activeFlags':['waitingOnUserInput']},'waiting',False),
                ({'type':'systemError'},'blocked',False)]:
            with self.subTest(status=status), patch.object(NativeCodex,'call',return_value={'thread':metadata(status)}):
                result=NativeCodex().observe(BINDING,terminal)
                self.assertEqual(result['state'],state);self.assertEqual(result['activity_confirmed'],active)
                if status['type']=='active':self.assertFalse(result['capacity_error'])
        for state in ('idle','running','done','blocked','waiting'):
            with patch.object(NativeCodex,'call',return_value={'thread':metadata()}):
                result=NativeCodex().observe(BINDING,{**terminal,'state':state})
                self.assertEqual(result['state'],state)
        for status in ({'type':'notLoaded'},):
            with patch.object(NativeCodex,'call',return_value={'thread':metadata(status)}):
                self.assertEqual(NativeCodex().observe(BINDING,terminal),terminal)
        with patch.object(NativeCodex,'call',side_effect=NativeError('disconnected')):
            self.assertEqual(NativeCodex().observe(BINDING,terminal),terminal)
            fresh = NativeCodex().observe(BINDING, terminal, fresh=True)
            self.assertEqual(fresh['state'], 'unknown')
            self.assertFalse(fresh['completion_confirmed'])

    def test_notification_observation_bypasses_recent_idle_runtime_status(self):
        terminal = dict(state='done', summary='Completed', completion_confirmed=True, activity_confirmed=False)
        native = NativeCodex()
        with patch.object(native, 'call', side_effect=[{'thread':metadata()}, {'thread':metadata({'type':'active','activeFlags':[]})}]) as rpc:
            self.assertEqual(native.observe(BINDING, terminal)['state'], 'done')
            self.assertEqual(native.observe(BINDING, terminal)['state'], 'done')
            current = native.observe(BINDING, terminal, fresh=True)
            self.assertEqual(current['state'], 'running')
            self.assertFalse(current['completion_confirmed'])
            self.assertEqual(rpc.call_count, 2)

    def test_system_error_preserves_a_confirmed_terminal_capacity_failure(self):
        terminal = {'state':'blocked', 'summary':'Selected model is at capacity',
                    'activity_confirmed':False, 'capacity_error':True}
        with patch.object(NativeCodex, 'call', return_value={'thread':metadata({'type':'systemError'})}):
            result = NativeCodex().observe(BINDING, terminal)
            self.assertTrue(result['capacity_error'])
            self.assertEqual(result['summary'], terminal['summary'])
            self.assertEqual(result['runtime_status']['state'], 'systemError')
            self.assertEqual(result['state'], 'blocked')
            self.assertFalse(result['activity_confirmed'])
            ordinary = NativeCodex().observe(BINDING, {**terminal, 'capacity_error':False})
            self.assertFalse(ordinary['capacity_error'])
            self.assertEqual(ordinary['summary'], 'Agent runtime error')

    def test_queue_edit_cancel_checks_the_displayed_input_and_never_starts_work(self):
        item={'id':'queued-1','clientUserMessageId':'user-1','input':[{'type':'text','text':'Compare the seeds','text_elements':[]}]}
        calls=[];current=[item]
        def call(method,params):
            calls.append((method,params))
            if method=='thread/read':return {'thread':metadata({'type':'active','activeFlags':[]})}
            if method=='thread/queue/list':return {'data':current}
            return {}
        native=NativeCodex()
        with patch.object(native,'call',side_effect=call):
            queue=native.queue(BINDING);row=queue['items'][0]
            self.assertTrue(row['editable'])
            native.change_queue(BINDING,row['id'],row['digest'],'Use the same evaluation seeds.\nKeep both traces.')
            self.assertEqual(calls[-1][0],'thread/queue/update')
            self.assertEqual(calls[-1][1]['input'][0]['text'],'Use the same evaluation seeds.\nKeep both traces.')
            native.change_queue(BINDING,row['id'],row['digest'])
            self.assertEqual(calls[-1][0],'thread/queue/delete')
            calls.clear();current[0]={**item,'input':[{'type':'text','text':'Changed in terminal'}]}
            with self.assertRaisesRegex(NativeError,'changed'):native.change_queue(BINDING,row['id'],row['digest'],'old edit')
            current.clear()
            with self.assertRaisesRegex(NativeError,'left the queue'):native.change_queue(BINDING,row['id'],row['digest'])
            self.assertFalse(any(method in ('thread/queue/update','thread/queue/delete') for method,_ in calls))
            self.assertFalse(any(method in ('thread/resume','thread/start','turn/start','thread/queue/start') for method,_ in calls))

    def test_image_caption_edits_preserve_exact_images_but_mentions_are_not_flattened(self):
        item={'id':'queued-image','clientUserMessageId':'user-image','input':[{'type':'text','text':'Inspect this plot'},
              {'type':'localImage','path':'/project/plot.png'}]}
        def call(method,params):return {'thread':metadata()} if method=='thread/read' else {'data':[item]}
        native=NativeCodex()
        with patch.object(native,'call',side_effect=call) as rpc:
            row=native.queue(BINDING)['items'][0]
            self.assertTrue(row['editable']);self.assertTrue(row['has_attachments'])
            native.change_queue(BINDING,row['id'],row['digest'],'New text')
            self.assertEqual(rpc.call_args.args, ('thread/queue/update', {
                'threadId':THREAD, 'queuedSubmissionId':'queued-image',
                'input':[{'type':'text','text':'New text','text_elements':[]},item['input'][1]]}))
            item['input'].append({'type':'mention','name':'file','path':'/project/file'})
            row = native.queue(BINDING, fresh=True)['items'][0]
            self.assertFalse(row['editable'])
            with self.assertRaisesRegex(NativeError,'mentions'):native.change_queue(BINDING,row['id'],row['digest'],'No mention')
        native.cache.clear()
        with patch.object(native,'call',return_value={'thread':{**metadata(),'ephemeral':True}}), self.assertRaises(NativeError):
            native.queue(BINDING)


class WebSocketFixture:
    """A tiny isolated server exercises the actual WebSocket transport."""
    def __init__(self,path):
        self.path=path;self.calls=[];self.stop=threading.Event();self.clients=[]
        self.socket=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.socket.bind(str(path));path.chmod(0o600)
        self.socket.listen();self.socket.settimeout(.1)
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
    def run(self):
        while not self.stop.is_set():
            try:client,_=self.socket.accept()
            except socket.timeout:continue
            except OSError:return
            self.clients.append(client)
            threading.Thread(target=self.serve,args=(client,),daemon=True).start()
    def serve(self,client):
        def read(n):
            data=b''
            while len(data)<n:
                part=client.recv(n-len(data))
                if not part:raise EOFError()
                data+=part
            return data
        try:
            headers=b''
            while b'\r\n\r\n' not in headers:headers+=read(1)
            key=next(line.split(b':',1)[1].strip() for line in headers.split(b'\r\n') if line.lower().startswith(b'sec-websocket-key:'))
            accept=base64.b64encode(hashlib.sha1(key+b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest())
            client.sendall(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: '+accept+b'\r\n\r\n')
            while not self.stop.is_set():
                first,second=read(2)
                if first&15==8:return
                length=second&127
                if length==126:length=struct.unpack('!H',read(2))[0]
                if length==127:length=struct.unpack('!Q',read(8))[0]
                mask=read(4);payload=read(length)
                value=json.loads(bytes(b^mask[i%4] for i,b in enumerate(payload)))
                self.calls.append(value)
                if 'id' not in value:continue
                result={'thread':metadata()} if value['method']=='thread/read' else {'data':[]} if value['method']=='thread/queue/list' else {}
                reply=json.dumps({'id':value['id'],'result':result}).encode()
                client.sendall(b'\x81'+(bytes([len(reply)]) if len(reply)<126 else b'\x7e'+struct.pack('!H',len(reply)))+reply)
        except (EOFError,OSError,ValueError):pass
        finally:client.close()
    def close(self):
        self.stop.set();self.socket.close()
        for client in self.clients:
            try:client.shutdown(socket.SHUT_RDWR)
            except OSError:pass
        self.thread.join(1)


class NativeTransportTests(unittest.TestCase):
    def test_private_socket_alias_handshake_read_only_methods_and_permissions(self):
        try:import websocket
        except ImportError:self.skipTest('Packaged websocket-client is required for transport integration')
        with tempfile.TemporaryDirectory(prefix='hub-ws-') as directory:
            root=Path(directory);server=WebSocketFixture(root/'real.sock');alias=root/'alias.sock';alias.symlink_to(server.path)
            native=NativeCodex(alias)
            try:
                self.assertTrue(native.available())
                self.assertEqual(native.thread(BINDING)['status'],{'type':'idle'})
                self.assertEqual(native.queue(BINDING)['items'],[])
                self.assertEqual([c['method'] for c in server.calls],['initialize','initialized','thread/read','thread/queue/list'])
                with self.assertRaises(NativeError):native.call('thread/resume',{'threadId':THREAD})
                server.path.chmod(0o666)
                self.assertFalse(native.available())
                with self.assertRaises(NativeError):native.thread(BINDING,fresh=True)
            finally:native.close();server.close()


if __name__=='__main__':unittest.main()
