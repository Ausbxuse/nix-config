/* Exact native queue items. Editing a queue never sends a new instruction. */
((root)=>{
  const messageKey=text=>String(text??'').replace(/\s+/g,' ').trim();
  const count=(texts,text)=>texts.filter(value=>messageKey(value)===messageKey(text)).length;
  const sameImages=(a,b)=>JSON.stringify((a||[]).map(image=>[image.host,image.id]))===JSON.stringify((b||[]).map(image=>[image.host,image.id]));
  function imageWire(text){
    const header=' Attached images (open these files to view them): ',key=messageKey(text),at=key.lastIndexOf(header);
    if(at<0)return null;
    const tail=key.slice(at+header.length),clipped=/^(?:…|\.\.\.)$/.test(tail);
    const paths=tail.split(' '),ids=paths.map(path=>/^\/\S*\/([a-f0-9]{32})\.(?:png|jpg)$/.exec(path)?.[1]);
    if(!clipped&&(!ids.length||ids.some(id=>!id)))return null;
    return {caption:key.slice(0,at),clipped,ids:clipped?[]:ids};
  }
  function presentation(value,terminal=[],receipts=[]){
    // All queue rows use one renderer, including a read-only terminal fallback.
    // Saved receipts survive navigation, while the terminal may show only "…".
    const available=value?.status==='available',items=available?value.items:[];
    if(!terminal.length&&!items.length)return value;
    const fallback=(!available||value.suppress_terminal===false)?terminal.map((text,index)=>({
      id:'capture-'+index,text,digest:'',editable:false,removable:false,backend:'capture'
    })):[];
    const used=new Set();
    const shown=[...items,...fallback].map(item=>{
      if(item.attachments?.length)return item;
      const wire=imageWire(item.text);
      if(!wire)return item;
      const candidates=receipts.filter(entry=>entry.attachments?.length&&messageKey(entry.text)===wire.caption&&
        (wire.clipped||JSON.stringify(entry.attachments.map(image=>image.id))===JSON.stringify(wire.ids)));
      const unique=new Map(candidates.map(entry=>[JSON.stringify(entry.attachments.map(image=>[image.host,image.id])),entry]));
      if(unique.size===1&&item.backend==='capture'){
        const entry=[...unique.values()][0];
        return {...item,text:entry.text,attachments:entry.attachments,attachment_match:wire.clipped?'preview':'exact'};
      }
      return {...item,text:wire.caption,has_attachments:true};
    }).filter(item=>{
      if(item.attachment_match!=='preview')return true;
      const index=receipts.findIndex((entry,index)=>!used.has(index)&&!entry.dismissed&&
        messageKey(entry.text)===messageKey(item.text)&&sameImages(entry.attachments,item.attachments));
      if(index<0)return true;
      used.add(index);
      const entry=receipts[index];
      // A clipped preview can show a known queued send, but cannot establish an
      // uncertain delivery. Keep that request's recovery card, with its image.
      return entry.confirmed||entry.status==='queued';
    });
    return {...value,status:'available',items:shown,...(fallback.length?{suppress_terminal:true}:{})};
  }
  function texts(queue,terminal=[]){
    if(queue?.status!=='available')return terminal;
    // The API and terminal have independent queues. An empty API result must
    // not exclude the terminal rows that the conversation still displays.
    const native=queue.items.map(item=>item.text);
    return queue.suppress_terminal===false?[...native,...terminal]:native;
  }
  function confirmations(entries,captured,messages=[],queuedItems=[]){
    const matched=[],usedMessages=new Set(),usedPositions=new Map(),usedImages=new Set();
    for(const entry of entries){
      if(entry.status==='failed'||entry.dismissed)continue;
      const key=messageKey(entry.text);
      // Each transcript message can account for only one local request.
      const index=messages.findIndex((message,index)=>!usedMessages.has(index)&&message.role==='user'&&
        messageKey(message.text)===key&&sameImages(entry.attachments,message.attachments)&&Date.parse(message.created)/1000>=entry.created-5);
      if(index>=0)usedMessages.add(index);
      const position=Math.max(entry.baselineCount||0,usedPositions.get(key)||0);
      const imageIndex=entry.attachments?.length?queuedItems.findIndex((item,index)=>!usedImages.has(index)&&
        (item.attachment_match!=='preview'||entry.status==='queued')&&messageKey(item.text)===key&&sameImages(entry.attachments,item.attachments)):-1;
      if(entry.confirmed){if(index<0&&imageIndex>=0)usedImages.add(imageIndex);continue;}
      const inQueue=['sending','queued','uncertain'].includes(entry.status)&&(entry.attachments?.length?imageIndex>=0:count(captured,entry.text)>position);
      if(inQueue&&imageIndex>=0)usedImages.add(imageIndex);
      if(inQueue)usedPositions.set(key,position+1);
      if(inQueue||index>=0)matched.push(entry);
    }
    return matched;
  }
  const stateOf=(queue,edit)=>!queue || queue.status!=='available'?'offline':
    !queue.items.some(item=>item.id===edit.id)?'gone':
    queue.items.find(item=>item.id===edit.id).digest!==edit.digest?'changed':'current';
  function mount(node,{send,refresh}){
    let agentId='',queue=null,edit=null,busy=false,note='',generation=0;
    const drafts=new Map(),rows=new Map();
    node.className='reader-entry reader-user native-queue';node.hidden=true;
    node.setAttribute('aria-label','Your queued messages');
    node.innerHTML='<h3><span class="reader-speaker">You</span><span class="reader-meta">Queued</span></h3><div class="reader-prose"><ul class="reader-queue" role="list" aria-label="Queued messages"></ul><p class="queue-note" role="status" hidden></p></div>';
    const list=node.querySelector('ul'),notice=node.querySelector('.queue-note');
    const draftKey=id=>agentId+':'+id;
    function revealEdit(){
      if(edit && node.contains(document.activeElement))requestAnimationFrame(()=>{
        const target=notice.hidden?node.querySelector('.queue-edit-controls'):notice;
        if(edit && node.contains(document.activeElement))target?.scrollIntoView({block:'nearest'});
      });
    }
    if(root.ResizeObserver && node.parentElement)new ResizeObserver(revealEdit).observe(node.parentElement);
    function remember(){
      if(!edit)return;
      drafts.delete(draftKey(edit.id));drafts.set(draftKey(edit.id),{...edit});
      while(drafts.size>32)drafts.delete(drafts.keys().next().value);
    }
    function select(id){
      if(agentId===id)return;
      remember();generation++;agentId=id;queue=edit=null;busy=false;note='';rows.clear();list.replaceChildren();node.hidden=true;
    }
    function button(label,icon,action){
      const result=document.createElement('button');result.type='button';result.className='icon-button';
      result.setAttribute('aria-label',label);result.title=label;result.dataset.queueAction=action;
      result.innerHTML='<svg class="icon" aria-hidden="true"><use href="#i-'+icon+'"/></svg>';return result;
    }
    function makeRow(item){
      const row=document.createElement('li');row.dataset.queueId=item.id;
      const arrow=document.createElement('span');arrow.className='queue-arrow';arrow.innerHTML='<svg class="icon"><use href="#i-queued"/></svg>';arrow.setAttribute('aria-hidden','true');
      const body=document.createElement('div');body.className='queue-message';
      const text=document.createElement('div');text.className='queue-input-text';
      const controls=document.createElement('div');controls.className='queue-controls';
      controls.append(button('Edit queued message','edit','edit'),button('Remove queued message','close','delete'));
      body.append(text);row.append(arrow,body,controls);rows.set(item.id,row);return row;
    }
    function render(){
      const available=queue?.status==='available',items=available?queue.items:[];
      const shown=edit&&!items.some(item=>item.id===edit.id)?[...items,{id:edit.id,text:edit.text,editable:true}]:items;
      const keep=new Set();
      for(const item of shown){
        let row=rows.get(item.id);if(!row)row=makeRow(item);
        keep.add(item.id);const editing=edit?.id===item.id;
        if(!editing){
          row.querySelector('form')?.remove();
          const text=row.querySelector('.queue-input-text');text.hidden=false;
          const body=(root.HubMessageContent?.displayText(item)??item.text)+(item.has_attachments&&!item.attachments?.length?'\nAttachment included':'');
          if(text.textContent!==body)text.textContent=body;
          let images=row.querySelector('.queue-images');
          if(item.attachments?.length){
            if(!images){images=document.createElement('div');images.className='queue-images';text.before(images);}
            const markup=root.HubImages?.html(item.attachments)||'';
            if(images.innerHTML!==markup)images.innerHTML=markup;
          }else images?.remove();
          const controls=row.querySelector('.queue-controls');controls.hidden=!item.editable&&item.removable===false;
          controls.querySelector('[data-queue-action=edit]').hidden=!item.editable;
          controls.querySelector('[data-queue-action=delete]').hidden=item.removable===false;
          for(const control of controls.children)control.disabled=busy||!available;
        }else{
          row.querySelector('.queue-input-text').hidden=true;row.querySelector('.queue-controls').hidden=true;
          let form=row.querySelector('form');
          if(!form){
            form=document.createElement('form');form.className='queue-edit';
            const input=document.createElement('textarea');input.rows=3;input.maxLength=12000;input.setAttribute('aria-label','Edit queued message');input.value=edit.text;
            const controls=document.createElement('div');controls.className='queue-edit-controls';
            const cancel=document.createElement('button');cancel.type='button';cancel.textContent='Cancel';cancel.dataset.queueAction='cancel';
            const save=document.createElement('button');save.type='submit';save.textContent='Save';save.className='queue-save';
            controls.append(cancel,save);form.append(input,controls);row.querySelector('.queue-message').append(form);
            input.oninput=()=>{if(edit?.id===item.id){edit.text=input.value;remember();}};
            input.onkeydown=event=>{
              if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();form.requestSubmit();}
              else if(event.ctrlKey&&!event.altKey&&!event.metaKey&&event.key.toLowerCase()==='w'){
                event.preventDefault();const end=input.selectionEnd,start=input.selectionStart===end?input.value.slice(0,end).replace(/\S+\s*$/,'').length:input.selectionStart;
                input.setRangeText('',start,end,'end');input.dispatchEvent(new Event('input',{bubbles:true}));
              }
            };
            form.onsubmit=event=>{event.preventDefault();if(!form.querySelector('[type=submit]').disabled)change('queue_update',edit);};
          }
          const condition=stateOf(queue,edit);
          form.querySelector('textarea').readOnly=busy;
          form.querySelector('[type=submit]').disabled=busy||condition!=='current';
          form.querySelector('[data-queue-action=cancel]').disabled=busy;
          if(condition!=='current')note={offline:'Queue unavailable. Your edit is kept.',gone:'This input left the queue. Your edit was not sent.',changed:'This queued message changed. Cancel this edit to read the latest version.'}[condition];
        }
        if(row!==list.children[[...keep].length-1])list.append(row);
      }
      for(const [id,row] of rows)if(!keep.has(id)){row.remove();rows.delete(id);}
      const priorNote=notice.textContent;
      notice.textContent=note||(queue?.more?'More queued inputs are available in the terminal.':'');notice.hidden=!notice.textContent;
      node.hidden=!shown.length&&!note;
      if(notice.textContent!==priorNote && edit && node.getBoundingClientRect().top<node.parentElement.getBoundingClientRect().bottom)revealEdit();
    }
    async function change(type,item){
      if(busy || queue?.status!=='available')return;
      const current=queue.items.find(value=>value.id===item.id);
      if(!current || current.digest!==item.digest){note='This queued input changed. Refresh before editing it.';render();return;}
      const selected=agentId,thread=queue.thread_id,epoch=generation;
      busy=true;note='';render();
      try{
        await send({type,agent_id:selected,thread_id:thread,queue_id:item.id,queue_digest:item.digest,...(current.backend?{queue_backend:current.backend}:{}),...(type==='queue_update'?{message:item.text}:{})});
        if(drafts.get(selected+':'+item.id)?.text===item.text)drafts.delete(selected+':'+item.id);
        if(generation===epoch){edit=null;note='';queue={...queue,items:type==='queue_delete'?queue.items.filter(value=>value.id!==item.id):queue.items.map(value=>value.id===item.id?{...value,text:item.text}:value)};}
        await refresh();
      }catch(error){if(generation===epoch)note=error.message||'Could not confirm the queue change. Refresh before trying again.';}
      finally{if(generation===epoch){busy=false;render();}}
    }
    node.addEventListener('click',event=>{
      const action=event.target.closest('[data-queue-action]')?.dataset.queueAction;
      if(!action || busy)return;
      const id=event.target.closest('[data-queue-id]')?.dataset.queueId,item=queue?.items?.find(item=>item.id===id);
      if(action==='cancel'){drafts.delete(draftKey(edit.id));edit=null;note='';render();}
      else if(action==='edit'&&item?.editable){remember();edit={...item,...(drafts.get(draftKey(id))||{})};note='';render();const input=rows.get(id).querySelector('textarea');input.focus();input.setSelectionRange(input.value.length,input.value.length);revealEdit();}
      else if(action==='delete'&&item)change('queue_delete',item);
    });
    return {select,update(id,value){if(agentId!==id)select(id);queue=value||null;if(!edit&&!busy)note='';render();},
      texts:terminal=>texts(queue,edit?[]:terminal),
      items:()=>queue?.status==='available'?queue.items:[],
      suppressTerminal:()=>queue?.status==='available'&&queue.suppress_terminal!==false||Boolean(edit)};
  }
  const api={mount,stateOf,texts,count,confirmations,sameImages,presentation};
  if(typeof module==='object'&&module.exports)module.exports=api;else root.HubQueue=api;
})(globalThis);
