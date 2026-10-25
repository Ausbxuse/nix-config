const test = require('node:test');
const assert = require('node:assert/strict');
const {nextHandoff}=require('./static/routing.js');

test('completed state resolves only its exact reply before the detailed job arrives',()=>{
  const {withReply}=require('./static/routing.js');
  const job={request_id:'one',context_agent_id:'a',status:'complete'};
  const wrong=[{role:'assistant',request_id:'two',context_agent_id:'a',text:'Wrong request.'},
    {role:'assistant',request_id:'one',context_agent_id:'b',text:'Wrong context.'},
    {role:'user',request_id:'one',context_agent_id:'a',text:'Not a reply.'}];
  assert.equal(withReply(job,wrong),job);
  const exact={role:'assistant',request_id:'one',context_agent_id:'a',text:'The exact reply.'};
  assert.equal(withReply(job,[exact,...wrong]).reply,exact.text);
  assert.equal(withReply({...job,status:'running'},[exact]).reply,undefined);
  assert.equal(withReply({...job,reply:'Authoritative job.'},[exact]).reply,'Authoritative job.');
});

test('related reviews stay together without outranking priority, urgency or an explicit pass',()=>{
  const agents=[{id:'a',cwd:'/research/lap'},{id:'b',cwd:'/research/teleop'},{id:'c',cwd:'/research/lap/'},{id:'d',cwd:'/research/lap'}];
  const items=['a','b','c','d'].map(id=>({id:'handoff-'+id,agent_id:id,kind:'done',preferred:false}));
  assert.equal(nextHandoff(items,agents,'a').agent_id,'c');
  assert.equal(nextHandoff(items,agents,'a',['handoff-c']).agent_id,'d');
  assert.equal(nextHandoff(items,agents,'a',['handoff-c','handoff-d']).agent_id,'b');
  const urgent=[{...items[1],kind:'waiting'},items[0],items[2]];
  assert.equal(nextHandoff(urgent,agents,'a').agent_id,'b');
  const preferred=[{...items[1],preferred:true},items[0],items[2]];
  assert.equal(nextHandoff(preferred,agents,'a').agent_id,'b');
  assert.equal(nextHandoff(items,agents.map(a=>({...a,cwd:'/home/user'})),'a').agent_id,'b');
  assert.equal(nextHandoff([],agents,'a'),null);
  const worktrees=agents.map(agent=>({...agent,cwd:'/separate/'+agent.id,project_key:['a','c','d'].includes(agent.id)?'same-repo':'other-repo'}));
  assert.equal(nextHandoff(items,worktrees,'a').agent_id,'c');
});
const { supervisorRequest, endCallRequested, selectionTarget, clarificationFor } = require('./static/routing.js');

test('hang-up requests accept conversational wording but not mentions, negations or agent work', () => {
  for (const text of ['ARIA, hang up.', 'Supervisor, hang up.', 'supervisor, please hang up', 'Hang up', 'hangup',
    'Can you please hang up for me?', 'end the call', 'stop this voice call', 'turn off the mic',
    'Supervisor: could you turn my microphone off, please?', 'stop the mic', 'close our call now']) {
    assert.equal(endCallRequested(text), true, text);
  }
  for (const text of ['Do not hang up', "don't end the call", 'Can you add a hang up button?',
    'Tell the agent to end the call', 'When it finishes, hang up', 'Hang up and delete the logs',
    'The mic should stop recording', '"hang up"', 'mute', 'stop', '']) {
    assert.equal(endCallRequested(text), false, text);
  }
});

test('supervisor address works for text and speech without matching quoted or embedded text', () => {
  for (const prefix of ['ARIA, ', 'aria: ', '  ARIA，', 'aria ', 'Supervisor, ', 'supervisor: ', '  SUPERVISOR，', 'supervisor ']) {
    assert.equal(supervisorRequest(prefix + 'please let me talk to the LAP agent'), 'please let me talk to the LAP agent');
  }
  assert.equal(supervisorRequest('supervisor,'), '');
  for (const text of ['Ask the supervisor about LAP', 'arias, check LAP', 'aria-label', 'Ask ARIA about LAP', 'supervisors, check LAP', '"supervisor, switch to LAP"', '```\nsupervisor, LAP\n```']) {
    assert.equal(supervisorRequest(text), null);
  }
});

test('ambiguity belongs only to its requesting tab and expires before it can steer later replies', () => {
  const agents = [{ id: 'gpu:lap', connection_online: true, live: true }, { id: 'gpu:checkpoints', connection_online: false, live: true }];
  const request = { id: 'mine', context: 'razy:nix', revision: 3 };
  const job = { request_id: 'mine', status: 'complete', select_agent_id: 'gpu:lap', clarification_id: 'choice', clarification_expires: 500,
    agent_choices: [{ agent_id: 'gpu:lap', reason: 'Evaluate LAP' }, { agent_id: 'gpu:checkpoints', reason: 'Prepare checkpoints' }] };
  assert.equal(selectionTarget(job, request, 'razy:nix', 3, agents), null);
  assert.deepEqual(clarificationFor(job, request, 'razy:nix', 3, agents, 400).choices, job.agent_choices);
  assert.equal(clarificationFor({...job,clarification_purpose:'coordinate'}, request, 'razy:nix', 3, agents, 400).purpose,'coordinate');
  assert.equal(clarificationFor(job, request, 'razy:nix', 3, agents, 400).purpose,'navigate');
  for (const change of [{ request_id: 'other-browser' }, { status: 'failed' }, { status: 'running' }, { clarification_id: '' }, { clarification_expires: 399 }, { agent_choices: [] }]) {
    assert.equal(clarificationFor({ ...job, ...change }, request, 'razy:nix', 3, agents, 400), null);
  }
  assert.equal(clarificationFor(job, undefined, 'razy:nix', 3, agents, 400), null);
  assert.equal(clarificationFor(job, request, 'razy:nix', 4, agents, 400), null);
  assert.equal(clarificationFor(job, request, 'other-context', 3, agents, 400), null);
  assert.equal(clarificationFor({ ...job, agent_choices: [job.agent_choices[0], job.agent_choices[0]] }, request, 'razy:nix', 3, agents, 400), null);
});

test('selection applies only to this request, before any later manual navigation', () => {
  const agent = { id: 'gpu:lap', connection_online: true, live: true };
  const request = { id: 'mine', context: 'razy:nix', revision: 3 };
  const job = { request_id: 'mine', status: 'complete', select_agent_id: agent.id };
  assert.equal(selectionTarget(job, request, 'razy:nix', 3, [agent]), agent);
  for (const change of [{ request_id: 'another-browser' }, { status: 'running' }, { status: 'failed' }, { select_agent_id: 'invented' }, { select_agent_id: '' }]) {
    assert.equal(selectionTarget({ ...job, ...change }, request, 'razy:nix', 3, [agent]), null);
  }
  assert.equal(selectionTarget(job, undefined, 'razy:nix', 3, [agent]), null);
  assert.equal(selectionTarget(job, request, 'razy:nix', 4, [agent]), null);
  assert.equal(selectionTarget(job, request, 'razy:other', 3, [agent]), null);
  for (const change of [{ connection_online: false }, { live: false }]) {
    assert.equal(selectionTarget(job, request, 'razy:nix', 3, [{ ...agent, ...change }]), null);
  }
});
