const {test}=require('node:test');
const assert=require('node:assert/strict');
const content=require('./static/message-content.js');
const reader=require('./static/reader.js');
const history=require('./static/history.js');
const fixtures=require('./message-content-fixtures.json');

test('image-only messages show their image without the transport instruction, including older receipts',()=>{
  const text='Please look at the attached images.',attachments=[{id:'a'.repeat(32),host:'local'}];
  const message=Object.freeze({id:'photo',role:'user',text,attachments});
  assert.equal(content.displayText(message),'');
  assert.equal(content.displayText({text,attachments}),'','pending receipts have no role field');
  assert.equal(content.displayText({text,has_attachments:true}),'','unresolved images still hide the transport instruction');
  assert.equal(content.displayText({...message,text:'Inspect the front wheel.'}),'Inspect the front wheel.');
  assert.equal(content.displayText({role:'user',text}),text,'ordinary user text stays literal');
  assert.equal(content.displayText({...message,role:'assistant'}),text);
  global.HubMarkdown={render:value=>value?'<p>'+value+'</p>':''};
  global.HubImages={html:()=>'<img alt="Attached photo"> '};
  try{
    for(const html of [reader.render('',[message]),reader.render('',[message],true),reader.renderMessages([message])]){
      assert.match(html,/Attached photo/);assert.doesNotMatch(html,/Please look at the attached images/);
    }
    assert.equal(message.text,text,'native receipts and fork verification retain the source');
  }finally{delete global.HubMarkdown;delete global.HubImages;}
});

test('server and browser use the same context envelopes and preserve literal examples',()=>{
  for(const {name,text,expected} of fixtures){
    assert.equal(content.userText(text),expected,name);
    assert.equal(content.visible({role:'user',text}),Boolean(expected),name);
    for(const role of ['assistant','tool'])assert(content.visible({role,text}),name);
  }
});

test('context is absent from latest, history, standalone bubbles, copies and page counts',()=>{
  const hidden=fixtures.filter(item=>!item.expected).map((item,index)=>({id:'context-'+index,role:'user',text:item.text}));
  const ask={id:'ask',role:'user',text:'Compare the checkpoints.',fork:{thread:'thread',turn:'turn'}};
  const reply={id:'reply',role:'assistant',text:'The quantile checkpoint is ready.'};
  const messages=[...hidden,ask,reply,...hidden];
  global.HubMarkdown={render:text=>'<p>'+text.replace(/</g,'&lt;').replace(/>/g,'&gt;')+'</p>'};
  try{
    for(const html of [reader.render('',messages),reader.render('',messages,true),reader.renderMessages(messages)]){
      assert.doesNotMatch(html,/context-\d|internal_context|environment_context|Internal goal data|Access configuration/);
      assert.match(html,/Compare the checkpoints/);
      assert.match(html,/quantile checkpoint is ready/);
    }
    assert.deepEqual(history.groups(messages).map(group=>group.messages[0].id),['ask','reply']);
    assert.equal(reader.userText(hidden[0].text),'');
    assert.equal(reader.renderMessages(hidden),'');
    for(const all of [false,true])assert.doesNotMatch(reader.render('› '+hidden[0].text,[],all),/Internal goal data|aria-label="You"/);
  }finally{delete global.HubMarkdown;}
});

test('mixed context retains genuine text and edit identity without mutating the source',()=>{
  const text=fixtures.find(item=>item.name==='request after context').text;
  const message=Object.freeze({id:'mixed',role:'user',text,fork:Object.freeze({thread:'thread',turn:'turn'})});
  const cleaned=content.messages([message]);
  assert.equal(cleaned[0].text,'Please compare the checkpoints.');
  assert.equal(cleaned[0].fork,message.fork);
  assert.equal(cleaned[0].id,'mixed');
  assert.equal(message.text,text);
});
