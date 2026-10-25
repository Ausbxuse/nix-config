/* Explicit device handoff. Checkpoints never execute commands or control other tabs. */
((root)=>{
  function checkpoint(value){
    if(!value)return null;
    const copy=JSON.parse(JSON.stringify(value));
    if(copy.bookmark)delete copy.bookmark.at;
    return copy;
  }
  class Publisher {
    constructor(send,delay=800){this.send=send;this.delay=delay;this.sequence=0;this.sent='';this.wanted=null;this.running=null;this.timer=null;}
    stage(value){
      if(!value)return;
      const clean=checkpoint(value),signature=JSON.stringify(clean);
      if(signature===this.wanted?.signature || signature===this.sent&&!this.wanted)return;
      this.wanted={value:clean,signature};clearTimeout(this.timer);
      this.timer=setTimeout(()=>this.flush(),this.delay);
    }
    flush(){
      clearTimeout(this.timer);
      if(this.running)return this.running;
      this.running=(async()=>{
        while(this.wanted){
          const next=this.wanted;this.wanted=null;
          if(next.signature===this.sent)continue;
          try{await this.send({...next.value,sequence:++this.sequence});this.sent=next.signature;}
          catch{if(!this.wanted)this.wanted=next;break;}
        }
      })().finally(()=>{this.running=null;});
      return this.running;
    }
  }
  const api={Publisher,checkpoint};
  if(typeof module==='object'&&module.exports){module.exports=api;return;}
  root.HubContinuity=api;
  const read=(key,fallback)=>{try{return JSON.parse(localStorage.getItem(key))??fallback;}catch{return fallback;}};
  const store=(key,value)=>{try{localStorage.setItem(key,JSON.stringify(value));return true;}catch{return false;}};
  const validId=id=>typeof id==='string'&&/^[a-zA-Z0-9_-]{16,64}$/.test(id);
  const randomId=()=>[...crypto.getRandomValues(new Uint8Array(16))].map(value=>value.toString(16).padStart(2,'0')).join('');
  let device=read('hub-device-id',null);if(!validId(device)){device=randomId();store('hub-device-id',device);}
  const tab=randomId(),userAgent=navigator.userAgent;
  const label=(/Firefox\//.test(userAgent)?'Firefox':/Edg\//.test(userAgent)?'Edge':/Chrome\//.test(userAgent)?'Chrome':'Browser')+' · '+(/iPad|Tablet/.test(userAgent)?'Tablet':/Android|iPhone|Mobile/.test(userAgent)?'Phone':'Desktop');
  const publisher=new Publisher(async value=>{
    const response=await fetch('/api/continuity',{method:'POST',keepalive:true,headers:{'Content-Type':'application/json','X-Hub-Token':state.snapshot?.csrf_token || ''},body:JSON.stringify({device,tab,label,...value})});
    if(!response.ok)throw Error('Checkpoint not saved');
  });
  const text=(tag,content,className)=>{const node=document.createElement(tag);node.textContent=content;if(className)node.className=className;return node;};
  const button=(content,run,className)=>{const node=text('button',content,className);node.type='button';node.onclick=run;return node;};
  const menu=button('Continue here',()=>show(),'');menu.id='focus-continue';menu.hidden=true;$('workspace-menu-items').append(menu);
  const dialog=document.createElement('dialog');dialog.id='continuity-dialog';dialog.setAttribute('aria-labelledby','continuity-title');
  const header=document.createElement('header'),title=text('strong','Continue here');title.id='continuity-title';header.append(title,button('Close',()=>dialog.close()));
  const content=document.createElement('div');content.className='continuity-content';dialog.append(header,content);document.body.append(dialog);
  let generation=0;
  dialog.addEventListener('close',()=>{generation++;api.changed();});
  const recoveries=()=>{const saved=read('hub-saved-drafts',[]);return (Array.isArray(saved)?saved:[]).filter(item=>item?.draft&&Date.now()/1000-item.updated<7*86400).slice(0,8);};
  function remember(value){
    if(!value.draft)return;
    const saved={...value,label:'Saved draft on this device',updated:Date.now()/1000};
    const items=recoveries().filter(item=>!(item.agent_id===value.agent_id&&item.mode===value.mode&&item.draft===value.draft));
    if(!store('hub-saved-drafts',[saved,...items].slice(0,8)))throw Error('Could not save the other draft. Copy it before switching.');
  }
  function available(value){return !value.agent_id||Boolean(agentById(value.agent_id));}
  function target(value){return (value.mode==='supervisor'&&value.agent_id?'ARIA · ':'')+value.title+(value.host?' · '+value.host:'');}
  function choose(value){
    if(!available(value)){toast('This agent is no longer available. You can still copy its saved draft.',true);return;}
    const local=HubFocus.continuityDraft(value),context=JSON.stringify(checkpoint(HubFocus.continuitySnapshot()));
    const apply=useLocal=>{
      if(context!==JSON.stringify(checkpoint(HubFocus.continuitySnapshot())) || local!==HubFocus.continuityDraft(value)){
        choose(value);toast('Your conversation changed. Choose again.');return;
      }
      try{
        const draft=useLocal?local:value.draft;
        if(local&&value.draft&&local!==value.draft)remember({...value,draft:useLocal?value.draft:local});
        HubFocus.continueHere(value,draft);dialog.close();toast('Continued here.');
      }catch(error){toast(error.message,true);}
    };
    if(local&&value.draft&&local!==value.draft){
      content.replaceChildren(text('p',target(value),'continuity-target'));
      content.append(text('p','Choose a draft. The other stays in Saved drafts.','continuity-note'));
      content.append(text('strong','On this device'),text('pre',local,'continuity-draft'),button('Keep this draft',()=>apply(true),'primary-button'));
      content.append(text('strong',value.label),text('pre',value.draft,'continuity-draft'),button('Use this draft',()=>apply(false),'primary-button'));
      content.append(button('Back',()=>show()));
    }else apply(Boolean(local&&!value.draft));
  }
  function rows(items,heading){
    if(heading)content.append(text('h2',heading));
    for(const item of items){
      const row=document.createElement('section');row.className='continuity-row';
      const pick=button('',()=>choose(item),'continuity-choice');pick.disabled=!available(item);
      pick.append(text('span',item.label),text('strong',target(item)),text('small',relativeTime(item.updated)+(item.draft?' · Draft':'')+(!available(item)?' · Agent unavailable':'')));
      row.append(pick);
      if(item.draft){
        const details=document.createElement('details');details.append(text('summary','View draft'),text('pre',item.draft,'continuity-draft'));
        details.append(button('Copy draft',async()=>{try{await navigator.clipboard.writeText(item.draft);toast('Draft copied.');}catch{toast('Select the draft text to copy it.',true);}}));row.append(details);
      }
      content.append(row);
    }
  }
  async function show(){
    document.querySelector('.workspace-menu').open=false;
    if(!dialog.open)dialog.showModal();
    const current=++generation;content.replaceChildren(text('p','Loading…','continuity-note'));
    try{
      const response=await window.api('/api/continuity?device='+encodeURIComponent(device));
      if(!dialog.open||generation!==current)return;
      content.replaceChildren();
      if(response.items.length)rows(response.items);else content.append(text('p','Open this hub on your other device. Its latest conversation will appear here.','continuity-note'));
    }catch(error){if(!dialog.open||generation!==current)return;content.replaceChildren(text('p',error.message,'continuity-note'));}
    const saved=recoveries();if(saved.length)rows(saved,'Saved drafts');
  }
  api.changed=()=>{
    menu.hidden=state.snapshot?.continuity!==true;
    if(menu.hidden||document.hidden||!document.hasFocus()||dialog.open)return;
    publisher.stage(HubFocus.continuitySnapshot());
  };
  for(const event of ['focus','online'])window.addEventListener(event,()=>{api.changed();publisher.flush();});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)publisher.flush();else api.changed();});
  window.addEventListener('pagehide',()=>publisher.flush());
  for(const id of ['agent-reader','task-supervisor'])$(id).addEventListener('scroll',()=>api.changed(),{passive:true});
  api.changed();
})(globalThis);
