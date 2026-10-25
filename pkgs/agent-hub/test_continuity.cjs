const {test}=require('node:test'),assert=require('node:assert/strict');
const {Publisher,checkpoint}=require('./static/continuity.js');
const {position,restore}=require('./static/reading.js');
const {Drafts}=require('./static/drafts.js');
const value={agent_id:'fixture:1',mode:'agent',draft:'A draft',bookmark:{top:400,bottom:false,anchor:{id:'assistant:1',offset:-50},at:1}};

test('checkpoints contain position and draft without capture-time churn',()=>{
  assert.deepEqual(checkpoint(value),checkpoint({...value,bookmark:{...value.bookmark,at:2}}));
  assert.equal(value.bookmark.at,1);
});

test('coalesced saves stay ordered through a slow request and an emptied draft',async()=>{
  const sent=[];let release;
  const publisher=new Publisher(async value=>{sent.push(value);if(sent.length===1)await new Promise(resolve=>release=resolve);},10000);
  publisher.stage(value);const running=publisher.flush();
  publisher.stage({...value,draft:'Second draft'});publisher.stage({...value,draft:''});
  release();await running;await publisher.flush();
  assert.deepEqual(sent.map(item=>[item.sequence,item.draft]),[[1,'A draft'],[2,'']]);
  publisher.stage({...value,draft:''});await publisher.flush();assert.equal(sent.length,2);
});

test('offline saves retain only the latest checkpoint and retry on an explicit flush',async()=>{
  let online=false;const sent=[];
  const publisher=new Publisher(async value=>{if(!online)throw Error('Offline');sent.push(value);},10000);
  publisher.stage(value);await publisher.flush();assert.equal(sent.length,0);
  publisher.stage({...value,draft:'Revised while offline'});online=true;await publisher.flush();
  assert.equal(sent.length,1);assert.equal(sent[0].draft,'Revised while offline');assert.equal(sent[0].sequence,2);
});

test('cross-width reading position follows the same paragraph and relative line',()=>{
  const make=(top,height,content)=>({textContent:content,classList:{contains:()=>false},closest:()=>null,getBoundingClientRect:()=>({top,bottom:top+height,height})});
  const paragraph=make(50,200,'Paragraph being read'),later=make(300,200,'Later paragraph');
  const node={dataset:{messageId:'assistant:1'},getBoundingClientRect:()=>({top:-800,bottom:500}),querySelector:()=>({children:[paragraph,later]})};
  const container={scrollTop:900,scrollHeight:2000,clientHeight:300,getBoundingClientRect:()=>({top:100})};
  const record=position(container,[node]);assert.equal(record.anchor.block.index,0);assert.equal(record.anchor.block.fraction,.25);
  paragraph.getBoundingClientRect=()=>({top:800,bottom:1200,height:400});container.scrollTop=0;
  restore(container,record,[node]);assert.equal(container.scrollTop,800-100+.25*400);
  paragraph.textContent='Edited paragraph';container.scrollTop=0;
  restore(container,record,[node]);assert.equal(container.scrollTop,0,'Changed paragraph falls back to message anchor');
});

test('draft edits in different tabs merge and each tab reloads its own version',()=>{
  const storage=()=>{const data=new Map();return {getItem:key=>data.get(key),setItem:(key,value)=>data.set(key,value)};};
  const shared=storage(),sessionA=storage(),sessionB=storage();
  const first=new Drafts(shared,sessionA,'drafts'),second=new Drafts(shared,sessionB,'drafts');
  first.save({one:'First tab draft'});second.save({two:'Second tab draft'});first.save({one:'First tab revised'});
  assert.deepEqual(JSON.parse(shared.getItem('drafts')),{one:'First tab revised',two:'Second tab draft'});
  second.save({one:'Other draft for the same agent',two:'Second tab draft'});
  assert.equal(new Drafts(shared,sessionA,'drafts').value.one,'First tab revised');
  assert.equal(new Drafts(shared,sessionB,'drafts').value.one,'Other draft for the same agent');
  first.save({});
  assert.equal(JSON.parse(shared.getItem('drafts')).one,'Other draft for the same agent','Clearing an old draft erased a newer tab draft');
  assert.equal(new Drafts(shared,sessionA,'drafts').value.one,'','The sent draft returned after reload');
});
