"""Synthetic native session logs only; search never instructs a user agent."""
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import Mock, patch

from archive import Archive, ArchiveQueries, PAGE_CHARS, artifacts
from bridge import BridgeError


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.codex = self.root / 'sessions'; self.codex.mkdir()
        self.claude = self.root / 'projects'; self.claude.mkdir()
        self.project = self.root / 'work'; self.project.mkdir()
        self.index = Archive(self.root / 'index', roots=[('codex', self.codex), ('claude', self.claude)])
        self.native = str(uuid.uuid4())
        self.path = self.codex / ('rollout-2026-01-01T10-00-00-' + self.native + '.jsonl')
        self.records = [{'type': 'session_meta', 'payload': {'id': self.native, 'cwd': str(self.project)}}]

    def tearDown(self):
        self.index.close(); self.temp.cleanup()

    def message(self, text, index=1, role='assistant', **extra):
        return {'timestamp': '2026-01-01T10:%02d:%02dZ' % (index // 60, index % 60), 'type': 'response_item',
                'payload': {'type': 'message', 'role': role, 'content': [{'type': 'input_text' if role == 'user' else 'output_text', 'text': text}], **extra}}

    def write(self, records=None):
        self.path.write_text(''.join(json.dumps(row)+'\n' for row in (records or self.records)))

    def refresh(self):
        for index in range(100):
            if not self.index.refresh(discover=index == 0):
                return
        self.fail('Index did not finish')

    def result(self, query):
        return self.index.search(query)['items'][0]

    def test_indexes_visible_old_sessions_and_paths_without_reasoning(self):
        self.records += [self.message('Compare stdmean and quantile', 1, 'user'),
                         self.message('Private chain canary', 2, phase='analysis'),
                         self.message('Hidden thought canary', 3, channel='analysis'),
                         {'type': 'response_item', 'payload': {'type': 'reasoning', 'summary': 'Secret reasoning canary'}},
                         self.message('Quantile reduced error. [Trace](<motion trace.csv>)\n\n[Video](rollout.webm)', 4)]
        self.write(); self.refresh()
        self.assertEqual(self.index.status()['messages'], 2)
        self.assertEqual(self.index.search('canary')['items'], [])
        item = self.result('quantile error')
        page = self.index.conversation(item['session'], item['message_id'], generation=item['generation'])
        self.assertEqual(page['messages'][-1]['text'], self.records[-1]['payload']['content'][0]['text'])
        self.assertIn('motion trace.csv', item['paths'])
        self.assertTrue(self.index.search('rollout webm', files=True)['items'])
        self.assertEqual(self.index.search('stdmean', files=True)['items'], [])
        self.assertEqual(self.index.asset_agent(item['session'], item['message_id'], 'motion trace.csv'), {'cwd': str(self.project)})
        for path in ('../private.txt', 'other.txt', '/etc/passwd'):
            with self.assertRaises(BridgeError):
                self.index.asset_agent(item['session'], item['message_id'], path)

    def test_new_file_types_in_older_indexes_still_require_an_exact_visible_reference(self):
        self.records += [self.message('Saved results: [Metrics](metrics.tsv), events.jsonl and traces.ndjson')]
        self.write(); self.refresh(); item = self.result('saved results')
        with self.index.db() as db:
            db.execute('UPDATE archive_messages SET paths=?', ('[]',))
        for path in ('metrics.tsv', 'events.jsonl', 'traces.ndjson'):
            self.assertEqual(self.index.asset_agent(item['session'], item['message_id'], path), {'cwd': str(self.project)})
        for path in ('other.tsv', '../events.jsonl', 'private.ndjson'):
            with self.assertRaises(BridgeError):
                self.index.asset_agent(item['session'], item['message_id'], path)

    def test_live_tail_connects_to_full_history_by_exact_message_identity(self):
        self.records += [self.message('Earlier result', 1), self.message('Latest result', 2)]
        self.write(); self.refresh(); latest = self.result('latest result')
        tail = [{'id':'unindexed-command', 'role':'tool'}, {'id':latest['message_id'], 'role':'assistant'}]
        before = self.index.metrics['bytes_read']
        with self.index.db() as writer:
            writer.execute('BEGIN IMMEDIATE')
            source = self.index.history_source(str(self.path), tail)
        self.assertEqual(source, {'session':latest['session'], 'generation':latest['generation'], 'anchor':latest['message_id'], 'earlier':True})
        self.assertEqual(self.index.metrics['bytes_read'], before)
        page = self.index.conversation(source['session'], source['anchor'], 'before', source['generation'])
        self.assertEqual([m['text'] for m in page['messages']], ['Earlier result'])
        self.assertIsNone(self.index.history_source(str(self.path), [{'id':'not-indexed-yet','role':'assistant'}]))
        self.assertIsNone(self.index.history_source(str(self.path)+'other', tail))
        first = self.result('earlier result')
        self.assertFalse(self.index.history_source(str(self.path), [{'id':first['message_id'],'role':'assistant'}])['earlier'])

    def test_claude_history_crosses_working_directory_changes_without_losing_messages(self):
        path = self.claude / 'project' / (str(uuid.uuid4())+'.jsonl'); path.parent.mkdir()
        records = [{'type':'assistant','uuid':str(uuid.uuid4()),'cwd':str(cwd),'timestamp':f'2026-01-01T10:00:0{i}Z',
                    'message':{'role':'assistant','content':[{'type':'text','text':f'Result number {i}'}]}}
                   for i,cwd in enumerate((self.project,self.root,self.project))]
        path.write_text(''.join(json.dumps(row)+'\n' for row in records)); self.refresh()
        with patch.object(self.index.parser, 'claude_root', self.claude.resolve()):
            _, live = self.index.parser.read_claude(path, True)
        self.assertEqual(len(live),1)
        source = self.index.history_source(str(path), live)
        page = self.index.conversation(source['session'],source['anchor'],'before',source['generation'])
        self.assertEqual([m['text'] for m in page['messages']],['Result number 0','Result number 1'])
        self.assertEqual([m['cwd'] for m in page['messages']],[str(self.project),str(self.root)])

    def test_append_restart_partial_write_and_replacement(self):
        self.records += [self.message('Baseline evidence', 1)]
        self.write(); self.refresh(); before = self.index.metrics['bytes_read']; original = self.result('baseline')
        self.refresh(); self.assertEqual(self.index.metrics['bytes_read'], before)
        new = json.dumps(self.message('New quantile result', 2))
        with self.path.open('a') as file:file.write(new[:40])
        self.refresh(); self.assertEqual(self.index.search('quantile')['items'], [])
        with self.path.open('a') as file:file.write(new[40:]+'\n')
        self.refresh(); self.assertEqual(len(self.index.search('quantile')['items']), 1)
        restored = Archive(self.root / 'index', roots=[('codex', self.codex)])
        try:
            restored.refresh(discover=True)
            self.assertEqual(restored.metrics['bytes_read'], 0)
            self.assertEqual(restored.status()['messages'], 2)
        finally:restored.close()
        replacement = self.path.with_suffix('.new');replacement.write_text(json.dumps(self.records[0])+'\n'+json.dumps(self.message('Rewritten measurement', 3))+'\n');replacement.replace(self.path)
        self.refresh()
        self.assertEqual(self.index.search('baseline')['items'], [])
        with self.assertRaisesRegex(BridgeError, 'changed'):
            self.index.conversation(original['session'], original['message_id'], generation=original['generation'])

    def test_context_is_not_indexed_and_old_index_is_repaired_without_reindexing_logs(self):
        context = '<codex_internal_context source="goal">Internal canary /private/hidden.csv</codex_internal_context>'
        self.records += [self.message(context, 1, 'user'), self.message('Visible checkpoint request', 2, 'user'), self.message('Checkpoint is ready.', 3)]
        self.write(); self.refresh()
        self.assertEqual(self.index.search('canary')['items'], [])
        self.assertEqual(self.index.status()['messages'], 2)
        saved = self.result('ready')
        # Simulate the old cache, including enough context rows to span batches.
        with self.index.db() as db:
            db.execute('PRAGMA user_version=0')
            for index in range(140):
                db.execute('INSERT INTO archive_messages(session,ordinal,id,role,phase,text,created,sort_time,cwd,paths) VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (saved['session'],index+10,'context'+str(index),'user','',context,'',0,str(self.project),'["/private/hidden.csv"]'))
            db.execute('INSERT INTO archive_messages(session,ordinal,id,role,phase,text,created,sort_time,cwd,paths) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (saved['session'],200,'mixed','user','',context+'\nKeep [plot](plot.png).','',0,str(self.project),'[]'))
        self.assertTrue(self.index.search('canary')['items'])
        original = self.path.read_bytes()
        restored = Archive(self.root / 'index', roots=[('codex', self.codex)])
        try:
            self.assertEqual(restored.search('canary')['items'], [])
            self.assertEqual(restored.search('hidden csv', files=True)['items'], [])
            self.assertEqual(restored.status()['messages'], 3)
            plot = restored.search('plot')['items'][0]
            self.assertEqual(plot['message_id'], 'mixed')
            self.assertEqual(plot['paths'], ['plot.png'])
            page = restored.conversation(saved['session'], saved['message_id'], generation=saved['generation'])
            self.assertEqual([item['text'] for item in page['messages']], ['Visible checkpoint request','Checkpoint is ready.','Keep [plot](plot.png).'])
            restored.refresh(discover=True)
            self.assertEqual(restored.metrics['bytes_read'], 0)
            self.assertEqual(self.path.read_bytes(), original)
        finally:
            restored.close()

    def test_runtime_notice_cleanup_upgrades_existing_index_without_changing_reply_links(self):
        self.records += [self.message('Keep the plot.', 1, 'user'), self.message('Plot is ready.', 2)]
        self.write(); self.refresh()
        saved = self.result('ready')
        with self.index.db() as db:
            db.execute('PRAGMA user_version=1')
            # Claude was excluded from the original context migration.
            db.execute("UPDATE archive_sessions SET runtime='claude'")
            user = dict(db.execute("SELECT * FROM archive_messages WHERE role='user'").fetchone())
            db.execute('DELETE FROM archive_messages WHERE rowid=?', (user['rowid'],))
            user['text'] = '<system-reminder>Internal canary.</system-reminder>\nKeep the plot.'
            db.execute('INSERT INTO archive_messages(rowid,session,ordinal,id,role,phase,text,created,sort_time,cwd,paths) VALUES(?,?,?,?,?,?,?,?,?,?,?)', tuple(user.values()))
            db.execute('INSERT INTO archive_messages(session,ordinal,id,role,phase,text,created,sort_time,cwd,paths) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (saved['session'],20,'notice','user','','<turn_aborted>Internal canary.</turn_aborted>','',0,str(self.project),'[]'))
        restored = Archive(self.root/'index', roots=[('codex', self.codex)])
        try:
            self.assertEqual(restored.search('canary')['items'], [])
            page = restored.conversation(saved['session'], saved['message_id'], generation=saved['generation'])
            self.assertEqual([m['text'] for m in page['messages']], ['Keep the plot.', 'Plot is ready.'])
            self.assertEqual(page['messages'][0]['id'], user['id'])
            self.assertEqual(restored.metrics['bytes_read'], 0)
            with restored.db() as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2)
        finally:
            restored.close()

    def test_resumed_logs_same_native_id_and_duplicate_names_keep_all_evidence(self):
        self.records += [self.message('First checkpoint evidence')]; self.write()
        resumed = self.path.with_name(self.path.name.replace('10-00-00', '11-00-00'))
        resumed.write_text(json.dumps(self.records[0])+'\n'+json.dumps(self.message('Resumed quantile evidence', 2))+'\n')
        other = self.codex / 'another-day';other.mkdir()
        (other/self.path.name).write_text(json.dumps(self.records[0])+'\n'+json.dumps(self.message('Separate directory evidence', 3))+'\n')
        self.refresh(); results = self.index.search('evidence')['items']
        self.assertEqual(len(results), 3)
        self.assertEqual(len({item['session'] for item in results}), 3)
        self.assertEqual(self.index.status()['sessions'], self.index.status()['known_files'])
        before = self.index.metrics['bytes_read']; self.refresh()
        self.assertEqual(self.index.metrics['bytes_read'], before)

    def test_move_into_archived_directory_preserves_exact_links_without_reindex(self):
        self.records += [self.message('Saved checkpoint evidence')]; self.write(); self.refresh()
        found = self.result('checkpoint'); before = self.index.metrics['records']
        destination = self.root/'archived_sessions';destination.mkdir()
        self.index.roots.append(('codex', destination));self.path.rename(destination/self.path.name)
        self.refresh(); latest = self.result('checkpoint')
        self.assertEqual(latest['session'], found['session'])
        self.assertEqual(latest['generation'], found['generation'])
        self.assertEqual(self.index.metrics['records'], before)
        self.assertFalse(latest['missing'])
        self.assertEqual(self.index.conversation(found['session'], found['message_id'], generation=found['generation'])['messages'][0]['text'], 'Saved checkpoint evidence')

    def test_all_referenced_files_remain_accessible_and_old_agent_binding_is_removed(self):
        body='\n'.join('[Trace '+str(i)+'](trace-'+str(i)+'.csv)' for i in range(300))
        self.records += [self.message(body)];self.write();self.refresh();found=self.result('trace')
        self.assertEqual(len(artifacts(body)), 300)
        self.assertEqual(self.index.asset_agent(found['session'], found['message_id'], 'trace-299.csv')['cwd'], str(self.project))
        agent={'id':'local:pane','title':'Checkpoint review'}
        self.index.bind(str(self.path),agent);self.refresh();self.assertEqual(self.result('trace')['agent_id'], agent['id'])
        self.index.bind(str(self.codex/'rollout-new.jsonl'),agent)
        self.refresh()
        self.assertIsNone(self.result('trace')['agent_id'])

    def test_explicit_file_links_keep_spaces_escaped_punctuation_and_nested_parentheses(self):
        body='[A](<motion trace (2).csv>) [B](clip_(v2).webm) [C](nested_(first_(next)).png) [D](escaped_\\(v2\\).mp4)'
        self.assertTrue({'motion trace (2).csv','clip_(v2).webm','nested_(first_(next)).png','escaped_(v2).mp4'}.issubset(artifacts(body)))

    def test_full_history_paging_and_large_message_parts_do_not_skip_content(self):
        self.records += [self.message('Historical trial '+str(i), i, 'user' if i % 2 else 'assistant') for i in range(1, 81)]
        long = 'a' * (PAGE_CHARS + 1000) + ' quantile checkpoint target ' + 'b' * PAGE_CHARS
        self.records += [self.message(long, 81)]
        self.write(); self.refresh()
        result = self.result('checkpoint target')
        page = self.index.conversation(result['session'], result['message_id'], offset=result['offset'])
        target = next(m for m in page['messages'] if m['id'] == result['message_id'])
        self.assertIn('checkpoint target', target['text'])
        collected = ''; offset = 0
        while True:
            part = self.index.conversation(result['session'], result['message_id'], offset=offset)['messages'][-1]
            self.assertEqual(part['part']['start'], len(collected))
            collected += part['text']
            if part['part']['end'] == part['part']['total']:break
            offset = part['part']['end'] + 80
        self.assertEqual(collected, long)
        seen = {result['message_id']}; anchor = result['message_id']
        while True:
            page = self.index.conversation(result['session'], anchor, direction='before')
            seen.update(m['id'] for m in page['messages'])
            if not page['earlier']:break
            anchor = page['messages'][0]['id']
        self.assertEqual(len(seen), 81)

    def test_claude_text_only_and_message_specific_project_boundaries(self):
        folder = self.claude / 'project';folder.mkdir();path = folder / (str(uuid.uuid4())+'.jsonl')
        records = [{'type': 'system', 'sessionId': path.stem},
            {'type': 'user', 'uuid': 'user-one', 'cwd': str(self.project), 'message': {'role': 'user', 'content': 'Compare the controllers'}},
            {'type': 'assistant', 'uuid': 'answer-one', 'cwd': str(self.project), 'message': {'role': 'assistant', 'content': [
                {'type': 'thinking', 'thinking': 'hidden canary'}, {'type': 'text', 'text': 'First comparison trace.csv'}]}},
            {'type': 'assistant', 'uuid': 'answer-two', 'cwd': str(self.root), 'message': {'role': 'assistant', 'content': 'Other project plot.png'}},
            {'type': 'assistant', 'uuid': 'side', 'isSidechain': True, 'cwd': str(self.project), 'message': {'role': 'assistant', 'content': 'sidechain canary'}}]
        path.write_text(''.join(json.dumps(row)+'\n' for row in records));self.refresh()
        self.assertEqual(self.index.search('canary')['items'], [])
        first, second = self.result('first comparison'), self.result('other project')
        self.assertEqual(self.index.asset_agent(first['session'], first['message_id'], 'trace.csv')['cwd'], str(self.project))
        self.assertEqual(self.index.asset_agent(second['session'], second['message_id'], 'plot.png')['cwd'], str(self.root))

    def test_codex_project_changes_are_recorded_with_each_message(self):
        self.records += [self.message('First trace.csv', 1),
                         {'type':'turn_context','payload':{'cwd':str(self.root)}},
                         self.message('Second trace.csv', 2),
                         {'type':'turn_context','payload':{'cwd':None}},
                         self.message('Unknown project trace.csv', 3)]
        self.write();self.refresh()
        first,second,unknown=(self.result(word) for word in ('first','second','unknown'))
        self.assertEqual(self.index.asset_agent(first['session'],first['message_id'],'trace.csv')['cwd'],str(self.project))
        self.assertEqual(self.index.asset_agent(second['session'],second['message_id'],'trace.csv')['cwd'],str(self.root))
        with self.assertRaisesRegex(BridgeError,'directory'):self.index.asset_agent(unknown['session'],unknown['message_id'],'trace.csv')

    def test_source_removal_keeps_a_labeled_saved_copy_and_never_follows_symlinks(self):
        self.records += [self.message('Saved result', 1)];self.write();self.refresh();item=self.result('saved result')
        self.path.unlink();self.index.discover()
        self.assertTrue(self.index.conversation(item['session'], item['message_id'])['missing'])
        outside=self.root/'secret.jsonl';outside.write_text(json.dumps(self.records[0])+'\n'+json.dumps(self.message('Forbidden source', 2))+'\n')
        self.path.symlink_to(outside);self.refresh()
        self.assertEqual(self.index.search('forbidden')['items'], [])

    def test_query_cursor_is_literal_stable_and_cannot_inject_sql(self):
        self.records += [self.message('Quantile trial '+str(i), i) for i in range(1, 35)]
        self.write();self.refresh();first=self.index.search('quantile')
        self.assertEqual(len(first['items']), 21)
        last=first['items'][-1]
        second=self.index.search('quantile', before=[last['sort_time'],last['key']])
        self.assertEqual(len(second['items']), 13)
        self.assertFalse({m['key'] for m in first['items']} & {m['key'] for m in second['items']})
        self.assertEqual(self.index.search('" OR 1=1 --')['items'], [])
        for cursor in ([float('nan'),'x'],[1,5],['1','x']):
            with self.assertRaises(BridgeError):self.index.search('quantile',before=cursor)

    def test_oversized_tools_are_excluded_without_hiding_missing_visible_records(self):
        tool={'timestamp':'2026-01-01T10:00:00Z','type':'response_item','payload':{'type':'custom_tool_call_output','output':'x'*800}}
        sequenced={'timestamp':tool['timestamp'],'sequence':4509,'type':tool['type'],'payload':tool['payload']}
        self.records += [tool,sequenced,self.message('visible '*150,2),self.message('Recovery after oversized records',3)]
        self.write()
        with patch('archive.MAX_RECORD',512):self.refresh()
        self.assertEqual(self.index.status()['skipped'],1)
        self.assertEqual(self.index.status()['messages'],1)
        self.assertIn('Recovery',self.result('recovery')['excerpt'])

    def test_live_binding_never_waits_for_the_index_writer(self):
        self.records += [self.message('Project trace.csv')];self.write();self.refresh()
        with self.index.db() as db:
            db.execute('BEGIN IMMEDIATE')
            start=time.monotonic();self.index.bind(str(self.path),{'id':'local:pane','title':'Active window'})
            self.assertLess(time.monotonic()-start,.5)
        self.refresh();self.assertEqual(self.result('trace')['title'],'Active window')

    def test_research_manifest_is_incremental_and_rewrite_uses_new_generation(self):
        self.records += [self.message('Robot policy baseline comparison is still planned.', 1)]
        self.write(); self.refresh()
        first = self.index.research_sources()
        self.assertEqual(len(first['items']), 1)
        source = first['items'][0]
        self.assertEqual(self.index.research_sources(after=first['next'])['items'], [])
        self.records += [self.message('Robot policy evaluation reported lower tracking error.', 2)]
        self.write(); self.refresh()
        appended = self.index.research_sources(after=first['next'])['items'][0]
        self.assertGreater(appended['revision'], source['revision'])
        self.assertEqual(appended['generation'], source['generation'])
        replacement = self.path.with_suffix('.new')
        replacement.write_text(json.dumps(self.records[0]) + '\n' + json.dumps(self.message('Rewritten robot policy study.', 3)) + '\n')
        replacement.replace(self.path); self.refresh()
        current = self.index.research_sources(after=appended['revision'])['items'][0]
        self.assertNotEqual(current['generation'], source['generation'])
        with self.assertRaisesRegex(BridgeError, 'changed'):
            self.index.research_evidence(source['session'], source['generation'])

    def test_research_pages_exclude_progress_and_reasoning_and_bound_long_text(self):
        self.records += [self.message('User question is not evidence.', 1, role='user'),
                         self.message('Private reasoning canary.', 2, phase='analysis'),
                         self.message('Checking the robot policy now.', 3, phase='commentary')]
        self.records += [self.message('Robot policy evaluation result ' + str(i), i + 4, phase='final_answer') for i in range(20)]
        long = 'Robot policy evidence. ' * 1000
        self.records += [self.message(long, 30, phase='final_answer')]
        self.write(); self.refresh()
        source = self.index.research_sources()['items'][0]
        first = self.index.research_evidence(source['session'], source['generation'])
        self.assertEqual(len(first['items']), 16)
        self.assertTrue(first['more'])
        second = self.index.research_evidence(source['session'], source['generation'], after=first['next'])
        self.assertEqual(len(second['items']), 5)
        self.assertFalse(second['more'])
        self.assertEqual(len(second['items'][-1]['text']), 6000)
        self.assertEqual(second['items'][-1]['digest'], hashlib.sha256(long.encode()).hexdigest())
        self.assertTrue(second['items'][-1]['truncated'])
        self.assertNotIn('canary', json.dumps(first))
        self.assertNotIn('Checking', json.dumps(first))
        self.assertNotIn('User question', json.dumps(first))

    def test_research_queries_validate_remote_sources_and_keep_worker_local(self):
        self.records += [self.message('Robot policy evidence and a matched-seed baseline comparison.', 1)]
        self.write(); self.refresh()
        mesh = Mock(node_id='local', is_worker=False, config={'label': 'Laptop'}, peers={'gpu': {'role': 'worker', 'label': 'GPU'}})
        hub = Mock(mesh=mesh, archive=self.index)
        queries = ArchiveQueries(hub)
        manifest = self.index.research_sources()
        mesh.request.return_value = manifest
        remote = queries.research_sources('gpu')
        self.assertEqual(mesh.request.call_args.args[1], 'worker/archive/research_sources')
        self.assertEqual(remote['items'][0]['host'], 'gpu')
        source = remote['items'][0]
        evidence = self.index.research_evidence(source['session'], source['generation'])
        mesh.request.return_value = evidence
        self.assertEqual(queries.research_evidence('gpu', source['session'], source['generation'])['items'], evidence['items'])
        evidence['items'][0]['digest'] = '0' * 64
        with self.assertRaisesRegex(BridgeError, 'digest'):
            queries.research_evidence('gpu', source['session'], source['generation'])
        mesh.is_worker = True
        with self.assertRaisesRegex(BridgeError, 'Unknown'):
            queries.research_sources('gpu')

    def test_research_manifest_epoch_can_be_detected_past_the_new_journal_end(self):
        self.records += [self.message('Robot research evidence is ready.', 1)]
        self.write(); self.refresh()
        manifest = self.index.research_sources(after=10000)
        self.assertEqual(manifest['items'], [])
        self.assertEqual(manifest['next'], 10000)
        self.assertEqual(len(manifest['epoch']), 32)
        # A controller first reads the changed epoch, then restarts at zero.
        self.assertEqual(len(self.index.research_sources(after=0)['items']), 1)

    def test_research_project_identity_merges_only_matching_repository_origins(self):
        second = self.root / 'second-checkout'; second.mkdir()
        third = self.root / 'other' / self.project.name; third.mkdir(parents=True)
        for project in (self.project, second):
            (project / '.git').mkdir()
            (project / '.git' / 'config').write_text('[remote "origin"]\nurl = git@example.test:lab/robot-learning.git\n')
        self.records += [self.message('Robot policy baseline study in the first checkout.', 1),
                         {'type': 'turn_context', 'payload': {'cwd': str(second)}},
                         self.message('Robot policy evaluation in another checkout.', 2),
                         {'type': 'turn_context', 'payload': {'cwd': str(third)}},
                         self.message('Robot policy results from an unrelated project.', 3)]
        self.write(); self.refresh()
        source = self.index.research_sources()['items'][0]
        items = self.index.research_evidence(source['session'], source['generation'])['items']
        self.assertEqual(items[0]['project_id'], items[1]['project_id'])
        self.assertNotEqual(items[0]['project_id'], items[2]['project_id'])


if __name__ == '__main__':
    unittest.main()
