"""Real controller/worker HTTP reads against private synthetic logs only."""
import base64
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from unittest.mock import patch

import bridge
from federation import Mesh
from server import Hub, Handler, ThreadingHTTPServer


class ArchiveNodes:
    def __init__(self, handler=Handler):
        self.temp = tempfile.TemporaryDirectory(prefix='hub-archive-test-')
        self.root = Path(self.temp.name)
        self.servers = {key: ThreadingHTTPServer(('127.0.0.1', 0), handler) for key in ('controller', 'worker')}
        self.urls = {key: 'http://127.0.0.1:'+str(server.server_port) for key, server in self.servers.items()}
        self.hubs = {}
        with patch.object(Mesh, 'start'), patch.object(Hub, 'save_connection'):
            for key, server in self.servers.items():
                other = 'worker' if key == 'controller' else 'controller'
                config = {'node_id': key, 'label': key, 'role': key, 'tmux_socket':str(self.root/'unused.sock'), 'public_url': self.urls[key], 'supervisor_owner': 'controller',
                          'peers': [{'id':other, 'role':other, 'label':other, 'token':'isolated-archive-fixture-'*3,
                                     **({'url':self.urls[other]} if key == 'controller' else {})}]}
                hub = Hub(self.root/key, node_config=config)
                logs = self.root/key/'logs';logs.mkdir()
                hub.archive.roots = [('codex',logs)]
                server.hub, server.allowed_hosts = hub, {'127.0.0.1','localhost'}
                self.hubs[key] = hub
                threading.Thread(target=server.serve_forever, daemon=True).start()

    def seed(self, host, texts, stem=None, project=None):
        hub = self.hubs[host];project = project or self.root/host/'project';project.mkdir(exist_ok=True)
        identity = str(uuid.uuid4());path = hub.archive.roots[0][1]/('rollout-'+(stem or identity)+'.jsonl')
        records = [{'type':'session_meta','payload':{'id':identity,'cwd':str(project)}}]
        for i, text in enumerate(texts):
            records.append({'timestamp':'2026-01-01T10:00:00Z','type':'response_item',
                            'payload':{'type':'message','role':'assistant','phase':'final_answer','content':[{'type':'output_text','text':text}]}})
        path.write_text(''.join(json.dumps(record)+'\n' for record in records))
        while hub.archive.refresh(discover=True):pass
        return path, project

    def get(self, destination, endpoint, **params):
        with urlopen(self.urls[destination]+endpoint+'?'+urlencode(params), timeout=5) as response:
            return json.load(response)

    def close(self):
        for hub in self.hubs.values():hub.close()
        for server in self.servers.values():server.shutdown();server.server_close()
        self.temp.cleanup()


