const { test } = require('node:test');
const assert = require('node:assert/strict');
const activity = require('./static/activity.js');

const agent = { live: true, connection_online: true, state: 'running' };
test('quiet output stops the working animation without claiming the process is stuck', () => {
  const status = activity.agent({ agent, observation: { changed: 10, started: 5 }, connected: true, now: 101 });
  assert.equal(status.kind, 'quiet');
  assert.match(status.note, /still reports working/);
  assert.doesNotMatch(status.title, /stuck|failed/i);
  assert.equal(activity.agent({ agent, observation: { changed: 100 }, connected: true, now: 101 }).kind, 'active');
});
test('connection failure and approval prompts override a generic working indicator', () => {
  assert.equal(activity.agent({ agent, connected: false, now: 101 }).kind, 'offline');
  assert.equal(activity.agent({ agent: { ...agent, state: 'waiting' }, connected: true, now: 101 }).title, 'Needs your input');
});
test('an unverified turn does not label a connected live agent as unavailable', () => {
  const status = activity.agent({agent:{...agent,state:'unknown'},connected:true,now:101});
  assert.equal(status.kind,'quiet');
  assert.equal(status.title,'Checking turn status');
  assert.equal(status.note,'');
  assert.equal(activity.agent({agent:{...agent,state:'unknown',connection_online:false},connected:true,now:101}).kind,'offline');
});
test('delivered input has an explicit waiting state, then warns when startup is not observed', () => {
  const value = { agent: { ...agent, state: 'done' }, receipt: { status: 'sent', created: 5 }, connected: true, now: 10 };
  assert.equal(activity.agent(value).title, 'Waiting for the agent');
  assert.equal(activity.agent({ ...value, now: 36 }).kind, 'quiet');
  assert.equal(activity.agent({ ...value, receipt: { ...value.receipt, responseSeen: true } }).kind, 'idle');
});
test('supervisor stages have elapsed time and completed jobs stop animating', () => {
  const value = { connected: true, available: true, job: { status: 'running', phase: 'thinking', started_at: 5 }, now: 10 };
  assert.equal(activity.supervisor(value).title, 'Thinking');
  assert.equal(activity.supervisor({ ...value, now: 96 }).kind, 'active');
  assert.equal(activity.supervisor({ ...value, job: null, busy: true, since: 5, now: 96 }).kind, 'quiet');
  assert.equal(activity.supervisor({ ...value, job: { status: 'complete' } }), null);
  assert.equal(activity.supervisor({ ...value, connected: false }).kind, 'offline');
});

test('confirmed CLI activity stays synced even when output is quiet', () => {
  const status = activity.agent({agent:{...agent,activity_confirmed:true,activity_phase:'Waiting for terminal'},observation:{changed:1,started:10},connected:true,now:200});
  assert.equal(status.kind,'active');
  assert.equal(status.title,'Waiting for terminal');
  assert.equal(status.since,10);
});

test('capacity retries stay distinct from real work and stop overriding confirmed activity', () => {
  const recovering = {...agent,state:'blocked',recovery:{kind:'capacity',status:'retrying',attempts:2,next_retry:105}};
  const status = activity.agent({agent:recovering,connected:true,now:101});
  assert.match(status.title,/retry in 4s/);assert.equal(status.kind,'active');
  assert.equal(activity.agent({agent:{...recovering,state:'running',activity_confirmed:true},connected:true,now:101}).title,'Working');
  assert.equal(activity.agent({agent:{...recovering,recovery:{kind:'capacity',status:'paused'}},connected:true,now:101}).kind,'attention');
});
