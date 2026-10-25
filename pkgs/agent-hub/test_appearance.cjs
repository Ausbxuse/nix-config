const {test}=require('node:test');
const assert=require('node:assert/strict');
const {defaults,normalize,preferences,storageKey}=require('./static/appearance.js');
function memory(values={}) {
  const data=new Map(Object.entries(values));
  return {getItem:key=>data.get(key)??null,setItem:(key,value)=>data.set(key,value),removeItem:key=>data.delete(key)};
}
test('old motion choices survive the upgrade and reset does not touch drafts',()=>{
  const storage=memory({'hub-motion':'reduce','hub-drafts':'private draft'}),prefs=preferences(()=>storage);
  assert.equal(prefs.value.motion,'reduce');
  prefs.update({size:115,font:'sans'});
  assert.deepEqual(preferences(()=>storage).value,{...defaults,size:115,font:'sans',motion:'reduce'});
  assert.equal(storage.getItem('hub-motion'),null);
  prefs.reset();
  assert.deepEqual(preferences(()=>storage).value,defaults);
  assert.equal(storage.getItem('hub-drafts'),'private draft');
});
test('invalid or future preferences cannot inject arbitrary CSS values',()=>{
  for (const input of [null,[],false,{size:'130',font:'url(https://invalid)',contrast:'bad'}, {size:NaN}, {size:Infinity}]) assert.deepEqual(normalize(input),defaults);
  assert.equal(normalize({size:300}).size,130);
  assert.equal(normalize({size:-4}).size,85);
  assert.equal(normalize({size:113}).size,115);
  for (const raw of ['{broken',JSON.stringify({version:2,font:'sans',size:130}),'null']) assert.deepEqual(preferences(()=>memory({[storageKey]:raw})).value,defaults);
});
test('blocked or full browser storage does not stop live customization',()=>{
  const blocked=preferences(()=>{throw new Error('SecurityError');});
  assert.equal(blocked.saved,false);
  blocked.update({font:'sans',size:120});
  assert.equal(blocked.value.size,120);assert.equal(blocked.value.font,'sans');assert.equal(blocked.saved,false);
  assert.deepEqual(blocked.reset(),defaults);
  const full=preferences(()=>({...memory(),setItem(){throw new Error('QuotaExceededError');}}));
  full.update({contrast:'high'});assert.equal(full.value.contrast,'high');assert.equal(full.saved,false);
});
test('reload picks up another tab and cleared storage restores defaults',()=>{
  const storage=memory(),a=preferences(()=>storage),b=preferences(()=>storage);
  a.update({spacing:'compact',motion:'subtle'});
  assert.deepEqual(b.read(),a.value);
  storage.removeItem(storageKey);
  assert.deepEqual(b.read(),defaults);
  // Exposed snapshots are copies, not mutable application state.
  b.value.size=130;assert.equal(b.value.size,100);
});
