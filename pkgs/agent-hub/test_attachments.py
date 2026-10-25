"""Uploads and delivery use synthetic hosts; no commands reach real agents."""
import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image

import bridge
import terminal_queue
from attachments import Attachments, MAX_BYTES
from native import NativeCodex, NativeError, NativeImageUnavailable
from server import Hub
from test_archive_http import ArchiveNodes
from test_native import BINDING, metadata
from test_terminal_queue import screen as queue_screen


def png():
    output = io.BytesIO()
    Image.new('RGBA', (32, 20), (22, 55, 90, 120)).save(output, 'PNG')
    return output.getvalue()


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        with patch.object(Hub, 'save_connection'):
            self.hub = Hub(self.temp.name)
        self.images = self.hub.attachments

    def tearDown(self):
        self.hub.close()
        self.temp.cleanup()

    def test_upload_strips_metadata_and_does_not_trust_names_or_file_extensions(self):
        photo = Image.new('RGBA', (32,20), (22,55,90,120))
        exif = Image.Exif()
        exif[0x010E] = 'Private image description'
        output = io.BytesIO()
        photo.save(output, 'PNG', exif=exif, icc_profile=b'private-profile')
        item = self.images.upload('local:agent', '/project', '../../private\x00.png', output.getvalue())
        self.assertEqual(item['name'], 'private.png')
        row = self.images.row(item['id'])
        path = self.images.path(item['id'], row['type'])
        self.assertEqual(path.parent, Path(self.temp.name)/'images')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with Image.open(path) as normalized:
            self.assertEqual(normalized.getpixel((0, 0)), (22, 55, 90, 120))
            self.assertNotIn('exif', normalized.info)
            self.assertNotIn('icc_profile', normalized.info)
        for data in (b'<svg onload="evil"/>', b'not an image', b'x'*(MAX_BYTES+1)):
            with self.assertRaises(bridge.BridgeError):
                self.images.upload('local:agent', '/project', 'image.png', data)
        with self.assertRaises(bridge.BridgeError):self.images.read('../hub.sqlite')

    def test_images_cannot_be_retargeted_and_history_reconstructs_the_original_caption(self):
        item = self.images.upload('local:agent', '/project', 'screen.png', png())
        for target, cwd in [('other', '/project'), ('local:agent', '/other')]:
            with self.assertRaises(bridge.BridgeError):self.images.prepare('Look', [item['id']], target, cwd)
        with self.assertRaises(bridge.BridgeError):self.images.prepare('Look', [item['id']]*2, 'local:agent', '/project')
        wire, paths = self.images.prepare('Look at this', [item['id']], 'local:agent', '/project')
        self.assertIn(paths[0], wire)
        messages = [{'role':'user','text':wire}, {'role':'assistant','text':wire}]
        restored = Attachments(self.hub).decorate(messages)
        self.assertEqual(restored[0]['text'], 'Look at this')
        self.assertEqual(restored[0]['attachments'], [item])
        self.assertNotIn('attachments', restored[1])
        result = self.hub.archive_images({'messages':[{'role':'user','text':wire,'part':{'start':0,'end':len(wire),'total':len(wire)}}]})
        self.assertEqual(result['messages'][0]['part']['total'], len('Look at this'))

    def test_codex_images_use_verified_thread_without_changing_models_permissions_or_interrupting(self):
        for queued, state, expected in [(False,'idle','turn/start'), (True,'active','thread/queue/add'), (True,'idle','turn/start')]:
            native = NativeCodex()
            def call(method, params):
                if method == 'thread/read':return {'thread':metadata({'type':state,'activeFlags':[]})}
                self.assertEqual(method, expected)
                self.assertEqual(set(params), {'threadId','clientUserMessageId','input'})
                self.assertEqual(params['input'][-1], {'type':'localImage','path':'/private/image.png'})
                return {}
            with patch.object(native, 'call', side_effect=call) as rpc:
                result = native.send_images(BINDING, 'Inspect it', ['/private/image.png'], 'web-image-request', queued)
                self.assertEqual(result, 'queued' if state == 'active' else 'sent')
                self.assertEqual(rpc.call_count, 2)
                self.assertEqual(native.timeout, .6)
        for modified in ({'id':'other'}, {'status':{'type':'notLoaded'}}, {'canAcceptDirectInput':False},
                         {'status':{'type':'active','activeFlags':['waitingOnApproval']}}, {'status':{'type':'active','activeFlags':[]}}):
            with patch.object(NativeCodex, 'call', return_value={'thread':{**metadata(),**modified}}) as rpc:
                with self.assertRaises(NativeError):NativeCodex().send_images(BINDING, 'Look', ['/private/image.png'], 'web-image-request')
                self.assertTrue(all(call.args[0]=='thread/read' for call in rpc.call_args_list))

    def test_clipped_image_queue_recovers_after_restart_without_changing_edit_authority(self):
        agent = dict(id='local:agent', cwd='/project', runtime='codex', live=True)
        image = self.images.upload(agent['id'], agent['cwd'], 'screen.png', png())
        caption = "Also, the queued's edit button is still taking a newline."
        wire, _ = self.images.prepare(caption, [image['id']], agent['id'], agent['cwd'])
        clipped = wire.rsplit('\n', 1)[0] + '\n…'
        item = terminal_queue.snapshot(agent, queue_screen([clipped]))['items'][0]
        identity, digest = item['id'], item['digest']
        # Existing installations are backfilled from durable upload ownership.
        with self.hub.db() as db:
            db.execute('DELETE FROM image_message_previews')
        restored = Attachments(self.hub).decorate([item], agent=agent)[0]
        self.assertEqual(restored['text'], caption)
        self.assertEqual(restored['attachments'], [image])
        self.assertEqual(restored['attachment_match'], 'preview')
        self.assertEqual((restored['id'], restored['digest']), (identity, digest))
        self.assertFalse(restored['editable'])
        # Clipping can change when the terminal is resized. Identity does not
        # depend on cached DOM, queue row IDs, or a particular line wrapping.
        wrapped = clipped.replace('taking a newline.', 'taking\na newline.').replace('…', '...')
        again = terminal_queue.snapshot(agent, queue_screen([wrapped]))['items'][0]
        self.assertEqual(self.images.decorate([again], agent=agent)[0]['attachments'], [image])

    def test_clipped_image_previews_require_unique_recipient_and_project_evidence(self):
        agent = dict(id='local:agent', cwd='/project', runtime='codex', live=True)
        first = self.images.upload(agent['id'], agent['cwd'], 'first.png', png())
        wire, paths = self.images.prepare('Compare this', [first['id']], agent['id'], agent['cwd'])
        clipped = wire.rsplit('\n', 1)[0] + '\n…'
        def item():
            return terminal_queue.snapshot(agent, queue_screen([clipped]))['items'][0]
        for scope in (None, {**agent, 'id':'another-pane'}, {**agent, 'cwd':'/other'}):
            self.assertNotIn('attachments', self.images.decorate([item()], agent=scope)[0])
        second = self.images.upload(agent['id'], agent['cwd'], 'second.png', png())
        self.images.prepare('Compare this', [second['id']], agent['id'], agent['cwd'])
        self.assertNotIn('attachments', self.images.decorate([item()], agent=agent)[0], 'Same caption can refer to different images')
        identified = terminal_queue.snapshot(agent, queue_screen([wire[:-4] + '…']))['items'][0]
        self.assertEqual(self.images.decorate([identified], agent=agent)[0]['attachments'], [first])
        # Historical prose is not a clipped live queue.
        self.assertNotIn('attachments', self.images.decorate([{'role':'user','text':clipped}], agent=agent)[0])

    def test_editing_an_image_message_preserves_images_in_a_separate_branch(self):
        agent = {'id':'local:branch','cwd':'/project','runtime':'codex','title':'Branch'}
        config = {'kind':'local','label':'Local','id':'local'}
        # A message inherited from an earlier branch keeps its image ownership.
        item = self.images.upload('local:parent', '/project', 'screen.png', png())
        wire, paths = self.images.prepare('Original caption', [item['id']], 'local:parent', '/project')
        message = {'id':'message','role':'user','text':wire,'fork':{'thread':'parent','turn':'first'}}
        self.images.decorate([message])
        branch = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        with patch.object(self.hub, 'lookup', return_value=(config,agent)), \
             patch.object(self.hub, 'local_preview', return_value={'messages':[message]}), \
             patch('server.fork_message', return_value=branch) as fork, \
             patch.object(bridge, 'run', side_effect=['%99','']) as run:
            self.hub.local_action({'type':'fork','agent_id':agent['id'],'message_id':'message','message':'New caption'})
        self.assertEqual(fork.call_args.args[0]['text'], wire)
        import shlex
        command = shlex.split(run.call_args_list[0].args[1][-1])
        self.assertEqual(command[:5], ['codex','resume',branch,'--image',paths[0]])
        self.assertEqual(command[-2], '--')
        self.assertIn('New caption', command[-1])
        self.assertEqual(self.images.decorate([{'role':'user','text':command[-1]}])[0]['attachments'], [item])

    def test_native_image_preflight_is_distinct_from_uncertain_delivery(self):
        native = NativeCodex()
        for binding, source in (({}, metadata()), (BINDING, {**metadata(), 'canAcceptDirectInput':False})):
            with patch.object(native, 'call', return_value={'thread':source}) as call:
                with self.assertRaises(NativeImageUnavailable):
                    native.send_images(binding, 'Look', ['/image.png'], 'image_preflight_1', queued=True)
                self.assertTrue(all(item.args[0]=='thread/read' for item in call.call_args_list))
        def fail_write(method, params):
            if method=='thread/read':return {'thread':metadata({'type':'active','activeFlags':[]})}
            raise NativeError('Write outcome uncertain')
        with patch.object(native, 'call', side_effect=fail_write):
            with self.assertRaises(NativeError) as error:
                native.send_images(BINDING, 'Look', ['/image.png'], 'image_uncertain_1', queued=True)
            self.assertNotIsInstance(error.exception, NativeImageUnavailable)

    def test_image_queue_falls_back_before_delivery_and_keeps_caption_edits(self):
        agent = {'id':'local:agent','cwd':'/project','runtime':'codex','live':True,'title':'Codex','pane':'%1','connection_id':'local'}
        config = {'kind':'local','label':'Local','id':'local'}
        image = self.images.upload(agent['id'], agent['cwd'], 'screen.png', png())
        command = {'type':'queue','agent_id':agent['id'],'message':'Inspect this screenshot','attachments':[image['id']],
                   'command_id':'image_queue_fallback_1'}
        with patch.object(self.hub, 'lookup', return_value=(config,agent)), \
             patch.object(bridge, 'verify_agent', return_value=agent), \
             patch.object(bridge, 'capture', return_value='screen') as capture, \
             patch.object(bridge, 'empty_codex_editor', return_value=True), \
             patch.object(self.hub, 'session_hints', return_value=[]), \
             patch.object(self.hub.transcripts, 'match', return_value={}), \
             patch.object(self.hub.recovery, 'manual'), \
             patch.object(bridge, 'queue', return_value='queued') as queue, \
             patch.object(bridge, 'send') as send:
            # Actual NativeCodex rejects the missing identity before any RPC.
            with patch.object(self.hub.native, 'call') as rpc:
                result = self.hub.action(command)
                self.assertEqual(result['delivery'], 'queued')
                self.assertEqual(self.hub.action(command), result)
                rpc.assert_not_called()
            queue.assert_called_once()
            wire = queue.call_args.args[-1]
            self.assertIn(str(self.images.path(image['id'], image['type'])), wire)
            capture.return_value = queue_screen([wire])
            item = terminal_queue.snapshot(agent, capture.return_value)['items'][0]
            self.images.decorate([item])
            self.assertEqual(item['text'], 'Inspect this screenshot')
            self.assertEqual(item['attachments'], [image])
            with patch.object(terminal_queue, 'change') as edit:
                self.hub.action({'type':'queue_update','queue_backend':'terminal','agent_id':agent['id'],
                                 'queue_id':item['id'],'queue_digest':item['digest'],'message':'Compare the corner',
                                 'command_id':'image_queue_caption_edit_1'})
            updated = {'role':'user','text':edit.call_args.args[-1]}
            self.images.decorate([updated])
            self.assertEqual(updated['text'], 'Compare the corner')
            self.assertEqual(updated['attachments'], [image])
            send.assert_not_called()
            # A write-stage failure must never trigger a second submission.
            queue.reset_mock()
            capture.return_value = 'screen'  # The earlier terminal queue has drained.
            with patch.object(self.hub.native, 'send_images', side_effect=NativeError('Write outcome uncertain')):
                with self.assertRaisesRegex(NativeError, 'uncertain'):
                    self.hub.action({**command, 'command_id':'image_queue_uncertain_1'})
            queue.assert_not_called()

    def test_native_caption_edit_keeps_upload_references_and_original_image_input(self):
        agent = dict(id='local:agent',cwd='/project',runtime='codex',live=True,title='Codex',pane='%1',connection_id='local')
        config = dict(kind='local', label='Local', id='local')
        image = self.images.upload(agent['id'], agent['cwd'], 'screen.png', png())
        wire, paths = self.images.prepare('Inspect this', [image['id']], agent['id'], agent['cwd'])
        original = dict(id='queue-image',clientUserMessageId='original-send',input=[
            {'type':'text','text':wire,'text_elements':[]}, {'type':'image','url':'data:image/png;base64,EXACT_BYTES'}])
        displayed = self.hub.native.queued_item(BINDING, original)
        writes = []
        def rpc(method, params):
            if method == 'thread/read':return {'thread':metadata({'type':'active','activeFlags':[]})}
            if method == 'thread/queue/list':return {'data':[original]}
            writes.append((method, params));return {}
        with patch.object(self.hub,'lookup',return_value=(config,agent)), patch.object(bridge,'verify_agent',return_value=agent), \
             patch.object(bridge,'capture',return_value='Fixture screen'), patch.object(self.hub,'session_hints',return_value=[]), \
             patch.object(self.hub.transcripts,'match',return_value={'binding':BINDING}), patch.object(self.hub.native,'call',side_effect=rpc):
            request = dict(type='queue_update', agent_id=agent['id'],thread_id=BINDING['thread_id'],queue_id=original['id'],
                           queue_digest=displayed['digest'],message='Compare the corners',command_id='native_image_caption_edit_123')
            self.assertEqual(self.hub.action(request), self.hub.action(request))
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][0], 'thread/queue/update')
        revised = writes[0][1]['input']
        self.assertEqual(revised[1:], original['input'][1:])
        self.assertIn(paths[0], revised[0]['text'])
        restored = self.images.decorate([{'text':revised[0]['text']}])[0]
        self.assertEqual(restored['text'], 'Compare the corners')
        self.assertEqual(restored['attachments'], [image])

    def test_claude_delivery_references_the_upload_and_refuses_a_terminal_draft(self):
        agent = {'id':'local:agent','cwd':'/project','runtime':'claude','live':True,'title':'Claude','pane':'%1','connection_id':'local'}
        config = {'kind':'local','label':'Local','id':'local'}
        item = self.images.upload(agent['id'], agent['cwd'], 'screen.png', png())
        with patch.object(self.hub, 'lookup', return_value=(config,agent)), patch.object(bridge,'verify_agent',return_value=agent), \
             patch.object(bridge,'capture',return_value='screen'), patch('claude_models.empty_editor',return_value=True), \
             patch.object(bridge,'send') as send, patch.object(self.hub.recovery,'manual'):
            self.hub.local_action({'type':'send','agent_id':agent['id'],'message':'Fix this','attachments':[item['id']],'command_id':'web-image-request'})
            self.assertIn(str(self.images.root), send.call_args.args[-1])
            self.assertIn('Fix this', send.call_args.args[-1])
            send.reset_mock()
            with patch('claude_models.empty_editor',return_value=False):
                with self.assertRaises(bridge.BridgeError):
                    self.hub.local_action({'type':'send','agent_id':agent['id'],'message':'Fix','attachments':[item['id']]})
            send.assert_not_called()

    def test_supervisor_receipt_binds_images_and_passes_them_to_its_job(self):
        image = self.images.upload('supervisor', '', 'screen.png', png())
        calls = []
        def supervise(job_id, *args):
            calls.append(self.hub.jobs[job_id])
        # Suppress only the new job's execution; no live provider is involved.
        with patch('server.threading.Thread') as thread:
            result = self.hub.start_supervisor('Inspect this', request_id='web-images-request', attachments=[image['id']])
            args = thread.call_args.kwargs['args']
            supervise(*args)
        self.assertTrue(calls[0]['image_paths'][0].endswith('.png'))
        self.assertEqual(self.hub.snapshot()['messages'][-1]['attachments'], [image])
        self.assertEqual(self.hub.start_supervisor('Inspect this', request_id='web-images-request', attachments=[image['id']]), result)
        with self.assertRaises(bridge.BridgeError):
            self.hub.start_supervisor('Inspect this', request_id='web-images-request')


