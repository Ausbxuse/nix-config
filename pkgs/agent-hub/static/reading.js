/* Small reading bookmarks: message IDs and offsets, never copies of transcripts. */
((root)=>{
  const key=message=>message.role+':'+message.id;
  function digest(message){let hash=2166136261;for(const char of message.text || '')hash=Math.imul(hash^char.codePointAt(0),16777619);return (hash>>>0).toString(36);}
  const entries=container=>[...container.querySelectorAll('.reader-entry[data-message-id]')].filter(node=>node.dataset.messageId!=='terminal-queue' && node.getClientRects().length);
  const blocks=node=>[...(node.querySelector('.reader-prose,.message-text')?.children || [])].flatMap(child=>child.classList.contains('reply-expansion')?[...child.querySelector('.reply-full').children]:[child]).filter(child=>!child.classList.contains('reply-outline'));
  function position(container,nodes,id=node=>node.dataset.messageId){
    const top=container.getBoundingClientRect().top,anchor=nodes.find(node=>node.getBoundingClientRect().bottom>top);
    let block;
    if(anchor){
      const parts=blocks(anchor),index=parts.findIndex(node=>node.getBoundingClientRect().bottom>top);
      if(index>=0){const node=parts[index],rect=node.getBoundingClientRect();block={index,digest:digest({text:node.textContent}),offset:rect.top-top,fraction:Math.max(0,Math.min(1,(top-rect.top)/Math.max(1,rect.height)))};}
    }
    return {top:container.scrollTop,bottom:container.scrollHeight-container.scrollTop-container.clientHeight<80,
      anchor:anchor?{id:id(anchor),offset:anchor.getBoundingClientRect().top-top,...(block?{block}:{})}:null};
  }
  function capture(container,messages,previous={}){
    const nodes=entries(container),top=container.getBoundingClientRect().top,bottom=top+container.clientHeight;
    const visible=nodes.filter(node=>node.getBoundingClientRect().top<bottom && node.getBoundingClientRect().bottom>top);
    const last=visible.at(-1),lastIndex=messages.findIndex(m=>key(m)===last?.dataset.messageId),oldIndex=messages.findIndex(m=>key(m)===previous.seen?.id);
    const seen=lastIndex>=0 && lastIndex>=oldIndex?{id:key(messages[lastIndex]),digest:digest(messages[lastIndex])}:previous.seen;
    return {...position(container,nodes),seen,at:Date.now()};
  }
  function restore(container,record,nodes=entries(container),id=node=>node.dataset.messageId){
    if(record.bottom){container.scrollTop=container.scrollHeight;return;}
    const anchor=nodes.find(node=>id(node)===record.anchor?.id),part=record.anchor?.block;
    const block=anchor&&part?blocks(anchor)[part.index]:null;
    if(block&&digest({text:block.textContent})===part.digest){const expanded=block.closest('.reply-expansion');if(expanded)expanded.open=true;const rect=block.getBoundingClientRect();container.scrollTop+=rect.top-container.getBoundingClientRect().top-(part.offset<0?-part.fraction*rect.height:part.offset);}
    else if(anchor)container.scrollTop+=anchor.getBoundingClientRect().top-container.getBoundingClientRect().top-record.anchor.offset;
    else container.scrollTop=record.top || 0;
  }
  function firstNew(messages,record){
    if(!record?.seen)return null;
    const index=messages.findIndex(m=>key(m)===record.seen.id);
    if(index<0)return null;
    return digest(messages[index])!==record.seen.digest?key(messages[index]):messages[index+1]?key(messages[index+1]):null;
  }
  function mark(container,messages,record){
    const id=firstNew(messages,record);
    for(const node of container.querySelectorAll('[data-unread-start]'))if(node.dataset.messageId!==id)node.removeAttribute('data-unread-start');
    const index=messages.findIndex(message=>key(message)===id);
    const node=index<0?null:entries(container).find(node=>messages.findIndex(message=>key(message)===node.dataset.messageId)>=index);
    if(node)node.dataset.unreadStart='true';
  }
  const exports={capture,restore,position,mark,firstNew,digest,key};
  if(typeof module==='object' && module.exports)module.exports=exports;else root.HubReading=exports;
})(globalThis);
