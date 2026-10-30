import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from transcripts import Transcripts, claude_display
import transcripts


class TranscriptTests(unittest.TestCase):
    def test_a_draft_in_the_input_box_is_not_a_message(self):
        rows = transcripts.visible_dialogue('• Done with the build, all tests pass and the APK is published for phones.\n\n› half typed thought about the\n\n  GPT-6 max · ~/src · weekly 90% left\n')
        self.assertEqual('assistant', transcripts.without_draft(rows)[-1][0])
        sent_screen = '• Done with the build.\n\n› please also update the docs\n\n• Working (3s • esc to interrupt)\n'
        sent = transcripts.visible_dialogue(sent_screen)
        self.assertEqual(sent, transcripts.without_draft(sent, transcripts.editor_draft(sent_screen)))
        typing = '• Done with the build, all tests pass.\n\n› half typed thought\n\n  GPT-6 max · ~/src · weekly 90% left\n'
        self.assertEqual('halftypedthought', transcripts.editor_draft(typing))

    def test_idle_pane_binds_by_its_whole_last_reply(self):
        reply = 'Done on both hosts. Automatic reply summaries and recap cards are off. Original replies stay intact.'
        messages = [{'role': 'user', 'text': 'only summarize when I ask'}, {'role': 'assistant', 'text': reply}]
        idle = '› only summarize when I ask\n\n• Ran ls -la\n  └ total 0\n\n• ' + reply + '\n\n› Ask Codex to do anything\n'
        self.assertIsNotNone(transcripts.last_reply_evidence(messages, transcripts.visible_dialogue(idle), '/tmp'))
        # After an unrelated reply, or too short to tell, it proves nothing.
        between = '› only summarize when I ask\n\n• Something else entirely, said in between.\n\n• ' + reply + '\n'
        self.assertIsNone(transcripts.last_reply_evidence(messages, transcripts.visible_dialogue(between), '/tmp'))
        short = [{'role': 'user', 'text': 'go'}, {'role': 'assistant', 'text': 'Done.'}]
        self.assertIsNone(transcripts.last_reply_evidence(short, transcripts.visible_dialogue('› go\n\n• Done.\n'), '/tmp'))

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.day = self.root / datetime.date.today().strftime('%Y/%m/%d')
        self.day.mkdir(parents=True)
        self.reader = Transcripts(self.root)
        self.agent = {'runtime':'codex','cwd':'/project'}
        self.body = 'This visible response describes a detailed algorithm with enough unique text to establish its identity in this particular terminal pane. ' * 3 + '\n\n```python\nfor x in range(5):\n    print(x)\n```'

    def tearDown(self): self.directory.cleanup()
    def write(self, name, cwd='/project', body=None):
        lines = [ {'type':'session_meta','payload':{'cwd':cwd}},
          {'type':'response_item','payload':{'type':'reasoning','text':'PRIVATE REASONING'}},
          {'type':'response_item','payload':{'type':'message','role':'assistant','phase':'analysis','content':[{'type':'output_text','text':'HIDDEN ANALYSIS'}]}},
          {'type':'response_item','payload':{'type':'message','role':'assistant','phase':'final_answer','content':[{'type':'output_text','text':body or self.body}]}}]
        (self.day / ('rollout-'+name+'.jsonl')).write_text('\n'.join(map(json.dumps, lines))+'\n')

    def test_fork_identity_tracks_turn_and_repeated_messages(self):
        records = [{"type": "session_meta", "payload": {"cwd": "/project", "id": "session"}}]
        for i in range(2):
            records += [{"type": "event_msg", "payload": {"type": "task_started", "turn_id": str(i)}},
                        {"type": "response_item", "timestamp": str(i), "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Repeat"}]}}]
        path = self.day / "rollout-fork.jsonl"
        path.write_text("\n".join(map(json.dumps, records)) + "\n")
        _, messages = self.reader.read(path, True)
        self.assertNotEqual(messages[0]["id"], messages[1]["id"])
        self.assertEqual(messages[1]["fork"], {"thread": "session", "turn": "1"})

    def test_original_markdown_preserved_without_reasoning(self):
        self.write('one')
        result = self.reader.match(self.agent, '• '+self.body.replace('```python','').replace('```',''))
        self.assertEqual(result['messages'][0]['text'], self.body)
        self.assertNotIn('PRIVATE', str(result))
        self.assertNotIn('ANALYSIS', str(result))

    def test_image_wrappers_require_structured_image_and_keep_message_identity(self):
        text = 'Inspect this screenshot.\n\nAttached images (open these files to view them):\n/private/image.png'
        content = [{'type':'input_text','text':text},
                   {'type':'input_text','text':'<image name="image.png">'},
                   {'type':'input_image','image_url':'data:image/png;base64,fixture'},
                   {'type':'input_text','text':'</image>'}]
        record = {'type':'response_item','timestamp':'now','payload':{'type':'message','role':'user','content':content}}
        path = self.day/'rollout-image.jsonl'
        meta = {'type':'session_meta','payload':{'cwd':'/project'}}
        path.write_text(json.dumps(meta)+'\n'+json.dumps(record)+'\n')
        messages = self.reader.read(path, True)[1]
        self.assertEqual(messages[0]['text'], text)
        original = '\n'.join(part['text'] for part in content if 'text' in part)
        self.assertEqual(messages[0]['id'], hashlib.sha256(('now'+original).encode()).hexdigest()[:20])
        # A literal example with the same tags has no structured image payload.
        content.pop(2)
        path.write_text(json.dumps(meta)+'\n'+json.dumps(record)+'\n')
        self.assertEqual(self.reader.read(path, True)[1][0]['text'], original)

    def test_claude_runtime_notice_does_not_leak_or_change_history_identity(self):
        root = self.root/'claude'
        root.mkdir()
        path = root/'session.jsonl'
        notice = '<system-reminder>Internal CLI context.</system-reminder>'
        text = notice+'\nKeep the graph.'
        records = [{'type':'user','uuid':str(index),'cwd':'/project',
                    'message':{'role':'user','content':[{'type':'text','text':value}]}}
                   for index, value in enumerate([notice, text, 'Explain '+notice])]
        records.append({'type':'user','uuid':'separate','cwd':'/project','message':{'role':'user',
                        'content':[{'type':'text','text':'Keep the table.'}, {'type':'text','text':notice}]}})
        path.write_text('\n'.join(map(json.dumps, records))+'\n')
        messages = Transcripts(self.root, root).read_claude(path, True)[1]
        self.assertEqual([m['text'] for m in messages], ['Keep the graph.', 'Explain '+notice, 'Keep the table.'])
        self.assertEqual(messages[0]['id'], hashlib.sha256(('1\0'+text).encode()).hexdigest()[:20])

    def test_resumed_old_session_is_discovered_among_many_new_sessions(self):
        self.write('resumed')
        old = self.root / '2025/01/01'
        old.mkdir(parents=True)
        resumed = old / 'rollout-resumed.jsonl'
        (self.day / 'rollout-resumed.jsonl').rename(resumed)
        for index in range(55):
            self.write(f'new-{index}', body='Unrelated supervisor lookup.')
            path = self.day / f'rollout-new-{index}.jsonl'
            os.utime(path, (100, 100))
        os.utime(resumed, (200, 200))
        candidates = self.reader.candidates()
        self.assertEqual(len(candidates), 50)
        self.assertEqual(candidates[0], resumed)
        result = self.reader.match(self.agent, self.body, history=True, binding=True)
        self.assertEqual(result['messages'][-1]['text'], self.body)
        self.assertEqual(result['archive_source'], str(resumed))

    def test_verified_idle_session_survives_new_logs_but_still_requires_visible_identity(self):
        self.write('idle')
        agent = {**self.agent, 'id':'host:pane:process'}
        path = self.day / 'rollout-idle.jsonl'
        self.assertEqual(self.reader.match(agent, self.body)['source'], 'original')
        os.utime(path, (100, 100))
        for index in range(55):
            self.write(f'supervisor-{index}', body='A different supervisor lookup.')
        self.reader.scanned = 0
        self.assertNotIn(path, self.reader.candidates())
        self.assertEqual(self.reader.match(agent, self.body)['source'], 'original')
        self.assertEqual(self.reader.match(agent, 'Unrelated terminal output. '*30), {})
        self.assertEqual(self.reader.match({**agent, 'id':'host:pane:new-process'}, self.body), {})
        self.write('duplicate')
        self.reader.scanned = 0
        self.assertEqual(self.reader.match(agent, self.body), {})

    def test_context_content_parts_are_hidden_without_changing_fork_or_message_identity(self):
        context = '<codex_internal_context source="goal">Internal goal.</codex_internal_context>'
        env = '<environment_context><current_date>2026-10-14</current_date></environment_context>'
        state = dict(cwd='/project', session='thread', turn='turn', messages=[])
        def record(parts, role='user'):
            return json.dumps(dict(type='response_item', timestamp='now', payload=dict(
                type='message', role=role, content=[dict(type='input_text', text=text) for text in parts])))
        self.reader._parse_record(record([context, env]), state, 'codex')
        self.assertEqual(state['messages'], [])
        parts = [context, 'Keep the plot.', env]
        self.reader._parse_record(record(parts), state, 'codex')
        item = state['messages'][-1]
        self.assertEqual(item['text'], 'Keep the plot.')
        self.assertEqual(item['id'], hashlib.sha256(('now'+'\n'.join(parts)).encode()).hexdigest()[:20])
        self.assertEqual(item['fork'], dict(thread='thread', turn='turn'))
        self.reader._parse_record(record([context], 'assistant'), state, 'codex')
        self.assertEqual(state['messages'][-1]['text'], context)
        self.reader._parse_record(json.dumps(dict(type='event_msg', payload=dict(type='task_complete', turn_id='turn', last_agent_message='Finished.'))), state, 'codex')
        self.assertEqual(state['lifecycle']['state'], 'done')

    def test_commentary_channel_is_progress_and_reasoning_channels_are_excluded(self):
        path = self.day / 'rollout-channels.jsonl'
        records = [{'type':'session_meta','payload':{'cwd':'/project'}}]
        for channel in ('commentary','analysis','thinking','reasoning','final'):
            records.append({'type':'response_item','payload':{'type':'message','role':'assistant','channel':channel,
                'content':[{'type':'output_text','text':'Visible '+channel if channel in ('commentary','final') else 'PRIVATE '+channel}]}})
        path.write_text('\n'.join(map(json.dumps, records))+'\n')
        _, messages = self.reader.read(path, True)
        self.assertEqual([(item['phase'],item['text']) for item in messages], [('commentary','Visible commentary'),('','Visible final')])

    def test_cli_recaps_are_not_evidence_for_matching_a_transcript(self):
        self.write('one')
        self.assertEqual(self.reader.match(self.agent, '※ recap: ' + self.body.replace('\n', ' ')), {})
        self.assertEqual(self.reader.match(self.agent, '─ Conversation recap ──────────\n\n  ' + self.body.replace('\n', '\n  ')), {})
        footer = '● Short reply.\n────────────────\n❯\n────────────────\n  ' + self.body.replace('\n', '\n  ')
        self.assertEqual(self.reader.match(self.agent, footer), {})
        actual = self.reader.match(self.agent, '• ' + self.body + '\n\n※ recap: Duplicate notice.')
        self.assertEqual(actual['messages'][0]['text'], self.body)

    def test_history_includes_earlier_user_turns_without_reasoning(self):
        records = [{"type": "session_meta", "payload": {"cwd": "/project"}}]
        for i in range(35):
            records.append({"type": "response_item", "payload": {"type": "message", "role": "user",
                "content": [{"type": "input_text", "text": f"Question {i}"}]}})
            records.append({"type": "response_item", "payload": {"type": "message", "role": "assistant",
                "phase": "final_answer", "content": [{"type": "output_text", "text": self.body if i == 34 else f"Answer {i}"}]}})
        (self.day / "rollout-history.jsonl").write_text("\n".join(map(json.dumps, records)) + "\n")
        recent = self.reader.match(self.agent, self.body)
        history = self.reader.match(self.agent, self.body, history=True)
        self.assertEqual(len(recent["messages"]), 8)
        self.assertEqual(len(history["messages"]), 70)
        self.assertEqual(history["messages"][0]["text"], "Question 0")
        self.assertEqual(history["messages"][0]["role"], "user")

    def test_short_visible_video_reply_after_identity_match(self):
        self.write("video")
        path = self.day / "rollout-video.jsonl"
        message = "[Watch rollout](/project/artifacts/clip.mp4)\n\nResult: timeout."
        with path.open("a") as file:
            file.write(json.dumps({"type": "response_item", "payload": {"type": "message",
                "role": "assistant", "phase": "final_answer", "content": [{"type": "output_text", "text": message}]}}) + "\n")
        capture = self.body + "\nWatch rollout (artifacts/clip.mp4)\nResult: timeout."
        result = self.reader.match(self.agent, capture, history=True)
        self.assertEqual(result["messages"][-1]["text"], message)
        self.assertEqual(len(self.reader.match(self.agent, self.body, history=True)["messages"]), 1)

    def test_same_directory_ambiguous_matches_fall_back(self):
        self.write('one'); self.write('two')
        self.assertEqual(self.reader.match(self.agent, self.body), {})

    def write_dialogue(self, name, messages):
        path = self.day / ('rollout-' + name + '.jsonl')
        records = [{'type':'session_meta','payload':{'cwd':'/project','id':name}}]
        for index, (role, text) in enumerate(messages):
            records.append({'type':'response_item','timestamp':str(index),'payload':{
                'type':'message','role':role,'content':[{'type':'input_text' if role == 'user' else 'output_text','text':text}]}})
        path.write_text('\n'.join(map(json.dumps, records)) + '\n')
        self.reader.scanned = 0
        return path

    def test_restored_scrollback_does_not_outscore_the_current_session(self):
        self.write('restored', body=self.body * 4)
        current = ('The current session is checking why the supervisor shows an older conversation. '
                   'The terminal contains restored scrollback from a previous task, followed by the '
                   'new request and this shorter response about the chat history.')
        path = self.write_dialogue('current', [('user', 'Fix the stale chat.'), ('assistant', current),
                                               ('assistant', 'A later reply not yet displayed.')])
        capture = '• ' + self.body * 4 + '\n› Fix the stale chat.\n• ' + current
        for history in (False, True):
            result = self.reader.match(self.agent, capture, history=history, binding=True)
            self.assertEqual(result['archive_source'], str(path))
            self.assertEqual(result['binding']['thread_id'], 'current')
            self.assertEqual(result['messages'][-1]['text'], current)
        # A longer old answer cannot resolve ambiguity in the newest dialogue.
        self.write_dialogue('duplicate', [('assistant', current)])
        self.assertEqual(self.reader.match(self.agent, capture, [str(path)], binding=True), {})

    def short_exchange(self):
        return [('user', 'For some reason, the agent supervisor shows stale messages for this session instead of the current conversation.'),
                ('assistant', 'I will trace how Agent Hub selects the chat for this pane and check why the latest messages are missing from its preview.')]

    def test_short_current_exchange_identifies_session_after_restored_scrollback(self):
        self.write('restored', body=self.body * 4)
        exchange = self.short_exchange()
        path = self.write_dialogue('current', exchange)
        capture = ('• ' + self.body * 4 + '\n› ' + exchange[0][1] + '\n• Ran check\n  └ done\n• ' + exchange[1][1])
        for history in (False, True):
            result = self.reader.match(self.agent, capture, history=history, binding=True)
            self.assertEqual(result['archive_source'], str(path))
            self.assertEqual([item['text'] for item in result['messages']], [text for _, text in exchange])
        self.write_dialogue('duplicate', exchange)
        self.assertEqual(self.reader.match(self.agent, capture, [str(path)]), {})

    def test_short_exchange_requires_ordered_explicit_dialogue(self):
        exchange = self.short_exchange()
        self.write_dialogue('current', exchange)
        prompt, reply = [text for _, text in exchange]
        for capture in ('• ' + reply + '\n› ' + prompt,
                        '• Ran echo\n  › ' + prompt + '\n  • ' + reply,
                        '• Quoted example:\n```text\n› ' + prompt + '\n• ' + reply + '\n```',
                        '› ' + prompt + '\n• An unrelated intervening reply.\n• ' + reply):
            with self.subTest(capture=capture):
                self.assertEqual(self.reader.match(self.agent, capture), {})

    def test_repeated_short_acknowledgments_do_not_establish_identity(self):
        reply = 'I am checking the current configuration and will report the results.'
        self.write_dialogue('repeated', [('assistant', reply)] * 5)
        self.assertEqual(self.reader.match(self.agent, '\n'.join(['• ' + reply] * 5)), {})

    def test_tool_echo_is_not_assistant_identity_evidence(self):
        self.write('one')
        self.assertEqual(self.reader.match(self.agent, '• Ran cat transcript\n  ' + self.body.replace('\n', '\n  ')), {})

    def prompt_fixture(self, name='prompt', text=None, cwd='/project'):
        fixture = json.loads(Path(__file__).with_name('terminal-message-boundaries-fixtures.json').read_text())['prompt_diff_redraw']
        path = self.day / ('rollout-' + name + '.jsonl')
        records = [{'type':'session_meta','payload':{'cwd':cwd,'id':name}},
                   {'type':'response_item','payload':{'type':'message','role':'assistant',
                    'content':[{'type':'output_text','text':'Earlier response outside this capture.'}]}},
                   {'type':'response_item','payload':{'type':'message','role':'user',
                    'content':[{'type':'input_text','text':text or fixture['original']}]}},
                   {'type':'response_item','payload':{'type':'message','role':'assistant',
                    'content':[{'type':'output_text','text':'A newer reply not yet visible in the terminal.'}]}}]
        path.write_text('\n'.join(map(json.dumps, records))+'\n')
        self.reader.scanned = 0
        return fixture, path

    def test_clipped_prompt_recovers_original_message_without_appending_diff_or_unseen_reply(self):
        fixture, path = self.prompt_fixture()
        for history in (False, True):
            result = self.reader.match(self.agent, fixture['capture'], history=history, binding=True)
            self.assertEqual(result['source'], 'original')
            self.assertEqual([m['role'] for m in result['messages']], ['assistant', 'user'])
            self.assertEqual(result['messages'][-1]['text'], fixture['original'])
            self.assertEqual(result['archive_source'], str(path))
            self.assertEqual(result['binding']['thread_id'], 'prompt')
            self.assertNotIn('import re', str(result))

    def test_prompt_identity_requires_unique_explicit_prefix_and_matching_directory(self):
        fixture, path = self.prompt_fixture()
        self.assertEqual(self.reader.match({**self.agent,'cwd':'/other'}, fixture['capture']), {})
        # A tool printing that text is not a user input record.
        self.assertEqual(self.reader.match(self.agent, fixture['capture'].replace('› ', '• Ran echo ', 1)), {})
        self.assertEqual(self.reader.match(self.agent, fixture['capture'].replace('…', ' unrelated words', 1)), {})
        self.assertEqual(self.reader.match(self.agent, '› continue\n• Ran '+('output '*70)), {})
        self.assertEqual(self.reader.match(self.agent, '› '+fixture['original']+'\n  Extra user instructions.'), {})
        self.assertEqual(self.reader.match(self.agent, '• Quoted example:\n```text\n'+fixture['capture']+'\n```'), {})
        # Two sessions that differ only beyond the clipped prefix stay ambiguous,
        # even when a cached file or a native connection offers one as a hint.
        self.prompt_fixture('other', fixture['original']+' Different follow-up.')
        self.assertEqual(self.reader.match(self.agent, fixture['capture'], [str(path)], binding=True), {})

    def test_full_wrapped_user_prompt_can_identify_a_session_before_first_reply(self):
        fixture, _ = self.prompt_fixture()
        capture = '› '+fixture['original'].replace('\n','\n  ')+'\n• Ran make\n  └ Checking'
        result = self.reader.match(self.agent, capture, history=True)
        self.assertEqual(result['messages'][-1]['text'], fixture['original'])
        self.assertEqual(len(result['messages']), 2)

    def test_native_binding_requires_the_same_unique_visible_reply_and_matching_directory(self):
        self.write('native')
        path=self.day/'rollout-native.jsonl'
        records=[json.loads(line) for line in path.read_text().splitlines()]
        records[0]['payload']['id']='01234567-1234-1234-1234-0123456789ab'
        path.write_text('\n'.join(map(json.dumps,records))+'\n')
        value=self.reader.match(self.agent,self.body,binding=True)
        self.assertEqual(value['binding'],{'thread_id':records[0]['payload']['id'],'path':str(path.resolve()),'cwd':'/project'})
        self.assertNotIn('binding',self.reader.match(self.agent,self.body))
        self.assertEqual(self.reader.match(self.agent,'unrelated output '*30,[str(path)],binding=True),{})
        self.write('duplicate');self.reader.scanned=0
        self.assertEqual(self.reader.match(self.agent,self.body,[str(path)],binding=True),{})

    def test_structured_turn_events_distinguish_progress_internal_restarts_and_final_handoff(self):
        path = self.day / 'rollout-lifecycle.jsonl'
        path.write_text(json.dumps({'type':'session_meta','payload':{'cwd':'/project','id':'thread'}})+'\n')
        binding = dict(path=str(path), cwd='/project', thread_id='thread')
        terminal = dict(state='unknown', summary='Unverified', activity_confirmed=False, completion_confirmed=False)
        def event(kind, turn='one', **extra):
            with path.open('a') as file:
                file.write(json.dumps({'type':'event_msg','timestamp':'2026-10-14T08:36:00Z',
                                       'payload':{'type':kind,'turn_id':turn,**extra}})+'\n')
        event('task_started')
        self.assertEqual(self.reader.observe(binding,terminal)['state'],'running')
        with path.open('a') as file:
            file.write(json.dumps({'type':'response_item','payload':{'type':'message','role':'assistant','phase':'commentary',
                'content':[{'type':'output_text','text':'The two runs are complete. I am checking the viewer.'}]}})+'\n')
        self.assertEqual(self.reader.observe(binding,terminal)['state'],'running')
        event('task_complete',last_agent_message=None)
        self.assertFalse(self.reader.observe(binding,terminal)['completion_confirmed'])
        event('task_started',turn='two')
        event('task_complete',turn='one',last_agent_message='Late old event')
        self.assertEqual(self.reader.observe(binding,terminal)['state'],'running')
        event('task_complete',turn='two',last_agent_message='The final measured result.')
        done=self.reader.observe(binding,terminal)
        self.assertEqual(done['state'],'done')
        self.assertTrue(done['completion_confirmed'])
        for override in (dict(state='running',activity_confirmed=True),dict(state='waiting'),dict(state='blocked')):
            self.assertEqual(self.reader.observe(binding,{**terminal,**override})['state'],override['state'])
        self.assertEqual(self.reader.observe({**binding,'thread_id':'other'},terminal),terminal)
        event('task_started',turn='three')
        current=self.reader.observe(binding,done)
        self.assertEqual(current['state'],'running')
        self.assertFalse(current['completion_confirmed'])
        event('turn_aborted',turn='two',reason='interrupted')
        self.assertEqual(self.reader.observe(binding,terminal)['state'],'running')
        event('turn_aborted',turn='three',reason='interrupted')
        interrupted=self.reader.observe(binding,terminal)
        self.assertEqual(interrupted['state'],'idle')
        self.assertFalse(interrupted['completion_confirmed'])
        event('task_started',turn='four')
        self.assertEqual(self.reader.observe(binding,interrupted)['state'],'running')


    def test_new_input_and_progress_invalidate_a_completed_turn_before_start_event_arrives(self):
        terminal = dict(state='done', summary='Old footer', activity_confirmed=False, completion_confirmed=True)
        messages = [
            dict(type='message', role='user', content=[dict(type='input_text', text='Run the next study.')]),
            dict(type='message', role='assistant', phase='commentary', content=[dict(type='output_text', text='Checking the next result.')]),
            dict(type='message', role='assistant', channel='analysis', content=[dict(type='output_text', text='PRIVATE REASONING')]),
            dict(type='function_call', name='exec_command', arguments='PRIVATE TOOL ARGUMENTS'),
            dict(type='reasoning', text='PRIVATE REASONING'),
        ]
        for index, payload in enumerate(messages):
            with self.subTest(record=payload['type'], role=payload.get('role')):
                path = self.day / f'rollout-resume-{index}.jsonl'
                binding = dict(path=str(path), cwd='/project', thread_id='thread')
                def append(value):
                    with path.open('a') as file:
                        file.write(json.dumps(value) + '\n')
                def event(kind, turn='old', **extra):
                    append(dict(type='event_msg', payload=dict(type=kind, turn_id=turn, **extra)))
                append(dict(type='session_meta', payload=dict(cwd='/project', id='thread')))
                event('task_started')
                event('task_complete', last_agent_message='First result.')
                self.assertTrue(self.reader.observe(binding, terminal)['completion_confirmed'])
                append(dict(type='response_item', payload=payload))
                # A late event for the old turn cannot overwrite this new work.
                event('task_complete', last_agent_message='First result.')
                event('task_complete', turn='even-older', last_agent_message='Unrelated older result.')
                resumed = self.reader.observe(binding, terminal)
                self.assertEqual(resumed['state'], 'running')
                self.assertFalse(resumed['completion_confirmed'])
                self.assertNotIn('PRIVATE', str(self.reader.read(path)))
                event('task_started', turn='new')
                event('task_complete', turn='new', last_agent_message='Next result.')
                completed = self.reader.observe(binding, terminal)
                self.assertEqual(completed['turn_status']['turn_id'], 'new')
                self.assertTrue(completed['completion_confirmed'])

    def test_repeated_final_and_internal_context_do_not_reopen_a_completed_turn(self):
        state = dict(cwd='/project', session='thread', turn='one', messages=[])
        def parse(kind, payload):
            self.reader._parse_record(json.dumps(dict(type=kind, payload=payload)), state, 'codex')
        parse('event_msg', dict(type='task_complete', turn_id='one', last_agent_message='First result.'))
        parse('response_item', dict(type='message', role='assistant', phase='final_answer',
                                   content=[dict(type='output_text', text='First result.')]))
        parse('response_item', dict(type='message', role='user', content=[dict(type='input_text',
                                   text='<codex_internal_context source="goal">Internal goal.</codex_internal_context>')]))
        self.assertEqual(state['lifecycle']['state'], 'done')
        self.assertNotIn('pending_turn', state)
        parse('response_item', dict(type='message', role='user', content=[dict(type='input_text', text='Next study.')]))
        parse('response_item', dict(type='message', role='assistant', phase='final_answer',
                                   content=[dict(type='output_text', text='Next result.')]))
        parse('event_msg', dict(type='task_complete', turn_id='two', last_agent_message='Next result.'))
        self.assertEqual(state['lifecycle']['state'], 'done')
        self.assertEqual(state['lifecycle']['turn_id'], 'two')

    def test_image_answer_arrives_without_another_user_turn(self):
        self.write('image')
        path = self.day / 'rollout-image.jsonl'
        commentary = 'I will pull up the saved earlier screenshot so you can see the original look.'
        final = ('Here is the original all-glass look, before the darker tints and stronger blur:\n\n'
                 '![Original all-glass style](/project/.restore-original/live-original-dark.png)\n\n'
                 '[Open full-resolution image](/project/.restore-original/live-original-dark.png)')
        with path.open('a') as file:
            for phase, text in [('commentary', commentary), ('final_answer', final)]:
                file.write(json.dumps({'type':'response_item','payload':{'type':'message','role':'assistant',
                    'phase':phase,'content':[{'type':'output_text','text':text}]}})+'\n')
        capture = self.body + '\n• ' + commentary
        self.assertEqual(self.reader.match(self.agent, capture, history=True)['messages'][-1]['text'], commentary)
        rendered = '\n• Here is the original all-glass look, before the darker tints and stronger blur:\n\n  Original all-glass style\n\n  Open full-resolution image'
        self.assertEqual(self.reader.match(self.agent, capture + rendered, history=True)['messages'][-1]['text'], final)
        # A matching link label in an older turn cannot expose a later reply.
        self.assertEqual(self.reader.match(self.agent, rendered + '\n' + capture, history=True)['messages'][-1]['text'], commentary)

    def test_rendered_links_can_establish_identity_without_guessing_the_session(self):
        text = ('The measurements for this robot learning experiment are available in '
                '[the command and measured-motion trace](/project/artifacts/trace.html). '
                'The next checkpoint comparison is ready in [the detailed results](/project/artifacts/results.html). '
                'Each trial uses the same inputs, controller settings and evaluation period.')
        self.write('links', body=text)
        visible = text.replace('](/project/artifacts/trace.html)', ']').replace('](/project/artifacts/results.html)', ']')
        self.assertEqual(self.reader.match(self.agent, visible)['messages'][-1]['text'], text)
        self.write('another', body=text)
        self.reader.scanned = 0
        self.assertEqual(self.reader.match(self.agent, visible), {})

    def test_old_session_hint_requires_a_visible_match(self):
        self.write('old')
        old = self.root / '2020/01/01/rollout-old.jsonl'
        old.parent.mkdir(parents=True)
        (self.day / 'rollout-old.jsonl').rename(old)
        self.assertEqual(self.reader.match(self.agent, 'unrelated visible response '*40), {})
        self.assertEqual(self.reader.match(self.agent, 'unrelated visible response '*40, [str(old)]), {})
        self.assertEqual(self.reader.match(self.agent, self.body, [str(old)])['messages'][0]['text'], self.body)

    def test_wrong_pane_or_directory_never_uses_unmatched_session(self):
        self.write('one', cwd='/other')
        self.assertEqual(self.reader.match(self.agent, self.body), {})
        self.assertEqual(self.reader.match({'runtime':'claude','cwd':'/other'}, self.body), {})

    def test_later_invisible_message_is_not_returned(self):
        self.write('one')
        path = self.day / 'rollout-one.jsonl'
        with path.open('a') as file:
            file.write(json.dumps({'type':'response_item','payload':{'type':'message','role':'assistant','phase':'final_answer','content':[{'type':'output_text','text':'NEW INVISIBLE RESPONSE'}]}})+'\n')
        result = self.reader.match(self.agent, self.body)
        self.assertEqual(len(result['messages']), 1)
        self.assertNotIn('INVISIBLE', str(result))

    def test_new_terminal_input_appears_before_any_assistant_reply(self):
        self.write('live-input')
        path = self.day / 'rollout-live-input.jsonl'
        for text in ('Compare the new checkpoints\nand report the variance.', 'continue'):
            with path.open('a') as file:
                file.write(json.dumps({'type':'response_item','timestamp':text,'payload':{'type':'message','role':'user',
                    'content':[{'type':'input_text','text':text}]}})+'\n')
            result = self.reader.match(self.agent, self.body + '\n› ' + text.replace('\n','\n  ') + '\n• Working (1s • esc to interrupt)\n› ', history=True)
            self.assertEqual(result['messages'][-1]['role'], 'user')
            self.assertEqual(result['messages'][-1]['text'], text)
        hidden = self.reader.match(self.agent, self.body, history=True)
        self.assertEqual(len(hidden['messages']), 1)
        old_prompt = self.reader.match(self.agent, '› continue\n• ' + self.body, history=True)
        self.assertEqual(len(old_prompt['messages']), 1)

