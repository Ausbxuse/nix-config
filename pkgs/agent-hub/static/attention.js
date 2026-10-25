/* A visible or fully spoken handoff is seen, not automatically reviewed. */
((root)=>{
  const key=item=>item?.id+':'+item?.version;
  class Attention {
    constructor({current,send,delay=700}){
      this.current=current;this.send=send;this.delay=delay;this.timer=null;this.waiting='';this.receipts=new Map();
    }
    sync(){
      const value=this.current(),identity=value?.visible?key(value.item):'';
      if(identity===this.waiting)return;
      clearTimeout(this.timer);this.waiting=identity;
      if(!identity || this.receipts.has('seen:'+identity))return;
      this.timer=setTimeout(()=>{
        this.waiting='';const latest=this.current();
        if(latest?.visible && key(latest.item)===identity)this.record(latest.item,'seen');
      },this.delay);
    }
    async record(item,mode){
      if(!item)return false;
      const identity=mode+':'+key(item);
      if(this.receipts.has(identity))return true;
      this.receipts.set(identity,true);
      while(this.receipts.size>200)this.receipts.delete(this.receipts.keys().next().value);
      try{
        await this.send({attention:{id:item.id,agent_id:item.agent_id,version:item.version,mode}});
        return true;
      }catch{this.receipts.delete(identity);return false;}
    }
    close(){clearTimeout(this.timer);this.waiting='';}
  }
  function visibleEnd(node,container){
    if(!node?.getClientRects().length || !container?.getClientRects().length)return false;
    const box=node.getBoundingClientRect(),bounds=container.getBoundingClientRect();
    return box.bottom>bounds.top+32 && box.bottom<=Math.min(bounds.bottom,root.innerHeight)+2;
  }
  async function source(target,messages){
    if(!target?.message_id || !target.message_digest || !root.crypto?.subtle)return null;
    const message=messages.find(m=>String(m.id)===target.message_id && m.role==='assistant');
    if(!message)return null;
    const digest=[...new Uint8Array(await root.crypto.subtle.digest('SHA-256',new TextEncoder().encode(message.text)))].map(x=>x.toString(16).padStart(2,'0')).join('');
    return digest===target.message_digest?message:null;
  }
  function sameTurn(item,agent){
    const prefix=agent?.state+':';
    return Boolean(item?.version?.startsWith(prefix) && Number(item.version.slice(prefix.length))===(agent?.changed_at ?? 0));
  }
  function pendingAgents(snapshot){
    const items=snapshot?.handoffs?.items,now=snapshot?.server_time ?? Date.now()/1000;
    const handoffs=new Map((items || []).map(item=>[item.agent_id,item]));
    return new Set((snapshot?.agents || []).filter(agent=>{
      if(!agent.connection_online || agent.acknowledged || agent.activity_confirmed ||
         ['retrying','checking','waiting'].includes(agent.recovery?.status))return false;
      // Reading clears the unread marker. Review remains a separate action;
      // a newer handoff has its own identity and lights up again.
      if(Array.isArray(items)){
        const item=handoffs.get(agent.id);
        return Boolean(item && !item.seen && !item.handled && !(item.deferred_until>now) &&
          sameTurn(item,agent));
      }
      return Boolean(agent.needs_attention);
    }).map(agent=>agent.id));
  }
  const exports={Attention,visibleEnd,source,pendingAgents,sameTurn};
  if(typeof module==='object' && module.exports)module.exports=exports;else root.HubAttention=exports;
})(globalThis);
