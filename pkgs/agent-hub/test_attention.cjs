const {test}=require('node:test');
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const {Attention,source,pendingAgents,sameTurn}=require('./static/attention.js');
const wait=()=>new Promise(resolve=>setTimeout(resolve,25));
const item={id:'handoff',agent_id:'host:agent',version:'done:1000'};

test('turn identity accepts the server float representation but rejects a different turn',()=>{
  assert(sameTurn({...item,version:'done:1000.0'},{state:'done',changed_at:1000}));
  assert(!sameTurn(item,{state:'done',changed_at:1001}));
  assert(!sameTurn(item,{state:'running',changed_at:1000}));
});

test('attention requires sustained visibility of the same handoff, never marks handled',async()=>{
  let value={item,visible:true};const sent=[];
  const attention=new Attention({current:()=>value,send:async data=>sent.push(data),delay:10});
  attention.sync();value={item,visible:false};await wait();assert.equal(sent.length,0);
  value={item,visible:true};attention.sync();value={item:{...item,version:'done:2000'},visible:true};await wait();assert.equal(sent.length,0);
  attention.sync();await wait();assert.equal(sent.length,1);assert.equal(sent[0].attention.version,'done:2000');
  assert.equal(sent[0].attention.mode,'seen');assert(!('handle' in sent[0]));
  attention.sync();await wait();assert.equal(sent.length,1);attention.close();
});

test('completed speech records its exact handoff once and a failed receipt can retry',async()=>{
  let fail=true;const sent=[];
  const attention=new Attention({current:()=>null,send:async data=>{if(fail)throw Error('Offline');sent.push(data);}});
  assert.equal(await attention.record(item,'heard'),false);
  fail=false;assert.equal(await attention.record(item,'heard'),true);await attention.record(item,'heard');
  assert.deepEqual(sent,[{attention:{...item,mode:'heard'}}]);attention.close();
});

test('an update link only opens the exact saved reply, including Unicode text',async()=>{
  const message={id:'reply',role:'assistant',text:'🧪 Saved both traces.'};
  const target={message_id:'reply',message_digest:crypto.createHash('sha256').update(message.text).digest('hex')};
  assert.equal(await source(target,[message]),message);
  assert.equal(await source(target,[{...message,text:message.text+' Correction.'}]),null);
  assert.equal(await source(target,[{...message,id:'another'}]),null);
  assert.equal(await source(target,[{...message,role:'user'}]),null);
});

const pendingFixture=()=>({server_time:1000,agents:[
  {id:'done',state:'done',changed_at:900,connection_online:true,needs_attention:true},
  {id:'waiting',state:'waiting',changed_at:901,connection_online:true,needs_attention:true},
  {id:'blocked',state:'blocked',changed_at:902,connection_online:true,needs_attention:true},
  {id:'working',state:'running',changed_at:903,connection_online:true,needs_attention:false}
],handoffs:{items:[
  {agent_id:'done',version:'done:900.0',deferred_until:0},
  {agent_id:'waiting',version:'waiting:901',deferred_until:0},
  {agent_id:'blocked',version:'blocked:902',deferred_until:0}
]}});

test('list attention clears after reading, without marking the handoff reviewed',()=>{
  const snapshot=pendingFixture();
  assert.deepEqual([...pendingAgents(snapshot)],['done','waiting','blocked']);
  snapshot.handoffs.items[0].seen=950;
  assert.deepEqual([...pendingAgents(snapshot)],['waiting','blocked']);
  assert.equal(snapshot.handoffs.items.length,3,'Reading leaves the handoff available for review');
  snapshot.agents[0].changed_at=990;
  snapshot.handoffs.items[0]={agent_id:'done',version:'done:990'};
  assert(pendingAgents(snapshot).has('done'),'A new response needs attention again');
  snapshot.handoffs.items=[];
  assert.equal(pendingAgents(snapshot).size,0,'Transient terminal status is not a settled handoff');
});

test('reviewing, deferring, resuming, recovery and offline status clear list attention',()=>{
  for(const patch of [
    {acknowledged:true},{activity_confirmed:true},{state:'running'},
    {recovery:{status:'retrying'}},{recovery:{status:'checking'}},{recovery:{status:'waiting'}},
    {connection_online:false},{changed_at:950}
  ]){
    const snapshot=pendingFixture();Object.assign(snapshot.agents[0],patch);
    assert.equal(pendingAgents(snapshot).has('done'),false,JSON.stringify(patch));
    assert(pendingAgents(snapshot).has('waiting'));
  }
  const snapshot=pendingFixture();snapshot.handoffs.items[0].deferred_until=1100;
  assert.equal(pendingAgents(snapshot).has('done'),false);
  snapshot.server_time=1100;
  assert(pendingAgents(snapshot).has('done'),'The reminder returns when Later expires');
  snapshot.handoffs.items[0].handled=1100;
  assert.equal(pendingAgents(snapshot).has('done'),false);
});

test('attention does not reorder agents and can read an older snapshot without handoffs',()=>{
  const snapshot=pendingFixture(),order=snapshot.agents.map(agent=>agent.id);
  snapshot.handoffs.items.reverse();pendingAgents(snapshot);
  assert.deepEqual(snapshot.agents.map(agent=>agent.id),order);
  delete snapshot.handoffs;
  assert.deepEqual([...pendingAgents(snapshot)],['done','waiting','blocked']);
  assert.equal(pendingAgents(null).size,0);
});

test('a stopped agent remains actionable when its last terminal state was idle',()=>{
  const snapshot={server_time:1000,agents:[{id:'stopped',state:'idle',changed_at:900,live:false,connection_online:true,needs_attention:false}],
    handoffs:{items:[{agent_id:'stopped',kind:'exited',version:'idle:900'}]}};
  assert.deepEqual([...pendingAgents(snapshot)],['stopped']);
});