class ClaudeTranscriptTests(unittest.TestCase):
    def test_history_survives_cd_into_subfolders(self):
        # The agent's shell moves into a run folder and back; that's still its session.
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'session.jsonl'
            records=[{'uuid':str(i),'type':'user' if i%2==0 else 'assistant','cwd':cwd,'message':{'role':'user' if i%2==0 else 'assistant','content':f'message {i}'}}
                     for i,cwd in enumerate(['/project/runs/a','/project','/project/runs/a','/project','/project/runs/b','/project'])]
            path.write_text('\n'.join(map(json.dumps,records))+'\n')
            cwd,messages=Transcripts(root/'codex',root).read_claude(path)
            self.assertEqual(cwd,'/project')
            self.assertEqual(len(messages),6)

    def test_unrelated_folder_still_starts_over(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'session.jsonl'
            records=[{'uuid':str(i),'type':'user','cwd':cwd,'message':{'role':'user','content':f'message {i}'}} for i,cwd in enumerate(['/project','/other'])]
            path.write_text('\n'.join(map(json.dumps,records))+'\n')
            cwd,messages=Transcripts(root/'codex',root).read_claude(path)
            self.assertEqual((cwd,len(messages)),('/other',1))

    def test_repeated_user_text_keeps_distinct_message_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'session.jsonl'
            records=[{'uuid':identity,'type':'user','cwd':'/project','message':{'role':'user','content':'continue'}} for identity in ('first','second','second')]
            path.write_text('\n'.join(map(json.dumps,records))+'\n')
            _,messages=Transcripts(root/'codex',root).read_claude(path)
            self.assertEqual(len(messages),2)
            self.assertNotEqual(messages[0]['id'],messages[1]['id'])

    def test_tool_use_metadata_marks_progress_without_guessing_from_prose(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'session.jsonl'
            records=[{'type':'assistant','cwd':'/project','message':{'role':'assistant','content':[
                {'type':'text','text':'I will inspect the traces.'},{'type':'tool_use','name':'Read','input':{}}]}},
                {'type':'assistant','cwd':'/project','message':{'role':'assistant','content':[{'type':'text','text':'I think the measured result is useful.'}]}}]
            path.write_text('\n'.join(map(json.dumps,records))+'\n')
            _,messages=Transcripts(root/'codex',root).read_claude(path)
            self.assertEqual(messages[0]['phase'],'commentary')
            self.assertEqual(messages[1]['phase'],'')

    def test_claude_visible_text_matches_without_thinking_or_sidechains(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); project=root/'-project'; project.mkdir()
            body='The visible response includes a sufficiently detailed description of an experiment and its measured result, together with a mathematical explanation of the calculation. ' * 3 + '\n$$E=mc^2$$'
            records=[{'type':'assistant','cwd':'/project','isSidechain':False,'message':{'role':'assistant','content':[{'type':'thinking','thinking':'SECRET'},{'type':'text','text':body}]}},
                     {'type':'assistant','cwd':'/project','isSidechain':True,'message':{'role':'assistant','content':[{'type':'text','text':'HIDDEN SIDECHAIN'}]}}]
            (project/'session.jsonl').write_text('\n'.join(map(json.dumps,records)))
            reader=Transcripts(root/'codex',root)
            result=reader.match({'runtime':'claude','cwd':'/project'},'● '+body.replace('$$',''))
            self.assertEqual(result['messages'][0]['text'],body)
            self.assertNotIn('SECRET',str(result));self.assertNotIn('SIDECHAIN',str(result))


class IncrementalTranscriptTests(unittest.TestCase):
    setUp = TranscriptTests.setUp
    tearDown = TranscriptTests.tearDown
    write = TranscriptTests.write
    def record(self, text, role='assistant'):
        return json.dumps({'type':'response_item','timestamp':text[:24], 'payload':{'type':'message','role':role,
            'content':[{'type':'output_text','text':text}]}}).encode()

    def test_short_and_history_caches_do_not_evict_each_other(self):
        self.write('cache')
        path = self.day / 'rollout-cache.jsonl'
        for history in (False, True):
            self.reader.read(path, history)
        before = dict(self.reader.metrics)
        for _ in range(5):
            for history in (False, True):
                self.assertEqual(self.reader.read(path, history)[1][-1]['text'], self.body)
        self.assertEqual(self.reader.metrics['bytes_read'], before['bytes_read'])
        self.assertEqual(self.reader.metrics['records'], before['records'])
        self.assertEqual(self.reader.metrics['cache_hits'] - before['cache_hits'], 10)

    def test_appends_parse_only_new_records_with_bounded_reads(self):
        self.write('append')
        path = self.day / 'rollout-append.jsonl'
        with path.open('ab') as file:
            for i in range(100):
                file.write(self.record(str(i) + 'x' * 5000) + b'\n')
        self.reader.read(path, True)
        before = dict(self.reader.metrics)
        with path.open('ab') as file:
            file.write(self.record('A new result') + b'\n')
        self.assertEqual(self.reader.read(path, True)[1][-1]['text'], 'A new result')
        self.assertLess(self.reader.metrics['bytes_read'] - before['bytes_read'], 1200)
        self.assertEqual(self.reader.metrics['records'] - before['records'], 1)
        self.assertEqual(self.reader.metrics['resets'], before['resets'])

    def test_partial_record_completes_without_duplication_or_missing_text(self):
        self.write('partial')
        path = self.day / 'rollout-partial.jsonl'
        self.reader.read(path, True)
        raw = self.record('A complete café result')
        split = raw.index(b'caf') + 4
        with path.open('ab') as file: file.write(raw[:split])
        self.assertEqual(len(self.reader.read(path, True)[1]), 1)
        with path.open('ab') as file: file.write(raw[split:])
        self.assertEqual(self.reader.read(path, True)[1][-1]['text'], 'A complete café result')
        with path.open('ab') as file: file.write(b'\n' + self.record('Next') + b'\n')
        self.assertEqual([m['text'] for m in self.reader.read(path, True)[1]], [self.body, 'A complete café result', 'Next'])

    def test_truncation_rotation_and_same_size_rewrite_reset(self):
        self.write('rotate')
        path = self.day / 'rollout-rotate.jsonl'
        self.reader.read(path, True)
        self.write('rotate', body='short')
        self.assertEqual(self.reader.read(path, True)[1][-1]['text'], 'short')
        self.write('rotate', body='other')
        self.assertEqual(self.reader.read(path, True)[1][-1]['text'], 'other')
        path.rename(path.with_suffix('.old'))
        self.write('rotate', cwd='/new', body='new session')
        cwd, messages = self.reader.read(path, True)
        self.assertEqual(cwd, '/new')
        self.assertEqual([m['text'] for m in messages], ['new session'])

    def test_growth_with_rewritten_tail_resets(self):
        self.write('rewrite')
        path = self.day / 'rollout-rewrite.jsonl'
        self.reader.read(path, True)
        self.write('rewrite', body=self.body.replace('algorithm', 'procedure') + ' A changed end.')
        self.assertEqual(self.reader.read(path, True)[1][-1]['text'], self.body.replace('algorithm', 'procedure') + ' A changed end.')

    def test_claude_append_without_newline_preserves_filters(self):
        root = self.root / 'claude'
        root.mkdir()
        path = root / 'session.jsonl'
        reader = Transcripts(self.root, root)
        def line(text, **flags):
            return json.dumps({'type':'assistant','cwd':'/project', **flags,
                'message':{'role':'assistant','content':[{'type':'thinking','thinking':'SECRET'}, {'type':'text','text':text}]}}).encode()
        path.write_bytes(line('First'))
        self.assertEqual(reader.read_claude(path, True)[1][0]['text'], 'First')
        with path.open('ab') as file: file.write(b'\n' + line('hidden', isSidechain=True) + b'\n' + line('Second'))
        messages = reader.read_claude(path, True)[1]
        self.assertEqual([m['text'] for m in messages], ['First','Second'])
        self.assertNotIn('SECRET', str(messages))


class ClaudeDisplayTests(unittest.TestCase):
    def display(self, text, **flags):
        return claude_display({"message": {"role": "user"}, **flags}, text)

    def test_internal_caveat_is_not_a_user_message(self):
        text = "<local-command-caveat>Caveat: The messages below were generated by the user while running local commands.</local-command-caveat>"
        self.assertIsNone(self.display(text))
        self.assertIsNone(self.display("internal", isMeta=True))

    def test_commands_outputs_and_tasks_have_their_own_role(self):
        command = self.display("<command-name>/model</command-name><command-message>model</command-message><command-args>opus</command-args>")
        self.assertEqual(command, {"role":"tool", "kind":"command", "text":"/model opus"})
        output = self.display("<local-command-stdout>Model changed.</local-command-stdout>")
        self.assertEqual(output["text"], "Model changed.")
        self.assertEqual(output["kind"], "command-output")
        task = self.display("<task-notification><task-id>12</task-id><summary>Evaluation complete.</summary></task-notification>")
        self.assertEqual(task["kind"], "task-update")
        self.assertEqual(task["text"], "Evaluation complete.")

    def test_cli_events_are_not_user_messages(self):
        summary = "This session is being continued from a previous conversation that ran out of context.\n\n<system-reminder>\n<tools-used-before-summary>Bash</tools-used-before-summary>"
        self.assertEqual(self.display(summary, isCompactSummary=True)["kind"], "compaction")
        self.assertEqual(self.display(summary)["kind"], "compaction")
        self.assertEqual(self.display("[Request interrupted by user for tool use]"), {"role":"tool", "kind":"interrupt", "text":"Interrupted"})
        self.assertEqual(self.display("<bash-input>git push</bash-input>"), {"role":"tool", "kind":"command", "text":"! git push"})
        out = self.display("<bash-stdout>Everything up-to-date</bash-stdout><bash-stderr></bash-stderr>")
        self.assertEqual(out, {"role":"tool", "kind":"command-output", "text":"Everything up-to-date"})
        self.assertIsNone(self.display("<bash-stdout></bash-stdout><bash-stderr></bash-stderr>"))

    def test_user_code_and_quoted_tags_stay_intact(self):
        for text in ('Explain <local-command-caveat>example</local-command-caveat>', '```xml\n<local-command-caveat>example</local-command-caveat>\n```', '<custom>data</custom>'):
            self.assertEqual(self.display(text), {"role":"user", "text":text})
