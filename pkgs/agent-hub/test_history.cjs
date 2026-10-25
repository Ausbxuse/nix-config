const {test}=require('node:test');
const assert=require('node:assert/strict');
const {groups,Window,Timeline,Preview,key}=require('./static/history.js');
const {outline}=require('./static/reader.js');
const message=(id,role='assistant',phase='',text='Message '+id)=>({id:String(id),role,phase,text});

const source={host:'gpu-004',session:'saved-session',generation:'version',anchor:'90',earlier:true};
const archived=(start,end,extra={})=>({...source,messages:Array.from({length:end-start},(_,i)=>message(start+i)),earlier:start>0,later:end<100,...extra});
test('a missing transcript preserves formatted messages and the archive cursor without freezing live status',()=>{
  const preview=new Preview(),messages=archived(90,100).messages;
  preview.update({messages,source:'original',conversation_id:'thread-a',history_source:source,facts:{version:'original'}});
  const capture={text:'unparsed terminal repaint',messages:[],observation:{state:'running'},queue:{version:'new'}};
  const held=preview.update(capture);
  assert.equal(held.messages,messages);assert.equal(held.history_source,source);assert(held.conversation_pending);
  assert.equal(held.text,capture.text);assert.equal(held.observation,capture.observation);assert.equal(held.queue,capture.queue);
  const resumed=preview.update({messages:[...messages,message(100)],conversation_id:'thread-a',history_source:source});
  assert.equal(resumed.messages.at(-1).id,'100');assert(!resumed.conversation_pending);
  preview.reset();assert.equal(preview.update(capture),capture,'another agent never inherits the cached transcript');
});
test('temporary index gaps preserve a cursor only for the same explicit conversation identity',()=>{
  const preview=new Preview(),timeline=new Timeline(),messages=archived(90,100).messages;
  const first=preview.update({messages,conversation_id:'thread-a',history_source:source});
  timeline.update(first.messages,first.history_source);
  const request=timeline.request('older');
  const next=preview.update({messages:[...messages,message(100)],conversation_id:'thread-a'});
  assert.equal(next.history_source,source);
  timeline.update(next.messages,next.history_source);assert(timeline.accept(request,archived(66,90)));
  const different=preview.update({messages:[message('new')],conversation_id:'thread-b'});
  assert.equal(different.history_source,undefined);
  timeline.update(different.messages,different.history_source);assert(!timeline.past);assert(!timeline.accept(request,archived(66,90)));
});
test('a terminal match ending at an older reply does not rewind the same conversation',()=>{
  const preview=new Preview(),messages=archived(90,100).messages;
  preview.update({messages,conversation_id:'thread-a',history_source:source});
  const older=preview.update({messages:messages.slice(0,4),conversation_id:'thread-a',history_source:source});
  assert.equal(older.messages.at(-1).id,'99');assert(older.conversation_pending);
  const reset=preview.update({messages:messages.slice(0,4),conversation_id:'thread-a',history_source:{...source,generation:'replacement'}});
  assert.equal(reset.messages.at(-1).id,'93');assert(!reset.conversation_pending);
  const empty=preview.update({messages:[],session_verified:true});
  assert.deepEqual(empty.messages,[]);assert.equal(empty.history_source,undefined);
});
test('live tails load earlier indexed pages, deduplicate overlap, and retain new output for Latest',()=>{
  const timeline=new Timeline();timeline.update(archived(90,100).messages,source);
  const request=timeline.request('older');assert.equal(request.parameters.message,'90');
  timeline.update(archived(90,101).messages,source); // A live append while the page is in flight.
  assert(timeline.accept(request,archived(66,91)));
  assert.deepEqual(timeline.messages.map(m=>m.id),Array.from({length:34},(_,i)=>String(66+i)));
  assert(timeline.past);assert(!timeline.later);assert(timeline.earlier);
  timeline.latest();assert.equal(timeline.messages.at(-1).id,'100');assert(!timeline.past);
});
test('history buffers stay bounded and can page through every record in both directions',()=>{
  const timeline=new Timeline({limit:48});timeline.update(archived(90,100).messages,source);
  const visited=new Set(timeline.messages.map(m=>m.id));
  while(timeline.earlier){
    const request=timeline.request('older'),end=Number(request.parameters.message);
    timeline.accept(request,archived(Math.max(0,end-24),end));
    assert(timeline.messages.length<=48);timeline.messages.forEach(m=>visited.add(m.id));
  }
  assert.equal(visited.size,100);assert(timeline.later);
  while(timeline.later){
    const request=timeline.request('newer'),start=Number(request.parameters.message)+1;
    timeline.accept(request,archived(start,Math.min(100,start+24)));
    assert(timeline.messages.length<=48);
  }
  assert.equal(timeline.messages.at(-1).id,'99');
});
test('session changes discard old history and stale requests cannot cross agents or generations',()=>{
  const timeline=new Timeline();timeline.update(archived(90,100).messages,source);
  const request=timeline.request('older');timeline.update([message('new')],{...source,generation:'changed'});
  assert.equal(timeline.accept(request,archived(66,90)),false);assert(!timeline.past);
  const current=timeline.request('older');assert.throws(()=>timeline.accept(current,archived(66,90)),/history changed/);
  timeline.update([message('new')]);assert.equal(timeline.request('older'),null);
});
test('history has a text budget and retains exact asset ownership and partial-message markers',()=>{
  const timeline=new Timeline({characters:1000});timeline.update([message('90')],source);
  const page=archived(66,90);page.messages[0]={...message('66','assistant','','x'.repeat(1100)),asset_agent_id:'gpu-004:archive:session:66',part:{start:0,end:1100,total:3000}};
  timeline.accept(timeline.request('older'),page);
  assert.equal(timeline.messages.length,1);assert.equal(timeline.messages[0].asset_agent_id,'gpu-004:archive:session:66');
  assert.equal(timeline.messages[0].part.total,3000);assert(timeline.later);
});

