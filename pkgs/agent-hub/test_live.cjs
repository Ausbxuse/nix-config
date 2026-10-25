const {test}=require('node:test');
const assert=require('node:assert/strict');
global.HubUpdates=require('./static/updates.js');
const {Client}=require('./static/live.js');
const {Tabs,microphone}=require('./static/live.js');
class Source {
  constructor(url){this.url=url;this.events={};this.closed=false;Source.all.push(this);}
  addEventListener(name,fn){this.events[name]=fn;}
  close(){this.closed=true;}
  emit(name,value){this.events[name]({data:JSON.stringify(value)});}
}
Source.all=[];
test('stream applies ordered reset and deltas, stale recipient cannot update current view',()=>{
  let query={active:true,agentId:'a',history:true},time=100;
  const seen=[];
  const client=new Client({query:()=>query,EventSource:Source,now:()=>time,onState:v=>seen.push(['state',v]),onPreview:(id,h,v)=>seen.push([id,v])});
  client.sync();const first=client.source;first.onopen();
  first.emit('reset',[
    {topic:['preview','a',true],packet:{version:'p1',value:{text:'First'}}},
    {topic:['state'],packet:{version:'s1',value:{agents:['a']}}}
  ]);
  assert.equal(seen[0][0],'state');assert.equal(client.healthy,true);
  first.emit('update',{topic:['preview','a',true],packet:{version:'p2',base:'p1',changes:[['set',['text'],'Complete']]}});
  assert.equal(seen.at(-1)[1].text,'Complete');
  query={...query,agentId:'b'};client.sync();assert.equal(first.closed,true);
  const count=seen.length;first.emit('update',{topic:['preview','a',true],packet:{version:'p3',value:{text:'Late old reply'}}});
  assert.equal(seen.length,count);
  query.active=false;client.sync();assert.equal(client.source,null);
});
test('disconnect enables fallback; replay preserves bases; invalid base forces clean reconnect',()=>{
  const client=new Client({query:()=>({active:true,history:true}),EventSource:Source,now:()=>100,onState:()=>{}});
  client.sync();const source=client.source;source.onopen();
  source.emit('reset',[{topic:['state'],packet:{version:'s1',value:{agents:[]}}}]);
  source.onerror();assert.equal(client.healthy,false);source.onopen();
  source.emit('update',{topic:['state'],packet:{version:'s2',base:'s1',changes:[['set',['agents'],['a']]]}});
  assert.equal(client.healthy,true);
  source.emit('update',{topic:['state'],packet:{version:'s3',base:'lost',changes:[]}});
  assert.equal(client.healthy,false);assert.equal(source.closed,true);
  client.sync();assert.notEqual(client.source,source);
});
test('manual refresh reconnects a healthy stream and rejects its old frames',()=>{
  const seen=[];
  const client=new Client({query:()=>({active:true,history:true}),EventSource:Source,onState:value=>seen.push(value)});
  client.sync();const old=client.source;
  old.emit('reset',[{topic:['state'],packet:{version:'s1',value:{text:'Old'}}}]);
  client.reconnect();assert(old.closed);assert.notEqual(client.source,old);assert.equal(client.bases.size,0);
  old.emit('update',{topic:['state'],packet:{version:'late',value:{text:'Late'}}});
  client.source.emit('reset',[{topic:['state'],packet:{version:'s2',value:{text:'New'}}}]);
  assert.deepEqual(seen,[{text:'Old'},{text:'New'}]);client.close();
});
test('state updates and pings cannot hide a stale or failed conversation subscription',()=>{
  let time=100;
  const query={active:true,agentId:'a',history:true};
  const client=new Client({query:()=>query,EventSource:Source,now:()=>time,onState:()=>{},onPreview:()=>{}});
  client.sync();let source=client.source;source.onopen();
  source.emit('reset',[{topic:['state'],packet:{version:'s1',value:{agents:['a']}}}]);
  assert.equal(client.healthy,false,'Missing initial preview must use polling');
  source.emit('update',{topic:['preview','a',true],packet:{version:'p1',value:{text:'Current'}}});
  assert.equal(client.healthy,true);
  time+=13000;
  source.emit('ping',{});
  source.emit('update',{topic:['state'],packet:{version:'s2',value:{agents:['a']}}});
  assert.equal(client.healthy,false);
  client.sync();assert(source.closed,'Stalled chat must reconnect even when the socket is open');
  source=client.source;
  source.emit('reset',[{topic:['state'],packet:{version:'s3',value:{agents:['a']}}},
    {topic:['preview','a',true],packet:{version:'p2',value:{text:'Recovered'}}}]);
  assert.equal(client.healthy,true);
  source.emit('update',{topic:['preview','a',true],error:true,packet:{version:'e1',value:{error:'Host unavailable'}}});
  assert.equal(client.healthy,false,'Preview errors must re-enable polling immediately');
  client.close();
});

test('switching to terminal requests a separate stream and carries the mode with updates',()=>{
  let query={active:true,agentId:'a',history:true};const seen=[];
  const client=new Client({query:()=>query,EventSource:Source,onState:()=>{},onPreview:(...args)=>seen.push(args)});
  client.sync();const old=client.source;
  query={...query,terminal:true};client.sync();assert.equal(old.closed,true);
  assert.deepEqual(JSON.parse(new URL(client.source.url,'http://test').searchParams.get('previews')),[['a',true,true]]);
  client.source.emit('reset',[{topic:['preview','a',true,true],packet:{version:'raw1',value:{text:'red',terminal:{ansi:'\x1b[31mred'}}}}]);
  assert.equal(seen[0][3],true);assert.equal(seen[0][2].terminal.ansi,'\x1b[31mred');client.close();
});

