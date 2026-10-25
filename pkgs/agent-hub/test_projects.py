import unittest
from pathlib import Path
import tempfile
from projects import project_context, search_agents, ProjectIdentity


class ProjectIdentityTests(unittest.TestCase):
    def test_subdirectories_worktrees_and_remote_clones_share_verified_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);main=root/'main';git=main/'.git';git.mkdir(parents=True)
            (git/'config').write_text('[remote "origin"]\nurl = git@example.test:team/research.git\n')
            sub=main/'src'/'study';sub.mkdir(parents=True)
            branch=root/'branch';branch.mkdir();work=git/'worktrees'/'branch';work.mkdir(parents=True)
            (branch/'.git').write_text('gitdir: '+str(work));(work/'commondir').write_text('../..')
            clone=root/'clone';(clone/'.git').mkdir(parents=True)
            (clone/'.git'/'config').write_text('[remote "origin"]\nurl = https://user:private@example.test/team/research.git\n')
            identity=ProjectIdentity()
            expected=identity.key(main,'razy')
            self.assertEqual(identity.key(sub,'razy'),expected)
            self.assertEqual(identity.key(branch,'razy'),expected)
            self.assertEqual(identity.key(clone,'gpu'),expected)
            self.assertNotIn('private',expected)
            (clone/'.git'/'config').write_text('[remote "origin"]\nurl = https://example.test/team/another.git\n')
            self.assertNotEqual(ProjectIdentity().key(clone,'gpu'),expected)

    def test_home_and_unrelated_directories_do_not_infer_a_common_project(self):
        identity=ProjectIdentity()
        self.assertEqual(identity.key('/home/user','host'),'')
        self.assertEqual(identity.key('/','host'),'')
        self.assertNotEqual(identity.key('/nonexistent/study-a','host'),identity.key('/nonexistent/study-b','host'))
        self.assertNotEqual(identity.key('/nonexistent/study-a','host'),identity.key('/nonexistent/study-a','other'))


class ProjectTests(unittest.TestCase):
    def agent(self, id, cwd='/research', host='gpu', online=True):
        return dict(id=id,cwd=cwd,connection_id=host,connection_label=host,title=id,
                    state='running',connection_online=online,summary='Working')

    def test_goal_and_recent_results_keep_workstreams_separate(self):
        messages = [dict(id=str(i), role='user' if i == 0 else 'assistant', text='goal' if i == 0 else 'result '+str(i)) for i in range(20)]
        projects = project_context([self.agent('baseline'),self.agent('ablation')], lambda *a, **kw: {'messages':messages})
        self.assertEqual(len(projects),1)
        self.assertEqual(len(projects[0]['workstreams']),2)
        for stream in projects[0]['workstreams']:
            self.assertEqual(stream['messages'][0]['text'],'goal')
            self.assertEqual(stream['messages'][-1]['text'],'result 19')

    def test_hosts_do_not_merge_and_offline_does_not_fetch(self):
        calls=[]
        def preview(id, **kw): calls.append(id); return {'text':'terminal'}
        projects=project_context([self.agent('one'),self.agent('two',host='laptop',online=False)],preview)
        self.assertEqual(len(projects),2)
        self.assertEqual(calls,['one'])
        self.assertIn('offline', projects[1]['workstreams'][0]['unavailable'])

    def test_budget_is_shared_including_nonattention_projects(self):
        agents=[self.agent(str(i),cwd='/project'+str(i)) for i in range(10)]
        projects=project_context(agents,lambda *a,**kw:{'messages':[{'id':'1','role':'assistant','text':'x'*10000}]},budget=1000)
        messages=[m for p in projects for s in p['workstreams'] for m in s['messages']]
        self.assertEqual(len(messages),10)
        self.assertLessEqual(sum(len(m['text']) for m in messages),1000)
        self.assertTrue(all(m['truncated'] for m in messages))

    def test_lookup_finds_an_older_assignment_and_checkpoint_aliases(self):
        messages = [dict(role='assistant', text='Unrelated update '+str(i)) for i in range(30)]
        messages[12] = dict(role='user', text='Nemo prepared normalizer_arms: task-quantile and task-meanstd. Try both.')
        messages[13] = dict(role='assistant', text='Both fits are running: task-meanstd on Fluence18 and task-quantile on Fluence19.')
        agents = [self.agent('other-'+str(i)) for i in range(13)] + [self.agent('lap')]
        calls = []
        def preview(id, **kwargs):
            calls.append(id)
            return {'messages': messages if id == 'lap' else [], 'text': ''}
        result = search_agents(agents, preview, 'which agent is preparing the stdmean vs quantile ckpts by nemo?')
        self.assertEqual(result['searched_histories'], 14)
        self.assertEqual(result['matches'][0]['title'], 'lap')
        self.assertIn('meanstd', result['matches'][0]['matched_terms'])
        self.assertIn('Fluence18', str(result['matches'][0]['messages']))
        self.assertEqual(len(calls), 14)

    def test_lookup_preserves_matching_middle_excerpt_within_budget(self):
        text = 'irrelevant '*1000 + 'Nemo task-quantile and task-meanstd checkpoints are ready.' + ' unrelated'*1000
        result = search_agents([self.agent('lap')], lambda *a, **kw: {'messages': [{'role':'assistant','text':text}]},
                               'Which agent prepared Nemo stdmean quantile ckpts?', budget=240)
        excerpt = result['matches'][0]['messages'][0]
        self.assertIn('task-meanstd', excerpt['text'])
        self.assertIn('checkpoints are ready', excerpt['text'])
        self.assertTrue(excerpt['truncated'])
        self.assertLessEqual(len(excerpt['text']), 240)

    def test_lookup_prefers_task_history_to_a_terminal_quoting_the_question(self):
        question = 'Which agent is preparing stdmean vs quantile ckpts by Nemo?'
        def preview(id, **kw):
            return {'text':'The supervisor failed to answer "'+question+'"'} if id == 'hub-ui' else {
                'messages':[{'role':'assistant','text':'Nemo task-meanstd and task-quantile fits are running.'}]}
        result = search_agents([self.agent('hub-ui'), self.agent('lap')], preview, question)
        self.assertEqual(result['matches'][0]['title'], 'lap')
        self.assertIn('mention alone', result['matches'][1]['history_scope'])

    def test_lookup_reports_offline_and_unsearched_agents(self):
        calls = []
        def preview(id, **kw): calls.append(id); return {'text':'No match'}
        result = search_agents([self.agent('nemo', online=False), self.agent('other')], preview, 'Which agent runs Nemo?', limit=1)
        self.assertEqual(calls, [])
        self.assertEqual(result['searched_histories'], 0)
        self.assertEqual(result['unsearched_agents'], 1)
        self.assertIn('offline', result['unavailable'][0]['unavailable'])

    def test_latest_assignment_keeps_a_previous_topic_from_becoming_false_ownership(self):
        messages = [dict(role='user', text='Prepare the LAP checkpoints.'),
                    dict(role='assistant', text='LAP checkpoint work has started.'),
                    dict(role='user', text='Stop that work. Evaluate the RTC baseline instead.'),
                    dict(role='assistant', text='The baseline evaluation is running.')]
        result = search_agents([self.agent('research')], lambda *a, **kw: {'messages': messages}, 'Switch to LAP')
        evidence = result['matches'][0]['messages']
        self.assertEqual([m['text'] for m in evidence], [m['text'] for m in messages])
        self.assertEqual(evidence[-2]['context'], 'Latest available instruction')
        self.assertEqual(evidence[-1]['context'], 'Latest available reply')

if __name__=='__main__': unittest.main()
