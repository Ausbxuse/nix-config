const {test}=require('node:test');
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
global.AgentReader=require('./static/reader.js');
const brief=require('./static/brief.js');
const hash=text=>crypto.createHash('sha256').update(text).digest('hex').slice(0,24);
const field=(kind,message,text)=>{
  const start=[...message.text.slice(0,message.text.indexOf(text))].length;
  return {kind,text,source:{message_id:message.id,role:message.role,digest:hash(message.text),start,end:start+[...text].length}};
};
const request={id:'user',role:'user',text:'Compare mean_std and quantile using the same seeds.'};
const reply={id:'agent',role:'assistant',text:'🧪 Saved both traces.\n\nWhich checkpoint should I use?\n\n'+('The evaluation uses the same observations and noise. '.repeat(45))};
const messages=[request,reply];
const value={agent_id:'host:agent',version:'current',fields:[field('request',request,request.text),field('change',reply,'Saved both traces.'),field('decision',reply,'Which checkpoint should I use?')]};

test('brief sources verify exact text and full message digests, including Unicode offsets',async()=>{
  assert.deepEqual(await brief.verified(value,messages),value.fields);
  const edited=[request,{...reply,text:reply.text+' Correction: the first result was wrong.'}];
  assert.deepEqual(await brief.verified(value,edited),[]);
  const newer=[...messages,{id:'next',role:'user',text:'Cancel that study.'}];
  assert.deepEqual(await brief.verified(value,newer),[]);
  assert.deepEqual(await brief.verified(value,[reply]),[]);
  const wrong=structuredClone(value);wrong.fields[1].source.start--;
  assert.deepEqual(await brief.verified(wrong,messages),[]);
});

test('short replies and already-visible summary text do not get duplicate summaries',()=>{
  assert.deepEqual(brief.useful(value.fields,'Saved both traces. Which checkpoint should I use?'),[]);
  assert.equal(brief.useful(value.fields,reply.text).length,3);
  const atEnd='Details. '.repeat(310)+'Saved both traces. Which checkpoint should I use?';
  assert.deepEqual(brief.useful(value.fields,atEnd,field=>atEnd.slice(-500).includes(field.text)).map(field=>field.kind),['request']);
  assert.deepEqual(brief.useful(value.fields,reply.text,()=>true),[]);
  const html=brief.markup(value.fields);
  assert.match(html,/mean_std/);
  assert.equal((html.match(/data-brief-source/g)||[]).length,3);
  assert.doesNotMatch(brief.markup([{...value.fields[0],text:'<script>alert(1)</script>'}]),/<script>/);
});

test('technical replies of at most 300 words stay intact without an extra brief',()=>{
  const text=Array(300).fill('checkpoint_comparison').join(' ');
  assert(text.length>650);
  assert.deepEqual(brief.useful(value.fields,text),[]);
  assert.equal(brief.useful(value.fields,text+' complete').length,3);
  assert.deepEqual(brief.useful(value.fields,'\n\n'+text.replaceAll(' ','\n\t')+'\n'),[]);
});

test('an update must shorten the current reply, not repeat it or describe a previous Claude turn',()=>{
  const update={original_reply:false,body:'The trace is ready. Choose the checkpoint.',sources:[{message_id:reply.id}]};
  assert(brief.usefulUpdate(update,messages));
  const short={...reply,text:'The trace is ready. The quantile checkpoint scored 0.4. Review both traces.'};
  assert(!brief.usefulUpdate(update,[request,short]),'old summary flags cannot create a duplicate for a short answer');
  assert(!brief.usefulUpdate({...update,body:reply.text},messages),'a near-copy adds no reading value');
  assert(!brief.usefulUpdate({...update,original_reply:true},messages),'preserved originals belong in the reader only');
  assert(!brief.usefulUpdate(update,[...messages,{id:'new',role:'assistant',phase:'',text:'I will wait for the GPUs.'}]),'an older Claude summary cannot attach to the latest reply');
  assert(!brief.usefulUpdate(update,[...messages,{id:'next',role:'user',text:'Wait.'}]),'a new user turn hides the previous brief');
  assert(!brief.usefulUpdate(update,[]),'loading or terminal fallback cannot verify a cached source');
  assert(!brief.usefulUpdate({...update,sources:[]},messages),'the current reply must be cited');
  assert(brief.usefulUpdate({...update,body:undefined,text:'Agent on host\n'+update.body},messages));
  assert(!brief.summaryUseful('Saved. '.repeat(150), 'a '.repeat(310)),'word count alone can hide an equally long summary');
});

test('a brief disappears while working, after switching tasks, or after its source changes',async()=>{
  const node={hidden:true,innerHTML:''},handoff={id:'handoff',agent_id:'host:agent'};
  await brief.render(node,{facts:value,messages,handoff,agentId:'host:agent'});
  assert(!node.hidden);assert.equal(node._fields.length,3);
  await brief.render(node,{facts:value,messages,handoff,agentId:'host:agent',enabled:false});
  assert(node.hidden);
  const old=brief.render(node,{facts:value,messages,handoff,agentId:'host:agent'});
  await brief.render(node,{facts:value,messages,handoff,agentId:'another:agent'});
  await old;assert(node.hidden,'a delayed digest must not restore a previous agent brief');
  await brief.render(node,{facts:value,messages:[request,{...reply,text:'Different reply.'}],handoff,agentId:'host:agent'});
  assert(node.hidden);
});

test('terminal fallback keeps a direct source and excludes the unsent editor and CLI notices',()=>{
  const capture='› Compare the checkpoints\n• Saved both traces.\n\n  Which checkpoint should I use?\n\n  '+('Same evaluation seeds. '.repeat(40))+'\n\n⚠ Heads up, you have less than 5% of your weekly limit left. Run /status for a breakdown.\n› A new unsent draft';
  const selected=brief.terminalFields(capture);
  assert.equal(selected.fields[0].text,'Compare the checkpoints');
  assert(selected.fields.some(field=>field.kind==='decision'));
  assert.doesNotMatch(JSON.stringify(selected),/Heads up|unsent draft/);
  const source={dataset:{terminalIndex:'1'}};
  const container={querySelectorAll:()=>[source]};
  const change=selected.fields.find(field=>field.kind==='change');
  assert.equal(brief.sourceNode(change,container,[],capture),source);
  assert.equal(brief.sourceNode(change,container,[],capture.replace('Saved both traces.','A different result.')),null);
});
