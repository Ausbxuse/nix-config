"""Native approval replay in an owned runtime; all model traffic stays localhost."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest

from approvals import Approvals
from native import NativeError


class Owner:
    def __init__(self,path):
        import websocket
        transport=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);transport.settimeout(5);transport.connect(str(path))
        self.ws=websocket.create_connection('ws://localhost/',socket=transport,timeout=5,suppress_origin=True)
        self.counter=0;self.events=[]
        self.rpc('initialize',{'clientInfo':{'name':'hub_fixture_owner','version':'1'},'capabilities':{'experimentalApi':True}})
        self.ws.send(json.dumps({'method':'initialized'}))

    def rpc(self,method,params):
        self.counter+=1;identity='owner'+str(self.counter)
        self.ws.send(json.dumps({'id':identity,'method':method,'params':params}))
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            self.ws.settimeout(deadline-time.monotonic());value=json.loads(self.ws.recv())
            if value.get('id')==identity and 'method' not in value:
                if 'error' in value:raise RuntimeError(value['error'])
                return value['result']
            self.events.append(value)
        raise TimeoutError(method)

    def waiting(self,identity):
        for _ in range(100):
            value=self.rpc('thread/read',{'threadId':identity,'includeTurns':False})['thread']
            if 'waitingOnApproval' in (value['status'].get('activeFlags') or []):return value
            time.sleep(.025)
        raise AssertionError('Fixture never requested approval')

    def close(self):self.ws.close(timeout=.1)


def exercise(binary,kind,external=False):
    release=threading.Event();calls=[]
    with tempfile.TemporaryDirectory(prefix='hub-approval-runtime-') as directory:
        root=Path(directory);path=root/'runtime.sock'
        class Provider(BaseHTTPRequestHandler):
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))));calls.append(body)
                if len(calls)>1:
                    release.wait(20)
                    try:self.send_error(503,'Fixture finished')
                    except OSError:pass
                    return
                if kind=='command':
                    tool={'type':'function_call','id':'fc_fixture','call_id':'fixture_call','name':'exec_command','namespace':'functions',
                          'arguments':json.dumps({'cmd':'printf HUB_APPROVAL_FIXTURE','sandbox_permissions':'require_escalated','justification':'Isolated fixture approval'})}
                    field='arguments';delta='response.function_call_arguments.delta'
                else:
                    tool={'type':'custom_tool_call','id':'fc_fixture','call_id':'fixture_call','name':'apply_patch','namespace':'functions',
                          'input':'*** Begin Patch\n*** Add File: '+str(root/'result.txt')+'\n+HUB_APPROVAL_FIXTURE\n*** End Patch'}
                    field='input';delta='response.custom_tool_call_input.delta'
                events=[{'type':'response.created','response':{'id':'fixture_response','status':'in_progress','output':[]}},
                        {'type':'response.output_item.added','output_index':0,'item':{**tool,field:''}},
                        {'type':delta,'item_id':'fc_fixture','output_index':0,'delta':tool[field]},
                        {'type':'response.output_item.done','output_index':0,'item':tool},
                        {'type':'response.completed','response':{'id':'fixture_response','status':'completed','output':[tool],
                            'usage':{'input_tokens':1,'output_tokens':1,'total_tokens':2}}}]
                data=''.join('event: '+event['type']+'\ndata: '+json.dumps(event)+'\n\n' for event in events).encode()
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(data)))
                self.end_headers();self.wfile.write(data)
            def log_message(self,*args):pass
        provider=ThreadingHTTPServer(('127.0.0.1',0),Provider)
        threading.Thread(target=provider.serve_forever,daemon=True).start()
        try:
            (root/'config.toml').write_text(f'model_provider = "fixture"\nmodel = "gpt-6.1-sol"\nsandbox_mode = "read-only"\napproval_policy = "on-request"\n'
                f'[model_providers.fixture]\nname = "fixture"\nbase_url = "http://127.0.0.1:{provider.server_port}/v1"\nwire_api = "responses"\nrequires_openai_auth = false\n'
                '[analytics]\nenabled = false\n')
            env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','LANG','LC_ALL','SSL_CERT_FILE')};env['CODEX_HOME']=directory
            with (root/'runtime.log').open('w+') as log:
                process=subprocess.Popen([binary,'app-server','--listen','unix://'+str(path)],cwd=root,env=env,
                                         stdin=subprocess.DEVNULL,stdout=log,stderr=log)
                owner=None;adapter=Approvals(path)
                try:
                    for _ in range(100):
                        if path.exists():break
                        if process.poll() is not None:raise RuntimeError('Fixture runtime stopped')
                        time.sleep(.025)
                    owner=Owner(path)
                    started=owner.rpc('thread/start',{'cwd':directory,'model':'gpt-6.1-sol','modelProvider':'fixture',
                        'approvalPolicy':'on-request','sandbox':'read-only','ephemeral':False})
                    identity=started['thread']['id']
                    owner.rpc('turn/start',{'threadId':identity,'input':[{'type':'text','text':'Isolated fixture request.','text_elements':[]}]})
                    thread=owner.waiting(identity);binding={'thread_id':identity,'path':thread['path'],'cwd':directory}
                    value=adapter.read(binding);assert len(value['items'])==1,value
                    row=value['items'][0];assert row['kind']==kind and 'accept' in row['decisions'],row
                    if kind=='files':
                        assert row['changes'][0]['path']==str(root/'result.txt')
                        assert 'HUB_APPROVAL_FIXTURE' in row['changes'][0]['diff']
                    # A reconnect replays the same pending request with a new UI id.
                    adapter.forget(identity);new=adapter.read(binding)['items'][0];assert new['id']!=row['id']
                    try:adapter.answer(binding,row['id'],row['digest'],'accept')
                    except NativeError:pass
                    else:raise AssertionError('Disconnected request was accepted')
                    row=new
                    resumed=owner.rpc('thread/resume',{'threadId':identity,'path':thread['path'],'excludeTurns':True})
                    assert all(resumed.get(k)==started.get(k) for k in ('model','modelProvider','approvalPolicy','approvalsReviewer','sandbox','cwd'))
                    if external:
                        pending=next(e for e in reversed(owner.events) if str(e.get('method','')).endswith('/requestApproval'))
                        owner.ws.send(json.dumps({'id':pending['id'],'result':{'decision':'cancel'}}))
                        for _ in range(50):
                            if owner.rpc('thread/read',{'threadId':identity,'includeTurns':False})['thread']['status']['type']!='active':break
                            time.sleep(.025)
                        try:adapter.answer(binding,row['id'],row['digest'],'accept')
                        except NativeError:pass
                        else:raise AssertionError('Resolved request accepted')
                    else:
                        assert adapter.answer(binding,row['id'],row['digest'],'accept')['ok']
                    assert owner.rpc('thread/read',{'threadId':identity,'includeTurns':False})['thread']['id']==identity
                    if kind=='files':
                        for _ in range(50):
                            if (root/'result.txt').exists():break
                            time.sleep(.025)
                        assert (root/'result.txt').read_text()=='HUB_APPROVAL_FIXTURE\n'
                    return {'kind':kind,'external_resolution':external,'owner_connected':True,'configuration_preserved':True}
                finally:
                    adapter.close()
                    if owner:owner.close()
                    process.terminate()
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
        finally:
            release.set();provider.shutdown();provider.server_close()


@unittest.skipUnless(os.environ.get('AGENT_HUB_TEST_CODEX'),'Set AGENT_HUB_TEST_CODEX for isolated native approvals')
class ApprovalRuntimeTests(unittest.TestCase):
    def test_native_command_file_replay_and_external_resolution(self):
        for kind,external in [('command',False),('files',False),('command',True)]:
            with self.subTest(kind=kind,external=external):
                result=exercise(os.environ['AGENT_HUB_TEST_CODEX'],kind,external)
                self.assertTrue(result['owner_connected']);self.assertTrue(result['configuration_preserved'])


if __name__=='__main__':unittest.main()
