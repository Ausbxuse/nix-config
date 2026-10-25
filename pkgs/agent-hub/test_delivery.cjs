const {test}=require('node:test');
const assert=require('node:assert/strict');
const {Store,apply}=require('./static/delivery.js');
class Storage{
  constructor(){this.items=new Map();}
  get length(){return this.items.size;}
  key(i){return [...this.items.keys()][i];}
  getItem(key){return this.items.get(key)||null;}
  setItem(key,value){this.items.set(key,value);}
  removeItem(key){this.items.delete(key);}
}
const entry=(id=1,kind='agent')=>({request_id:'web-fixture-request-'+id,kind,agent_id:'host:agent',created:100+id,text:'Compare checkpoints',status:'sending',request:{type:kind==='queue'?'queue':'send',agent_id:'host:agent',command_id:'web-fixture-request-'+id,message:'Compare checkpoints'}});
test('image receipts preserve uploaded identities but never temporary blob URLs',()=>{
  const storage=new Storage(),store=new Store(storage,()=>1000),value={...entry(),attachments:[
    {id:'a'.repeat(32),host:'worker',name:'screen.png',url:'blob:temporary',blob:'private'},
    {id:'b'.repeat(32),url:'blob:unsent',name:'draft.png'}
  ]};
  store.save(value);
  const restored=store.load()[0];
  assert.equal(restored.attachments.length,1);assert.equal(restored.attachments[0].id,'a'.repeat(32));
  assert.equal(restored.attachments[0].url,undefined);assert.equal(restored.attachments[0].blob,undefined);
});
test('reload retains exact request, result and target without transcript copies',()=>{
  const storage=new Storage(),store=new Store(storage,()=>1000),value={...entry(),baseline:'private transcript',requestTimer:123};
  assert.equal(store.save(value),true);
  const restored=new Store(storage,()=>1000).load()[0];
  assert.deepEqual(restored.request,value.request);assert.equal(restored.agent_id,value.agent_id);
  assert.equal(restored.baseline,undefined);assert.equal(restored.requestTimer,undefined);
  apply(restored,{status:'complete',result:{ok:true}});store.save(restored);
  assert.equal(new Store(storage,()=>1000).load()[0].status,'sent');
});
test('uncertain and unknown outcomes remain recoverable, not delivered',()=>{
  for(const status of ['sending','unknown','uncertain']){
    const value=entry();apply(value,{status});assert.equal(value.status,'uncertain');assert.equal(value.request.message,'Compare checkpoints');
  }
  const queued=entry(2,'queue');apply(queued,{status:'complete',result:{delivery:'queued'}});assert.equal(queued.status,'queued');
  const idle=entry(3,'queue');apply(idle,{status:'complete',result:{delivery:'sent'}});assert.equal(idle.status,'sent');
  const supervisor=entry(4,'supervisor');apply(supervisor,{status:'complete',result:{job_id:'job-1'}});assert.equal(supervisor.job_id,'job-1');assert.equal(supervisor.confirmed,true);
});
test('stale tabs cannot overwrite a delivered receipt with a late error',()=>{
  const storage=new Storage(),a=new Store(storage,()=>1000),b=new Store(storage,()=>1000),value=entry();
  a.save(value);const stale=b.load()[0];a.save({...value,status:'sent',confirmed:true});b.save({...stale,status:'uncertain',error:'timeout'});
  assert.equal(a.load()[0].status,'sent');assert.equal(a.load()[0].confirmed,true);assert.equal(a.load()[0].error,'');
});
test('dismissing an interrupted request survives reload and stale tabs without claiming delivery',()=>{
  const storage=new Storage(),a=new Store(storage,()=>1000),b=new Store(storage,()=>1000),value={...entry(1,'queue'),status:'uncertain'};
  a.save(value);const stale=b.load()[0];a.save({...value,dismissed:true});
  b.save({...stale,error:'Interrupted'});
  const restored=a.load()[0];
  assert.equal(restored.dismissed,true);assert.equal(restored.confirmed,false);assert.equal(restored.status,'uncertain');
  assert.deepEqual(restored.request,value.request);
  apply(restored,{status:'complete',result:{delivery:'queued'}});a.save(restored);
  assert.equal(a.load()[0].dismissed,true);assert.equal(a.load()[0].status,'queued');
});
test('private storage is bounded, expires old receipts and tolerates corrupt records',()=>{
  const storage=new Storage(),store=new Store(storage,()=>1000);
  storage.setItem('hub-drafts','keep');storage.setItem('hub-delivery-v1:broken','{');
  for(let i=1;i<=200;i++)store.save(entry(i));store.prune();
  assert.equal(store.load().length,160);assert.equal(store.load()[0].created,141);assert.equal(storage.getItem('hub-drafts'),'keep');
  const expired=new Store(storage,()=>31*86400);expired.prune();assert.deepEqual(expired.load(),[]);assert.equal(storage.length,1);
  assert.deepEqual(new Store(null).load(),[]);assert.equal(new Store(null).save(entry()),false);
});