class ArchiveHTTPTests(unittest.TestCase):
    def setUp(self):
        self.nodes = ArchiveNodes();self.a,self.b = (self.nodes.hubs[key] for key in ('controller','worker'))
        self.nodes.seed('controller',['Quantile controller evidence '+str(i) for i in range(27)])
        self.path,self.project = self.nodes.seed('worker',['Quantile worker evidence '+str(i) for i in range(28)]+['Saved [trace](trace.csv) and [video](clip.webm).'])
        (self.project/'trace.csv').write_text('time,position\n0,42\n')
        (self.project/'clip.webm').write_bytes(b'0123456789')

    def tearDown(self):self.nodes.close()

    def test_live_preview_exposes_an_exact_local_history_cursor_that_the_controller_can_page(self):
        item=self.b.archive.search('trace csv')['items'][0]
        tail=self.b.archive.conversation(item['session'],item['message_id'])['messages'][-1:]
        agent={'id':'worker:pane','pane':'%1','runtime':'claude','live':True,'title':'Claude','cwd':str(self.project)}
        original={'source':'original','messages':tail,'session_verified':True,'archive_source':str(self.path)}
        with patch.object(bridge,'capture',return_value=''), patch.object(self.b.transcripts,'match',return_value=copy.deepcopy(original)), \
             patch.object(self.b,'agent_observation',return_value={'state':'done'}), \
             patch('server.terminal_queue.snapshot',return_value=None), patch.object(self.b.task_facts,'build',return_value={}):
            preview=self.b._local_preview({'kind':'local','id':'worker'},agent,history=True)
        source=preview['history_source'];self.assertEqual(source['host'],'worker')
        self.assertRegex(preview['conversation_id'], r'^[a-f0-9]{32}$')
        self.assertEqual(source['anchor'],tail[0]['id']);self.assertTrue(source['earlier'])
        self.assertNotIn('archive_source',preview)
        with patch.object(self.b.transcripts,'match',return_value=copy.deepcopy(original)), patch.object(self.b,'session_hints',return_value=[]), patch.object(self.b,'agent_observation',return_value={}), patch('server.bridge.capture',return_value=''), patch('server.terminal_queue.snapshot',return_value=None), patch.object(self.b.task_facts,'build',return_value={}), patch.object(self.b.archive,'history_source',return_value=None):
            pending=self.b._local_preview({'kind':'local','id':'worker'},agent,history=True)
        self.assertEqual(pending['conversation_id'],preview['conversation_id'])
        self.assertNotIn('history_source',pending)
        page=self.nodes.get('controller','/api/archive/conversation',host=source['host'],session=source['session'],message=source['anchor'],generation=source['generation'],direction='before')
        self.assertEqual(len(page['messages']),24)
        self.assertTrue(all(m['asset_agent_id'].startswith('worker:archive:') for m in page['messages']))

    def test_cross_host_cursor_reaches_every_match_without_duplicates_or_reverse_calls(self):
        seen=set();cursor=None
        with patch.object(self.b.mesh,'request',side_effect=AssertionError('Worker made a reverse request')), patch.object(bridge,'send') as send:
            while True:
                result=self.nodes.get('controller','/api/archive/search',q='quantile',**({'before':json.dumps(cursor)} if cursor else {}))
                self.assertEqual(result['unavailable'],[])
                keys={item['key'] for item in result['items']};self.assertFalse(keys&seen);seen.update(keys)
                cursor=result['next']
                if not cursor:break
            self.assertEqual(len(seen),55)
            local=self.nodes.get('worker','/api/archive/search',q='quantile')
            self.assertEqual({item['host'] for item in local['items']},{'worker'})
            send.assert_not_called()
        self.assertNotIn('Quantile controller evidence',json.dumps(self.a.mesh.export({})))
        self.assertNotIn('Quantile controller evidence',json.dumps(self.b.snapshot()))

    def test_exact_ended_message_files_and_ranges_stay_on_the_source_host(self):
        item=self.nodes.get('controller','/api/archive/search',q='trace csv',files=1)['items'][0]
        page=self.nodes.get('controller','/api/archive/conversation',host='worker',session=item['session'],message=item['message_id'],generation=item['generation'])
        message=next(m for m in page['messages'] if m['id']==item['message_id']);owner=message['asset_agent_id']
        self.assertEqual(owner,'worker:archive:'+item['session']+':'+item['message_id'])
        self.assertNotIn('fork',message);self.assertEqual(self.b.snapshot()['agents'],[])
        url=self.nodes.urls['controller']+'/api/file?'+urlencode({'agent_id':owner,'path':'trace.csv'})
        with urlopen(url,timeout=5) as response:
            self.assertEqual(response.read(),b'time,position\n0,42\n');self.assertIn('no-store',response.headers['Cache-Control'])
        media=self.nodes.urls['controller']+'/api/media?'+urlencode({'agent_id':owner,'path':'clip.webm'})
        with urlopen(Request(media,headers={'Range':'bytes=3-6'}),timeout=5) as response:
            self.assertEqual(response.status,206);self.assertEqual(response.read(),b'3456')
        (self.project/'unmentioned.csv').write_text('not referenced')
        with self.assertRaises(bridge.BridgeError):self.a.video(owner,'unmentioned.csv')
        with self.assertRaises(bridge.BridgeError):self.b.file_agent(owner.replace('worker:','controller:',1),'trace.csv')
        self.path.unlink();self.b.archive.discover()
        saved=self.nodes.get('controller','/api/archive/conversation',host='worker',session=item['session'],message=item['message_id'])
        self.assertTrue(saved['missing']);self.assertEqual(saved['messages'][-1]['text'],message['text'])
        with patch.object(bridge,'send') as send, self.assertRaises(bridge.BridgeError):self.a.action({'type':'send','agent_id':owner,'message':'not a live target'})
        send.assert_not_called()

    def test_host_and_peer_boundaries_reject_reverse_access_and_invalid_requests(self):
        headers={'X-Hub-Peer':'worker','Authorization':'Bearer '+self.a.mesh.peers['worker']['token']}
        for endpoint in ('archive/search','archive/conversation','worker/archive/search'):
            with self.assertRaises(HTTPError) as error:urlopen(Request(self.nodes.urls['controller']+'/peer/'+endpoint,b'{}',headers),timeout=3)
            self.assertEqual(error.exception.code,403);error.exception.close()
        for endpoint,params in [('/api/archive/search',{'q':'x','host':'controller'}),('/api/archive/conversation',{'host':'controller','session':'0'*32}),('/api/archive/search',{'q':'x','before':'[NaN,"x"]'}),('/api/archive/conversation',{'session':'../../private','offset':'bad'})]:
            with self.assertRaises(HTTPError) as error:self.nodes.get('worker',endpoint,**params)
            self.assertEqual(error.exception.code,409);error.exception.close()
        with self.assertRaises(HTTPError) as error:urlopen(Request(self.nodes.urls['controller']+'/api/archive/search?q=quantile',headers={'Host':'evil.invalid'}),timeout=3)
        self.assertEqual(error.exception.code,403);error.exception.close()
        with patch.object(self.a,'client_allowed',return_value=False),self.assertRaises(HTTPError) as error:self.nodes.get('controller','/api/archive/search',q='quantile')
        self.assertEqual(error.exception.code,403);error.exception.close()

    def test_peer_failure_reports_partial_results_and_cannot_spoof_the_source(self):
        with patch.object(self.a.mesh,'request',side_effect=bridge.BridgeError('Offline')):
            result=self.nodes.get('controller','/api/archive/search',q='quantile')
        self.assertEqual({m['host'] for m in result['items']},{'controller'});self.assertEqual(result['unavailable'][0]['host'],'worker')
        original=self.b.archive.search('quantile');original['status'].update(host='controller',label='spoofed')
        with patch.object(self.a.mesh,'request',return_value=original):
            result=self.nodes.get('controller','/api/archive/search',q='quantile',host='worker')
        self.assertEqual(result['hosts'][0]['host'],'worker');self.assertEqual(result['hosts'][0]['label'],'worker')
        item=original['items'][0];page=self.b.archive.conversation(item['session'],item['message_id'])
        page['session']='0'*32
        with patch.object(self.a.mesh,'request',return_value=page),self.assertRaises(bridge.BridgeError):
            self.a.archives.conversation('worker',{'key':item['session'],'anchor':item['message_id']})

if __name__=='__main__':unittest.main()
