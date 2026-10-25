"""Capacity recovery uses fixtures and an isolated tmux server only."""
import json
from pathlib import Path
import shlex
import shutil
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch

import bridge
from recovery import CapacityRecovery
from server import Hub
from test_native import BINDING, metadata

ERROR = '■ Selected model is at capacity. Please try a different model.'
SCREEN = ERROR + '\n\n› \n'


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        with patch.object(Hub, 'save_connection'):
            self.hub = Hub(self.temp.name)
        self.now = 1000
        self.recovery = CapacityRecovery(self.hub, clock=lambda: self.now)
        self.connection = {'id':'test','kind':'local','label':'Test'}
        self.agent = {'id':'test:%1:100:@1:200','pane':'%1','runtime':'codex','live':True,
                      'state':'blocked','summary':'Capacity','title':'Study','capacity_error':True,
                      'cursor_x':'2','cursor_y':'2','in_mode':'0'}

    def tearDown(self):
        self.hub.close(); self.temp.cleanup()

    def observe(self, advance=0, **change):
        self.now += advance
        agent = {**self.agent, **change}
        self.recovery.observe(self.connection, agent)
        return agent

    def test_capacity_is_current_and_other_terminal_text_is_not_authority(self):
        for screen in (SCREEN, ERROR.replace('Please','\nPlease')+'\n› '):
            observation = bridge.observation(self.agent, screen)
            self.assertEqual(observation['state'],'blocked')
            self.assertTrue(observation['capacity_error'])
        for screen in ('  '+SCREEN, SCREEN+'\n• The explanation is complete.\n› ',
                       SCREEN+'continue\n\n› ', ERROR+'\n■ Another error\n› '):
            self.assertFalse(bridge.capacity_error(self.agent, screen))
        for screen, state in ((SCREEN+'\n• Working (2s • esc to interrupt)\n› ', 'running'),
                              (SCREEN+'\nWould you like to run the following command?', 'waiting')):
            observation = bridge.observation(self.agent, screen)
            self.assertEqual(observation['state'],state)
            self.assertFalse(observation['capacity_error'])
        self.assertFalse(bridge.capacity_error({**self.agent,'live':False},SCREEN))

    def test_capacity_wording_and_narrow_terminal_wrapping(self):
        errors = (ERROR, '■ The model is at capacity. Please try again later.',
                  '■ Model is at capacity.', '■ The model is currently at capacity.',
                  '■ Selected model is at capacity')
        for error in errors:
            for width in (20, 32, 80):
                screen = '\n'.join(textwrap.wrap(error, width, subsequent_indent='  ')) + '\n\n› '
                with self.subTest(error=error, width=width):
                    observation = bridge.observation(self.agent, screen)
                    self.assertTrue(observation['capacity_error'])
                    self.assertEqual(observation['state'], 'blocked')
                    self.assertFalse(bridge.capacity_error(self.agent, screen + 'continue\n› '))
        for text in ('› The model is at capacity.', '• The model is at capacity.',
                     '  ■ The model is at capacity.', '■ The model is at capacity planning stage.',
                     '■ The model is unavailable.', '■ You have hit your usage limit.'):
            self.assertFalse(bridge.capacity_error(self.agent, text + '\n› '), text)

    def test_current_codex_warning_marker_and_live_placeholder_reach_recovery(self):
        for marker in ('■', '⚠', '⚠️'):
            for width in (24, 80):
                warning = ERROR.replace('■', marker)
                screen = '\n'.join(textwrap.wrap(warning, width, subsequent_indent='  '))
                screen += '\n\n› Ask Codex to do anything\n\n  gpt-6-astra max · /project · weekly 98% left · ← for agents\n'
                agent = {**self.agent, 'cursor_y':str(screen.splitlines().index('› Ask Codex to do anything'))}
                with self.subTest(marker=marker,width=width):
                    self.assertTrue(bridge.empty_codex_editor(agent, screen))
                    self.hub.native.cache.clear()
                    with patch.object(self.hub.native, 'available', return_value=True), \
                            patch.object(self.hub.native, 'call', return_value={'thread':metadata({'type':'systemError'})}):
                        observed=self.hub.agent_observation(self.connection, agent, screen, screen, {'binding':BINDING})
                    self.assertTrue(observed['capacity_error'])
                    self.assertEqual(observed['summary'],'Selected model is at capacity')
                    with patch.object(bridge,'verify_agent',return_value=agent), patch.object(bridge,'run',return_value=screen) as run:
                        bridge.retry_capacity(self.connection,agent)
                        self.assertEqual(run.call_args.kwargs['stdin'],'continue')
                # Quoted output and subsequent turns cannot trigger retries.
                self.assertFalse(bridge.capacity_error(agent,'  '+screen))
                self.assertFalse(bridge.capacity_error(agent,screen+'\n• Working (1s • esc to interrupt)\n› '))
                self.assertFalse(bridge.capacity_error(agent,screen+'\n› continue\n\n› '))
        for later in ('⚠ A different runtime error.', '⚠️ You have hit your usage limit.', '■ A different error.'):
            self.assertFalse(bridge.capacity_error(self.agent, ERROR+'\n'+later+'\n› '))
        notice='⚠ Heads up, you have less than 5% of your weekly limit left. Run /status for a breakdown.'
        self.assertTrue(bridge.capacity_error(self.agent, ERROR+'\n'+notice+'\n› '))

    def test_native_capacity_failure_reaches_recovery_and_rearms_after_work(self):
        original = {'binding': BINDING}
        def observe(status, screen=SCREEN, advance=0):
            self.now += advance
            self.hub.native.cache.clear()
            with patch.object(self.hub.native, 'available', return_value=True), \
                    patch.object(self.hub.native, 'call', return_value={'thread':metadata(status)}):
                agent = {**self.agent, 'state':'running'}
                agent.update(self.hub.agent_observation(self.connection, agent, screen, screen, original))
                self.recovery.observe(self.connection, agent)
                return agent
        with patch.object(bridge, 'retry_capacity') as send:
            error = {'type':'systemError'}
            self.assertEqual(observe(error)['recovery']['status'], 'retrying')
            observe(error, advance=2)
            self.assertEqual(send.call_count, 1)
            # A native running turn overrides even a not-yet-redrawn error.
            working = observe({'type':'active', 'activeFlags':[]}, advance=1)
            self.assertTrue(working['activity_confirmed'])
            self.assertNotIn('recovery', working)
            observe(error, advance=10); observe(error, advance=2)
            self.assertEqual(send.call_count, 2)
            done = observe({'type':'idle'}, '• Completed.\nWorked for 30s\n› ', advance=30)
            self.assertNotIn('recovery', done)
            observe(error, '■ A different runtime error.\n› ', advance=30)
            self.assertEqual(send.call_count, 2)

    def test_send_rechecks_capacity_cursor_draft_and_process_identity(self):
        self.assertTrue(bridge.empty_codex_editor(self.agent, SCREEN.replace('› ', '›')))
        with patch.object(bridge,'verify_agent',return_value=self.agent), patch.object(bridge,'run',return_value=SCREEN) as run:
            bridge.retry_capacity(self.connection,self.agent)
            self.assertEqual(run.call_count,2)
            self.assertEqual(run.call_args.kwargs['stdin'],'continue')
        for screen, change in ((SCREEN+'\n• Working (1s • esc to interrupt)',{}),
                               (SCREEN.replace('› ','› my draft'),{}), (SCREEN,{'cursor_x':'6'}),
                               (SCREEN,{'in_mode':'1'}), ('Ready\n\n› \n',{})):
            with self.subTest(screen=screen,change=change), patch.object(bridge,'verify_agent',return_value={**self.agent,**change}), patch.object(bridge,'run',return_value=screen) as run:
                with self.assertRaises(bridge.BridgeError): bridge.retry_capacity(self.connection,self.agent)
                self.assertEqual(run.call_count,1)
        with patch.object(bridge,'list_agents',return_value=[{**self.agent,'id':'test:%1:100:@1:999'}]), patch.object(bridge,'run') as run:
            with self.assertRaises(bridge.BridgeError): bridge.retry_capacity(self.connection,self.agent)
            run.assert_not_called()

    def test_retries_continue_with_cooldown_survive_restart_and_stop_on_activity(self):
        with patch.object(bridge,'retry_capacity') as send:
            self.observe(); self.observe(1); send.assert_not_called()
            self.observe(1); self.assertEqual(send.call_count,1)
            self.recovery = CapacityRecovery(self.hub,clock=lambda:self.now)
            self.observe(1); self.assertEqual(send.call_count,1)
            self.observe(4); self.assertEqual(send.call_count,2)
            self.observe(9); self.assertEqual(send.call_count,2)
            self.observe(1); self.assertEqual(send.call_count,3)
            working = self.observe(1,state='running',activity_confirmed=True,capacity_error=False)
            self.assertNotIn('recovery',working)
            self.observe(100,state='running',activity_confirmed=True,capacity_error=False)
            self.assertEqual(send.call_count,3)
            self.observe(1); self.observe(2)
            self.assertEqual(send.call_count,4, 'a fresh capacity failure resumes recovery')

    def test_draft_or_approval_race_never_becomes_a_successful_retry(self):
        with patch.object(bridge,'retry_capacity',side_effect=bridge.BridgeError('Retry waits for an empty terminal input')) as send:
            self.observe(); result=self.observe(2)
            self.assertEqual(result['recovery']['attempts'],0)
            self.assertEqual(result['recovery']['status'],'waiting')
            self.observe(1); self.assertEqual(send.call_count,1)
        self.recovery.manual(self.agent['id'],paused=True)
        with patch.object(bridge,'retry_capacity') as send:
            self.assertEqual(self.observe(100)['recovery']['status'],'paused'); send.assert_not_called()
            self.recovery.manual(self.agent['id'],paused=False)
            self.observe(); self.observe(2); self.assertEqual(send.call_count,1)

    def test_remote_snapshots_cannot_retry_and_removed_agents_are_pruned(self):
        with patch.object(bridge,'retry_capacity') as send:
            self.recovery.observe({**self.connection,'kind':'ssh'},dict(self.agent)); self.now+=100
            self.recovery.observe({**self.connection,'kind':'ssh'},dict(self.agent)); send.assert_not_called()
        self.observe()
        self.recovery.prune('test',set())
        self.assertEqual(self.recovery.records,{})
        self.assertEqual(CapacityRecovery(self.hub).records,{})

    def test_pause_control_stops_retries_and_terminal_resume_rearms_them(self):
        self.hub.recovery = self.recovery
        agent = self.observe()
        self.hub.connections['test'] = {'config':self.connection,'agents':[agent],'status':'online'}
        with patch.object(bridge,'interrupt') as interrupt, patch.object(bridge,'retry_capacity') as send:
            result=self.hub.local_action({'type':'interrupt','agent_id':agent['id']})
            self.assertIn('retries paused',result['message']); interrupt.assert_not_called()
            self.observe(30); send.assert_not_called()
            self.observe(1,capacity_error=False,activity_confirmed=True,state='running')
            self.observe(1); self.observe(2)
            self.assertEqual(send.call_count,1)

    @unittest.skipUnless(shutil.which('tmux') and sys.platform=='linux','isolated Linux tmux fixture')
    def test_real_tmux_capacity_work_capacity_work_cycle(self):
        root=Path(self.temp.name); worker=root/'worker.py'; inputs=root/'inputs.jsonl'
        worker.write_text(f'''import ctypes,json,sys,time,textwrap
ctypes.CDLL(None).prctl(15,b"codex",0,0,0)
for attempt,error in enumerate([{ERROR.replace('■', '⚠')!r}, "■ The model is at capacity. Please try again later."]):
    print("\\x1b[2J\\x1b[H"+textwrap.fill(error,20,subsequent_indent="  ")+"\\n\\n› ",end="",flush=True)
    line=sys.stdin.readline().strip()
    with open({str(inputs)!r},"a") as f: f.write(json.dumps(line)+"\\n")
    print("\\x1b[2J\\x1b[H• Working (1s • esc to interrupt)\\n› ",end="",flush=True)
    time.sleep(.4 if attempt==0 else 60)
''')
        connection={**self.connection,'socket':str(root/'fixture.sock')}
        command=shlex.join([shutil.which('bash'),'-c','exec -a codex '+shlex.join([sys.executable,str(worker)])])
        pane=bridge.run(connection,['-f','/dev/null','new-session','-d','-s','capacity-fixture','-P','-F','#{pane_id}',command]).strip()
        try:
            resumed=0; deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                agents=bridge.list_agents(connection)
                if agents:
                    agent=agents[0]
                    screen = bridge.capture(connection, pane, 0)
                    terminal = bridge.observation(agent, screen)
                    status = ({'type':'systemError'} if terminal['capacity_error'] else
                              {'type':'active', 'activeFlags':[]} if terminal['activity_confirmed'] else {'type':'idle'})
                    self.hub.native.cache.clear()
                    with patch.object(self.hub.native, 'available', return_value=True), \
                            patch.object(self.hub.native, 'call', return_value={'thread':metadata(status)}):
                        agent.update(self.hub.agent_observation(connection, agent, screen, screen, {'binding':BINDING}))
                    self.now+=1
                    self.recovery.observe(connection,agent)
                    if agent['activity_confirmed']: resumed+=1
                    if inputs.exists() and len(inputs.read_text().splitlines())==2 and agent['activity_confirmed']:
                        break
                time.sleep(.05)
            self.assertTrue(inputs.exists(),repr((bridge.list_agents(connection),bridge.capture(connection,pane,0))))
            self.assertEqual([json.loads(line) for line in inputs.read_text().splitlines()],['continue','continue'])
            self.assertTrue(agent['activity_confirmed']); self.assertGreater(resumed,1)
            with patch.object(bridge,'retry_capacity') as send:
                self.now+=100; self.recovery.observe(connection,agent); send.assert_not_called()
        finally:
            bridge.run(connection,['kill-server'])


if __name__=='__main__': unittest.main()
