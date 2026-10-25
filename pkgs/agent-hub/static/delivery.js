/* Per-request records survive reloads without replaying a send. */
((root)=>{
  const prefix='hub-delivery-v1:',age=30*86400,limit=160,bytes=2*1024*1024;
  const fields=['request_id','kind','agent_id','context_agent_id','owner','text','attachments','created','status','confirmed','dismissed','error','afterKey','request','job_id','baselineInputs','baselineDigest','baselineCount','wasRunning','responseSeen'];
  const accepted=entry=>['sent','queued'].includes(entry.status);
  function valid(entry){
    return entry && ['agent','queue','supervisor'].includes(entry.kind) && typeof entry.request_id==='string' && /^web-[a-z0-9-]{8,90}$/.test(entry.request_id) &&
      typeof entry.text==='string' && entry.text.length<=12000 && Number.isFinite(entry.created) && typeof entry.request==='object' && entry.request!==null &&
      ['sending','sent','queued','uncertain','failed'].includes(entry.status);
  }
  class Store{
    constructor(storage,now=()=>Date.now()/1000){this.storage=storage;this.now=now;}
    load(){
      const entries=[];
      try{
        for(let i=0;i<this.storage.length;i++){
          const key=this.storage.key(i);if(!key?.startsWith(prefix))continue;
          try{const entry=JSON.parse(this.storage.getItem(key));if(valid(entry) && key===prefix+entry.request_id && entry.created>this.now()-age)entries.push(entry);}catch{}
        }
      }catch{}
      const recent=entries.sort((a,b)=>b.created-a.created).slice(0,limit),kept=[];
      let used=0;
      for(const entry of recent){used+=JSON.stringify(entry).length*2;if(used>bytes)break;kept.push(entry);}
      return kept.reverse();
    }
    save(entry){
      if(!valid(entry))return false;
      try{
        const key=prefix+entry.request_id,previous=JSON.parse(this.storage.getItem(key)||'null');
        const value=Object.fromEntries(fields.filter(field=>entry[field]!==undefined).map(field=>[field,entry[field]]));
        if(value.attachments)value.attachments=value.attachments.filter(image=>/^[a-f0-9]{32}$/.test(image.id)&&typeof image.host==='string').slice(0,4).map(({id,host,name,width,height,type,size})=>({id,host,name,width,height,type,size}));
        // A late network error or stale tab cannot undo an acknowledged receipt.
        if(valid(previous)){
          if(accepted(previous) && !accepted(value)){value.status=previous.status;value.error='';}
          value.confirmed=Boolean(previous.confirmed || value.confirmed);
          value.dismissed=Boolean(previous.dismissed || value.dismissed);
        }
        const serialized=JSON.stringify(value);
        const isNew=this.storage.getItem(key)===null;
        if(serialized!==this.storage.getItem(key))this.storage.setItem(key,serialized);
        if(isNew)this.prune();
        return true;
      }catch{return false;}
    }
    prune(){
      try{
        const keep=new Set(this.load().map(entry=>prefix+entry.request_id));
        for(let i=this.storage.length-1;i>=0;i--){const key=this.storage.key(i);if(key?.startsWith(prefix)&&!keep.has(key))this.storage.removeItem(key);}
      }catch{}
    }
  }
  function apply(entry,result){
    if(result.status==='complete'){
      entry.status=entry.kind==='queue' && result.result?.delivery==='queued'?'queued':'sent';entry.error='';
      if(entry.kind==='supervisor'){entry.confirmed=true;entry.job_id=result.result?.job_id || entry.job_id;}
      entry.needsCheck=false;return;
    }
    if(!accepted(entry)){
      entry.status='uncertain';
      entry.error=result.status==='sending'?'Checking delivery…':result.status==='unknown'?'No delivery receipt yet. Check the conversation before resending.':'The send was interrupted. Check the conversation before resending.';
    }
  }
  const exports={Store,apply,accepted};
  if(typeof module==='object' && module.exports)module.exports=exports;else root.HubDelivery=exports;
})(globalThis);
