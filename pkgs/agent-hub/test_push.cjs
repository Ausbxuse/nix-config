const {test}=require('node:test'),assert=require('node:assert/strict');
const {status}=require('./static/push.js');
const {route}=require('./static/push.js');
test('notification routes survive query links and retain legacy fragments',()=>{
  const target='gpu-004:%3:123:@3:456';
  for(const separator of ['?','#']){
    const result=route('https://hub.test/'+separator+new URLSearchParams({updates:'1',agent:target,notice:'abc_123'}));
    assert.equal(result.agent,target);assert.equal(result.notice,'abc_123');
  }
  assert.equal(route('https://hub.test/'),null);
  assert.equal(route('https://hub.test/?updates=1&notice=new#updates=1&notice=old').notice,'new');
});
const snapshot=()=>({server_time:1000,assistant:{attention:{mode:'normal'}},watch:{eligible:true,enabled:true,checked_at:999,push_available:true,push_status:{checked_at:999,subscribers:1,last_accepted_at:990}}});
test('a configured relay alone never claims notifications are on',()=>{
  const s=snapshot();s.watch.push_status={};assert.equal(status(s).state,'checking');
  s.watch.push_status={checked_at:999,subscribers:0};assert.equal(status(s).label,'No devices');
  s.watch.push_status={checked_at:800,subscribers:1};assert.equal(status(s).state,'error');
  s.watch.push_status={checked_at:999,subscribers:1,error:'offline'};assert.equal(status(s).state,'error');
  assert.equal(status(snapshot(),{connected:false}).label,'Status unavailable');
  assert.equal(status(snapshot(),{elapsed:30}).label,'Watch reconnecting');
});
test('confirmed enrollment is distinct from pause, focus, quiet, and watch failure',()=>{
  const s=snapshot();assert.equal(status(s).state,'on');assert.match(status(s).help,/1 device.*unseen/);
  s.assistant.attention.mode='focus';assert.equal(status(s).label,'Focused alerts');
  s.assistant.attention.mode='quiet';assert.equal(status(s).state,'paused');
  s.watch.enabled=false;assert.equal(status(s).label,'Alerts paused');
  s.watch.enabled=true;s.watch.error='Offline';assert.equal(status(s).label,'Watch reconnecting');
  s.watch.eligible=false;assert.equal(status(s).label,'On controller');
});
test('seen suppression and provider acceptance are explicit, without claiming phone delivery',()=>{
  const s=snapshot();s.watch.push_latest={push_state:'observed'};
  assert.equal(status(s).state,'on');assert.match(status(s).outcome,/seen; push skipped/);
  s.watch.push_latest.push_state='sent';assert.match(status(s).outcome,/accepted by the push service/);
  assert.equal(status(s).accepted,990);
  s.watch.push_latest.push_state='no_device';assert.match(status(s).outcome,/at that time/);
});