class Channel {
  static all=new Set();
  constructor(){Channel.all.add(this);}
  postMessage(value){for(const target of Channel.all)if(target!==this)queueMicrotask(()=>target.onmessage?.({data:structuredClone(value)}));}
  close(){Channel.all.delete(this);this.onmessage=null;}
}
const settle=()=>new Promise(resolve=>setTimeout(resolve,0));
test('two tabs share one stream, include both conversations and replace a closed leader',async()=>{
  Source.all=[];
  const seenA=[],seenB=[];
  const options=(id,seen)=>({query:()=>({active:true,agentId:id,history:true}),EventSource:Source,BroadcastChannel:Channel,onState:value=>seen.push(value),onPreview:()=>{}});
  const a=new Tabs(options('agent-a',seenA)),b=new Tabs(options('agent-b',seenB));
  a.id='a';b.id='b';a.sync();b.sync();await settle();await settle();
  assert.equal(a.leader,'a');assert.equal(b.leader,'a');
  const active=Source.all.filter(source=>!source.closed);assert.equal(active.length,1);
  const previews=JSON.parse(new URL(active[0].url,'http://example.test').searchParams.get('previews'));
  assert.deepEqual(previews,[['agent-a',true],['agent-b',true]]);
  active[0].onopen();active[0].emit('reset',[{topic:['state'],packet:{version:'s1',value:{agents:['a','b']}}}]);
  await settle();assert.deepEqual(seenA,seenB);assert.equal(b.healthy,false);
  active[0].emit('update',{topic:['preview','agent-b',true],packet:{version:'pb1',value:{text:'Current'}}});
  await settle();assert.equal(b.healthy,true);assert.equal(a.healthy,false);
  a.close();await settle();assert.equal(b.leader,'b');assert.equal(Source.all.filter(source=>!source.closed).length,1);
  b.close();
});
test('a follower watches its own chat, not other conversations or leader activity',async()=>{
  let time=100;
  const options=id=>({query:()=>({active:true,agentId:id,history:true}),now:()=>time,EventSource:Source,BroadcastChannel:Channel,onState:()=>{},onPreview:()=>{}});
  const a=new Tabs(options('agent-a')),b=new Tabs(options('agent-b'));a.id='a';b.id='b';a.sync();b.sync();await settle();await settle();
  const source=a.client.source;
  const emit=(topic,version,value)=>source.emit('update',{topic,packet:{version,value}});
  emit(['state'],'s1',{});emit(['preview','agent-b',true],'b1',{text:'Current'});await settle();assert.equal(b.healthy,true);
  time+=13000;
  emit(['state'],'s2',{});emit(['preview','agent-a',true],'a2',{text:'Another tab'});await settle();assert.equal(b.healthy,false);
  emit(['preview','agent-b',true],'b2',{text:'Recovered'});await settle();assert.equal(b.healthy,true);
  source.emit('update',{topic:['preview','agent-b',true],error:true,packet:{version:'e1',value:{error:'Unavailable'}}});
  await settle();assert.equal(b.healthy,false);
  a.close();b.close();
});
test('refresh in a follower reconnects the shared stream without closing either tab',async()=>{
  const seen=[];
  const options=id=>({query:()=>({active:true,agentId:id,history:true}),EventSource:Source,BroadcastChannel:Channel,onState:value=>seen.push(value),onPreview:()=>{}});
  const a=new Tabs(options('agent-a')),b=new Tabs(options('agent-b'));a.id='a';b.id='b';a.sync();b.sync();await settle();await settle();
  const old=a.client.source;b.reconnect();await settle();
  assert(old.closed);assert.notEqual(a.client.source,old);assert(Channel.all.has(a.channel));assert(Channel.all.has(b.channel));
  assert.equal(Source.all.filter(source=>!source.closed).length,1);
  a.client.source.emit('reset',[{topic:['state'],packet:{version:'fresh',value:{text:'Fresh'}}}]);
  await settle();assert.deepEqual(seen,[{text:'Fresh'},{text:'Fresh'}]);a.close();b.close();
});
test('microphone lock rejects a second call and releases when the first call ends',async()=>{
  const previous=Object.getOwnPropertyDescriptor(global,'navigator');let held=false;
  Object.defineProperty(global,'navigator',{configurable:true,value:{locks:{async request(_name,_options,callback){
    if(held)return callback(null);held=true;try{return await callback({});}finally{held=false;}
  }}}});
  try{
    const release=await microphone();assert.equal(typeof release,'function');
    assert.equal(await microphone(),null);
    release();await settle();const again=await microphone();assert.equal(typeof again,'function');again();await settle();
  }finally{if(previous)Object.defineProperty(global,'navigator',previous);else delete global.navigator;}
});

test('shared tabs retain independent conversation and terminal subscriptions for the same agent',async()=>{
  const seen=[];
  const options=terminal=>({query:()=>({active:true,agentId:'same',history:true,terminal}),EventSource:Source,BroadcastChannel:Channel,onState:()=>{},onPreview:(...args)=>seen.push(args)});
  const a=new Tabs(options(false)),b=new Tabs(options(true));a.id='a';b.id='b';a.sync();b.sync();await settle();await settle();
  assert.deepEqual(a.union().previews.sort((a,b)=>a.length-b.length),[['same',true],['same',true,true]]);
  a.client.source.emit('reset',[{topic:['preview','same',true,true],packet:{version:'raw',value:{terminal:{ansi:'\x1b[32mgreen'}}}}]);
  await settle();assert.equal(seen.length,2);assert(seen.every(args=>args[3]===true));a.close();b.close();
});
