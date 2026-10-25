const {test}=require('node:test'),assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process');
const {apply}=require('./static/updates.js');
const {firstNew,digest}=require('./static/reading.js');

test('Python deltas reconstruct edited, inserted, reordered and removed messages with Unicode',()=>{
  const cases=[
    [{items:[{id:'a',text:'before'},{id:'b',text:'last'}]}, {items:[{id:'a',text:'edited'},{id:'new',text:'insert'},{id:'b',text:'last changed'}]}],
    [{items:[{id:'a'},{id:'b'},{id:'c'}]}, {items:[{id:'c'},{id:'a'}]}],
    [{items:[]}, {items:[{id:'new',text:'hello'}]}],
    [{items:[{id:'a'}],remove:true}, {items:[],add:{value:3}}],
    [{text:'🐱 '+ 'old '.repeat(100)+' 🎉'}, {text:'🐱 '+ 'old '.repeat(40)+'new'+ 'old '.repeat(50)+' 🎉'}],
    [{text:'prefix '.repeat(90)}, {text:'prefix '.repeat(90)+'a new final paragraph'}]
  ];
  const result=spawnSync('python3',['-c',"import json,sys; from updates import changes; print(json.dumps([changes(a,b) for a,b in json.load(sys.stdin)]))"],{cwd:__dirname,input:JSON.stringify(cases),encoding:'utf8'});
  assert.equal(result.status,0,result.stderr);
  JSON.parse(result.stdout).forEach((changes,index)=>{
    const [before,after]=cases[index],saved=structuredClone(before);
    assert.deepEqual(apply({version:'old',value:before},{base:'old',changes}),after);
    assert.deepEqual(before,saved,'a UI update cannot mutate the cached base');
  });
  assert.throws(()=>apply({version:'new',value:{}},{base:'old',changes:[]}),/expired/);
});

test('New begins at appended or completed messages, never a guessed position after history rotation',()=>{
  const messages=[{id:'1',role:'user',text:'Check this'},{id:'2',role:'assistant',text:'First paragraph'}];
  const record={seen:{id:'assistant:2',digest:digest(messages[1])}};
  assert.equal(firstNew(messages,record),null);
  assert.equal(firstNew([...messages,{id:'3',role:'assistant',text:'New result'}],record),'assistant:3');
  assert.equal(firstNew([messages[0],{...messages[1],text:'First paragraph. Final result.'}],record),'assistant:2');
  assert.equal(firstNew([{id:'4',role:'assistant',text:'Rotated history'}],record),null);
});