test('explicit progress folds separately while final answers and user prose stay visible',()=>{
  const values=groups([message(1,'user','','I think this should work.'),message(2,'assistant','commentary'),
    message(3,'tool'),message(4,'assistant','analysis','PRIVATE'),message(5,'assistant','final_answer','I think the result is useful.'),
    message(6,'user'),message(7,'assistant','commentary')]);
  assert.deepEqual(values.map(v=>[v.type,v.messages.map(m=>m.id)]),[['message',['1']],['progress',['2','3']],['message',['5']],['message',['6']],['progress',['7']]]);
  assert(!JSON.stringify(values).includes('PRIVATE'));
});
test('channel-marked commentary folds and every structured reasoning variant is excluded',()=>{
  const input=[{...message(0),channel:'commentary'},...['analysis','thinking','reasoning','reasoning_summary'].flatMap((kind,i)=>['phase','channel','type'].map(field=>({...message(i+1),[field]:kind}))),message(5,'assistant','','I think the measured result is useful.')];
  const result=groups(input);
  assert.equal(result.length,2);assert.equal(result[0].type,'progress');assert.equal(result[1].messages[0].id,'5');
});
test('history starts at the latest page and can reach every older and newer available turn',()=>{
  const data=groups(Array.from({length:240},(_,i)=>message(i))),window=new Window();window.update(data);
  assert.equal(window.view.length,32);assert.equal(window.view.at(-1).messages[0].id,'239');
  const visited=new Set(window.view.map(g=>g.key));
  while(window.older()){assert(window.view.length<=32);window.view.forEach(g=>visited.add(g.key));}
  assert.equal(visited.size,240);assert.equal(window.start,0);
  while(window.newer())assert(window.view.length<=32);
  assert.equal(window.end,240);
});
test('an agent waiting on the user keeps its latest question visible',()=>{
  const values=groups([message(1,'user'),message(2,'assistant','commentary'),message(3,'assistant','commentary','Which checkpoint should I use?')],true);
  assert.equal(values.at(-1).type,'message');assert.equal(values.at(-1).messages[0].text,'Which checkpoint should I use?');
  assert.equal(values.at(-2).messages.length,1);
});
test('reading an old page survives a live append and source jumps stay exact',()=>{
  const data=Array.from({length:100},(_,i)=>message(i)),window=new Window();window.update(groups(data));window.older();
  const before=window.view.map(g=>g.key);window.update(groups([...data,message(100)]));assert.deepEqual(window.view.map(g=>g.key),before);
  assert(window.reveal(key(data[2])));assert(window.view.some(g=>g.messages.includes(data[2])));
  assert(!window.reveal('assistant:missing'));
  window.update(groups([message('new-session')]));assert.equal(window.view[0].messages[0].id,'new-session');
});
test('reading within the live page still adds new replies without losing the visible anchor',()=>{
  const data=Array.from({length:40},(_,i)=>message(i)),window=new Window();window.update(groups(data));
  window.update(groups([...data,message(40)]),false,'assistant:30');
  assert.equal(window.view.at(-1).key,'assistant:40');
  assert(window.view.some(group=>group.key==='assistant:30'));
  assert.equal(window.end,41);
  // A very old visible anchor still takes priority over a full live page.
  window.update(groups([...data,...Array.from({length:40},(_,i)=>message(40+i))]),false,'assistant:30');
  assert(window.view.some(group=>group.key==='assistant:30'));
  assert(window.view.length<=32);
});
test('oversized individual replies remain complete and pages never skip expensive turns',()=>{
  const data=groups(Array.from({length:20},(_,i)=>message(i,'assistant','final_answer','x'.repeat(120000))));
  const window=new Window();window.update(data);const visited=new Set();
  do{assert.equal(window.view.length,1);visited.add(window.view[0].key);}while(window.older());
  assert.equal(visited.size,20);assert(window.reveal('assistant:12'));assert.equal(window.view[0].key,'assistant:12');
});
test('mixed reply sizes never leave gaps between older or newer pages',()=>{
  for(const size of [240,1200,30000,220000]){
    const data=groups(Array.from({length:150},(_,i)=>message(i,'assistant','final_answer','x'.repeat(i%7===0?size:300))));
    const window=new Window(),older=new Set(),newer=new Set();window.update(data);
    let previous=window.end;
    do{
      assert(window.end>=previous,'Older paging skipped unread messages');
      window.view.forEach(group=>older.add(group.key));previous=window.start;
    }while(window.older());
    previous=window.start;
    do{
      assert(window.start<=previous,'Newer paging skipped unread messages');
      window.view.forEach(group=>newer.add(group.key));previous=window.end;
    }while(window.newer());
    assert.equal(older.size,data.length);assert.equal(newer.size,data.length);
    assert.equal(window.end,data.length);
  }
});
test('a growing reply above the viewport cannot evict the message being read',()=>{
  const data=Array.from({length:45},(_,i)=>message(i)),window=new Window();window.update(groups(data));
  data[13]={...data[13],text:'x'.repeat(220000)};
  window.update(groups(data),false,'assistant:30');
  assert(window.view.some(group=>group.key==='assistant:30'));
  assert(window.view.length<=32);
  window.update(groups([...data,message(45)]),true,'assistant:30');
  assert.equal(window.view.at(-1).key,'assistant:45');
});
test('only long multi-section replies receive local section navigation',()=>{
  const html='<p>Result.</p><h2>Measurements</h2><p>One.</p><h2>&lt;script&gt;</h2><p>Two.</p>';
  assert.equal(outline(html,'short'),html);
  const result=outline(html,'x'.repeat(2000));
  assert.match(result,/In this reply/);assert.match(result,/data-reply-section="1"/);assert.match(result,/data-reply-jump="0"/);
  assert(!result.includes('<script>'));
  assert.equal(outline('<h2>Only heading</h2>','x'.repeat(2000)),'<h2>Only heading</h2>');
});

test('moderate Claude replies keep full original formatting without a reading fold or outline',()=>{
  const {replyHTML,shortReply}=require('./static/reader.js');
  const text='checkpoint_comparison '.repeat(300).trim();
  const html='<h2>Result</h2><p>'+text+'</p><h2>Trace</h2><pre><code>score = 0.4</code></pre>';
  assert(text.length>1500);
  assert(shortReply(text));assert(!shortReply(text+' done'));
  assert.equal(replyHTML(html,text),html);
});
