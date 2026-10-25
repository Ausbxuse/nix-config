const {test}=require('node:test');
const assert=require('node:assert/strict');
const {stateOf,texts,count,confirmations,presentation}=require('./static/queue.js');
const reader=require('./static/reader.js');

test('an earlier image with the same caption cannot confirm a new image send or queue',()=>{
  const image=id=>({host:'worker',id});
  const entry={text:'Please look at the attached images.',created:100,status:'sending',attachments:[image('new')]};
  const earlier={role:'user',text:entry.text,created:new Date(99000).toISOString(),attachments:[image('old')]};
  assert.deepEqual(confirmations([entry],[entry.text],[earlier],[{text:entry.text,attachments:[image('old')]}]),[]);
  assert.deepEqual(confirmations([entry],[],[{...earlier,attachments:[image('new')]}]),[entry]);
  assert.deepEqual(confirmations([entry],[entry.text],[],[{text:entry.text,attachments:[image('new')]}]),[entry]);
  assert.deepEqual(confirmations([{...entry,status:'failed'}],[entry.text],[],[{text:entry.text,attachments:[image('new')]}]),[]);
});

test('queue editing distinguishes source changes, execution/removal and connection loss',()=>{
  const item={id:'one',digest:'before'},queue={status:'available',items:[item]};
  assert.equal(stateOf(queue,item),'current');
  assert.equal(stateOf({...queue,items:[{...item,digest:'after'}]},item),'changed');
  assert.equal(stateOf({...queue,items:[]},item),'gone');
  assert.equal(stateOf(undefined,item),'offline');
  assert.equal(stateOf({status:'unavailable'},item),'offline');
});

test('a native queue replaces capture-derived queue rows without changing the raw source',()=>{
  const capture='• A completed response.\n\n• Queued follow-up inputs\n  ↳ Compare the seeds\n  ↳ Keep both traces\n  shift + up to edit last queued message';
  assert.match(reader.render(capture,[],true),/Compare the seeds/);
  const hidden=reader.render(capture,[],true,true);
  assert.doesNotMatch(hidden,/Compare the seeds|Keep both traces/);
  assert.match(hidden,/A completed response/);
  assert.deepEqual(reader.queued(capture),['Compare the seeds','Keep both traces']);
});

const entry=(extra={})=>({request_id:'request-1',text:'Compare the seeds across both hosts.',created:100,status:'queued',baselineCount:0,...extra});

test('a clipped queue restores the saved image on return without raw transport or duplicate receipts',()=>{
  const image={host:'worker',id:'a'.repeat(32)},receipt=entry({attachments:[image]});
  const clipped=receipt.text+'\n\nAttached images (open these files to view them):\n…';
  const result=presentation(undefined,[clipped],[receipt]);
  assert.equal(result.suppress_terminal,true);
  assert.deepEqual(result.items[0].attachments,[image]);
  assert.equal(result.items[0].text,receipt.text);
  assert.equal(result.items[0].editable,false);
  assert.deepEqual(confirmations([receipt],texts(result),[],result.items),[receipt]);
  assert.deepEqual(presentation({status:'available',items:[],suppress_terminal:true},[],[receipt]).items,[],'Consumed queues are not cached');
});

test('clipped previews retain uncertain recovery cards without claiming delivery',()=>{
  const receipt=entry({attachments:[{host:'worker',id:'a'.repeat(32)}],status:'uncertain'});
  const item={text:receipt.text,attachments:receipt.attachments,attachment_match:'preview'};
  const result=presentation({status:'available',items:[item],suppress_terminal:true},[],[receipt]);
  assert.deepEqual(result.items,[],'The recovery card is the sole visible copy');
  assert.deepEqual(confirmations([receipt],[receipt.text],[],[item]),[]);
  assert.equal(receipt.status,'uncertain');assert(!receipt.confirmed);
});

test('preview recovery does not guess between photos or override the host attachment lookup',()=>{
  const first=entry({attachments:[{host:'worker',id:'a'.repeat(32)}]}),second=entry({attachments:[{host:'worker',id:'b'.repeat(32)}]});
  const clipped=first.text+'\n\nAttached images (open these files to view them):\n…';
  const ambiguous=presentation(null,[clipped],[first,second]).items[0];
  assert(!ambiguous.attachments);assert.equal(ambiguous.text,first.text);assert(ambiguous.has_attachments);
  const unresolved=presentation({status:'available',items:[{backend:'terminal',text:clipped}],suppress_terminal:true},[],[first]).items[0];
  assert(!unresolved.attachments,'Another browser may have sent a different image with the same caption');
  assert.deepEqual(confirmations([first,second],[],[],[ambiguous]),[]);
  assert.equal(first.text,'Compare the seeds across both hosts.');
});