class ImageHTTPTests(unittest.TestCase):
    def setUp(self):
        self.nodes = ArchiveNodes()
        self.a,self.b = (self.nodes.hubs[key] for key in ('controller','worker'))
        self.agent = {'id':'worker:agent','cwd':'/project','runtime':'claude','live':True}
        self.lookup = patch.object(self.b,'lookup',return_value=({'kind':'local'},self.agent))
        self.lookup.start()
        self.peer = patch.object(self.a.mesh,'peer_for',return_value='worker')
        self.peer.start()

    def tearDown(self):
        self.lookup.stop()
        self.peer.stop()
        self.nodes.close()

    def post(self, target, data, token=None, origin=None):
        headers = {'Content-Type':'image/png','X-Hub-Token':self.a.csrf if token is None else token}
        if origin:headers['Origin']=origin
        request = Request(self.nodes.urls['controller']+'/api/images?'+urlencode({'target':target,'name':'photo.png'}),data=data,headers=headers)
        with urlopen(request,timeout=10) as response:return json.load(response)

    def test_remote_upload_and_preview_remain_one_direction_and_recipient_local(self):
        with patch.object(self.b.mesh,'request',side_effect=AssertionError('Worker made a reverse request')):
            item = self.post(self.agent['id'], png())
            self.assertEqual(item['host'], 'worker')
            self.assertEqual(list(self.a.attachments.root.iterdir()), [])
            self.assertEqual(len(list(self.b.attachments.root.iterdir())), 1)
            url = self.nodes.urls['controller']+'/api/image?'+urlencode({'host':'worker','id':item['id']})
            with urlopen(url,timeout=10) as response:
                self.assertEqual(response.headers['Content-Type'], 'image/png')
                self.assertEqual(Image.open(io.BytesIO(response.read())).size, (32,20))
            with self.assertRaises(bridge.BridgeError):self.b.image('controller', item['id'])
            with self.assertRaises(bridge.BridgeError):
                self.b.peer_request('controller','worker/image/upload',{'target':'supervisor','data':base64.b64encode(png()).decode()})

    def test_controller_queues_worker_image_without_a_native_binding_or_reverse_request(self):
        agent = {**self.agent, 'runtime':'codex', 'title':'Worker task', 'pane':'%1', 'connection_id':'worker'}
        config = {'kind':'local', 'id':'worker', 'label':'Worker'}
        with patch.object(self.b, 'lookup', return_value=(config,agent)), \
             patch.object(bridge, 'verify_agent', return_value=agent), \
             patch.object(bridge, 'capture', return_value='screen'), \
             patch.object(bridge, 'empty_codex_editor', return_value=True), \
             patch.object(self.b, 'session_hints', return_value=[]), \
             patch.object(self.b.transcripts, 'match', return_value={}), \
             patch.object(self.b.recovery, 'manual'), \
             patch.object(self.b.mesh, 'request', side_effect=AssertionError('Worker made a reverse request')), \
             patch.object(bridge, 'queue', return_value='queued') as queue:
            image = self.post(agent['id'], png())
            action = {'type':'queue', 'agent_id':agent['id'], 'message':'Inspect this', 'attachments':[image['id']],
                      'command_id':'remote_image_queue_1'}
            for _ in range(2):
                request = Request(self.nodes.urls['controller']+'/api/action', json.dumps(action).encode(),
                                  {'Content-Type':'application/json','X-Hub-Token':self.a.csrf})
                with urlopen(request, timeout=10) as response:
                    self.assertEqual(json.load(response)['delivery'], 'queued')
            queue.assert_called_once()
            self.assertIn(str(self.b.attachments.root), queue.call_args.args[-1])
            self.assertEqual(list(self.a.attachments.root.iterdir()), [])

    def test_csrf_origin_and_malformed_uploads_are_rejected(self):
        for kwargs, code in [({'token':'wrong'},403), ({'origin':'https://other.example'},403)]:
            with self.assertRaises(HTTPError) as error:self.post(self.agent['id'], png(), **kwargs)
            self.assertEqual(error.exception.code, code)
            error.exception.close()
        with self.assertRaises(HTTPError) as error:self.post(self.agent['id'], b'<script>bad</script>')
        error.exception.close()
        self.assertEqual(list(self.b.attachments.root.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