test('a confirmed image request reserves its occurrence when a second identical image is queued',()=>{
  const first=entry({attachments:[{host:'worker',id:'a'.repeat(32)}],confirmed:true}),second={...first,request_id:'request-2',confirmed:false};
  const item={text:first.text,attachments:first.attachments};
  assert.deepEqual(confirmations([first,second],[],[],[item]),[]);
  assert.deepEqual(confirmations([first,second],[],[],[item,item]),[second]);
  assert.deepEqual(confirmations([first,second],[],[{...item,role:'user',created:new Date(100000).toISOString()}],[item]),[second],
    'An earlier request already in the transcript does not occupy the current queue');
});

test('receipt matching includes the terminal fallback beside an empty API queue',()=>{
  const terminal=['Compare the seeds across both hosts.'];
  const api={status:'available',items:[],suppress_terminal:false};
  assert.deepEqual(texts(api,terminal),terminal);
  assert.equal(confirmations([entry()],texts(api,terminal)).length,1);
  assert.deepEqual(texts(undefined,terminal),terminal);
  assert.deepEqual(texts({status:'unavailable'},terminal),terminal);
});

test('a merged queue is counted once and independent native inputs remain distinct',()=>{
  const terminal=['Compare the seeds'],native={status:'available',items:[{text:'Compare the seeds'}]};
  assert.deepEqual(texts({...native,suppress_terminal:true},terminal),terminal);
  assert.deepEqual(texts({...native,suppress_terminal:false},terminal),['Compare the seeds','Compare the seeds']);
});

test('wrapping and padding do not leave a second local copy of the queue row',()=>{
  const captured=['Compare the seeds\nacross both hosts.  '];
  assert.equal(count(captured,entry().text),1);
  assert.equal(confirmations([entry()],captured).length,1);
  assert.equal(confirmations([entry({text:'Compare the seeds on a different host.'})],captured).length,0);
});

test('a preview can confirm delivery before its acknowledgement or after a lost response',()=>{
  for(const status of ['sending','queued','uncertain']){
    const value=entry({status});
    assert.deepEqual(confirmations([value],[value.text]),[value]);
  }
  assert.deepEqual(confirmations([entry({status:'failed'})],[entry().text]),[]);
  assert.deepEqual(confirmations([entry({status:'uncertain'})],[]),[]);
});

test('two deliberate identical queue requests each need a visible occurrence',()=>{
  const first=entry(),second=entry({request_id:'request-2',baselineCount:1});
  assert.deepEqual(confirmations([first,second],[first.text]),[first]);
  first.confirmed=true;
  assert.deepEqual(confirmations([first,second],[first.text]),[]);
  assert.deepEqual(confirmations([first,second],[first.text,first.text]),[second]);
  assert.deepEqual(confirmations([entry({baselineCount:1})],[first.text]),[]);
});

test('one snapshot occurrence cannot confirm two unmatched local requests',()=>{
  const first=entry(),second=entry({request_id:'request-2'});
  assert.deepEqual(confirmations([first,second],[first.text]),[first]);
});
test('a dismissed warning cannot consume confirmation for a later queue request',()=>{
  const dismissed=entry({dismissed:true}),later=entry({request_id:'request-2'});
  assert.deepEqual(confirmations([dismissed,later],[later.text]),[later]);
});

test('consumed queue inputs reconcile once each against recent transcript messages',()=>{
  const first=entry(),second=entry({request_id:'request-2',baselineCount:1});
  const message={role:'user',text:first.text,created:new Date(100000).toISOString()};
  assert.deepEqual(confirmations([first,second],[],[message]),[first]);
  first.confirmed=true;
  assert.deepEqual(confirmations([first,second],[],[message]),[]);
  assert.deepEqual(confirmations([first,second],[],[message,{...message,created:new Date(102000).toISOString()}]),[second]);
  assert.deepEqual(confirmations([entry()],[],[{...message,created:new Date(80000).toISOString()}]),[]);
});
