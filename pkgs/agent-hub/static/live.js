/* One resumable observation stream. Commands always use their explicit POST path. */
((root)=>{
  const subscription=query=>[query.agentId,Boolean(query.history),...(query.terminal?[true]:[])];
  const topics=query=>[['state'],...(query.previews || (query.agentId?[subscription(query)]:[])).map(item=>['preview',...item])];
  const fresh=(seen,query,now)=>topics(query).every(topic=>{const at=seen.get(JSON.stringify(topic));return at!==undefined && now-at<12000;});
  class Client {
    constructor({query,onState,onPreview,onError,EventSource=root.EventSource,now=()=>Date.now()}){
      Object.assign(this,{query,onState,onPreview,onError,EventSource,now});
      this.source=null;this.key='';this.connected=false;this.started=0;this.bases=new Map();this.seen=new Map();
    }
    healthyFor(query){return this.connected && fresh(this.seen,query,this.now());}
    get healthy(){return this.healthyFor(this.query());}
    close(){this.source?.close();this.source=null;this.connected=false;this.key='';this.bases.clear();this.seen.clear();}
    reconnect(){this.close();this.sync();}
    sync(){
      const query=this.query();
      if(!query.active || !this.EventSource){this.close();return;}
      const previews=query.previews || (query.agentId?[subscription(query)]:[]);
      const key=JSON.stringify(previews);
      // A socket heartbeat or another tab's updates cannot prove this chat is
      // current. Reopen a stalled subscription even if the socket stays open.
      if(this.source && this.key===key && (this.healthy || this.now()-this.started<12000))return;
      this.close();this.key=key;this.started=this.now();
      const source=this.source=new this.EventSource('/api/live?'+new URLSearchParams({previews:JSON.stringify(previews)}));
      source.onopen=()=>{if(this.source===source)this.connected=true;};
      source.onerror=()=>{if(this.source===source)this.connected=false;};
      const receive=(event,reset)=>{
        if(this.source!==source)return;
        try{
          const frames=reset?JSON.parse(event.data):[JSON.parse(event.data)];
          if(reset){this.bases.clear();this.seen.clear();}
          // A reset can contain both scopes in either order. State establishes
          // the current agent before its conversation is applied.
          frames.sort((a,b)=>(a.topic[0]==='state'?-1:0)-(b.topic[0]==='state'?-1:0));
          for(const frame of frames){
            const scope=JSON.stringify(frame.topic),base=this.bases.get(scope);
            const value=root.HubUpdates.apply(base,frame.packet);
            this.bases.set(scope,{version:frame.packet.version,value});
            this.connected=true;
            if(frame.error){this.seen.delete(scope);if(frame.topic[0]==='state')this.connected=false;this.onError?.(frame.topic,value.error || 'Live observation unavailable');continue;}
            this.seen.set(scope,this.now());
            if(frame.topic[0]==='state')this.onState(structuredClone(value));
            else if(frame.topic[0]==='preview')this.onPreview(frame.topic[1],Boolean(frame.topic[2]),structuredClone(value),Boolean(frame.topic[3]));
            if(this.source!==source)return;
          }
        }catch(error){this.onError?.(['stream'],error.message);this.close();}
      };
      source.addEventListener('reset',event=>receive(event,true));
      source.addEventListener('update',event=>receive(event,false));
      source.addEventListener('ping',()=>{});
    }
  }

  class Tabs {
    constructor(options){
      this.options=options;this.now=options.now || (()=>Date.now());this.id=root.crypto.randomUUID();this.peers=new Map();this.seen=new Map();this.lastHello=0;this.leader='';
      this.channel=options.BroadcastChannel===null?null:new (options.BroadcastChannel || root.BroadcastChannel)('agent-hub-live-v2');
      this.client=new Client({...options,query:()=>this.union(),
        onState:value=>{options.onState(value);this.channel?.postMessage({type:'state',from:this.id,value});},
        onPreview:(id,history,value,terminal)=>{options.onPreview(id,history,value,terminal);this.channel?.postMessage({type:'preview',from:this.id,id,history,value,terminal});},
        onError:(topic,error)=>{options.onError?.(topic,error);this.channel?.postMessage({type:'error',from:this.id,topic,error});}
      });
      if(this.channel){
        this.channel.onmessage=event=>this.receive(event.data);
        this.channel.postMessage({type:'hello',from:this.id});
      }
      this.pagehide=()=>{this.channel?.postMessage({type:'bye',from:this.id});this.client.close();};
      root.addEventListener?.('pagehide',this.pagehide);
    }
    get healthy(){return this.leader===this.id?this.client.healthyFor(this.options.query()):fresh(this.seen,this.options.query(),this.now());}
    union(){
      const active=[...this.peers.values()].filter(p=>p.query.active);
      const previews=[...new Map(active.filter(p=>p.query.agentId).map(p=>{const value=subscription(p.query);return [JSON.stringify(value),value];})).values()];
      previews.sort((a,b)=>JSON.stringify(a).localeCompare(JSON.stringify(b)));
      return {active:this.leader===this.id && active.length>0,previews:previews.slice(0,32)};
    }
    elect(){
      const now=this.now();
      for(const [id,peer] of this.peers)if(now-peer.at>6500)this.peers.delete(id);
      const leader=[...this.peers].filter(([,p])=>p.query.active).map(([id])=>id).sort()[0] || '';
      if(leader!==this.leader){this.leader=leader;this.seen.clear();}
      this.client.sync();
    }
    sync(){
      const query=this.options.query(),now=this.now(),previous=this.peers.get(this.id);
      this.peers.set(this.id,{query,at:now});
      if(now-this.lastHello>2000 || JSON.stringify(query)!==JSON.stringify(previous?.query)){
        this.channel?.postMessage({type:'presence',from:this.id,query});this.lastHello=now;
      }
      this.elect();
    }
    reconnect(){
      this.seen.clear();
      this.client.close();
      this.sync();
      if(this.leader!==this.id)this.channel?.postMessage({type:'refresh',from:this.id});
    }
    receive(message){
      if(!message || message.from===this.id)return;
      if(message.type==='hello'){
        this.channel?.postMessage({type:'presence',from:this.id,query:this.options.query()});return;
      }
      if(message.type==='presence'){
        this.peers.set(message.from,{query:message.query,at:this.now()});this.elect();return;
      }
      if(message.type==='bye'){this.peers.delete(message.from);this.elect();return;}
      if(message.type==='refresh'){
        if(this.leader===this.id && this.peers.get(message.from)?.query.active)this.client.reconnect();
        return;
      }
      if(message.from!==this.leader || this.leader===this.id || !this.options.query().active)return;
      if(message.type==='error'){this.seen.delete(JSON.stringify(message.topic));this.options.onError?.(message.topic,message.error);return;}
      const topic=message.type==='state'?['state']:message.type==='preview'?['preview',message.id,Boolean(message.history),...(message.terminal?[true]:[])]:null;
      if(!topic)return;
      this.seen.set(JSON.stringify(topic),this.now());
      while(this.seen.size>65)this.seen.delete(this.seen.keys().next().value);
      if(message.type==='state')this.options.onState(message.value);
      else this.options.onPreview(message.id,message.history,message.value,Boolean(message.terminal));
    }
    close(){this.channel?.postMessage({type:'bye',from:this.id});this.channel?.close();this.client.close();root.removeEventListener?.('pagehide',this.pagehide);}
  }

  async function microphone(){
    if(!root.navigator?.locks?.request)return ()=>{};
    return new Promise(resolve=>{
      root.navigator.locks.request('agent-hub-microphone',{ifAvailable:true},lock=>{
        if(!lock){resolve(null);return;}
        return new Promise(release=>resolve(release));
      }).catch(()=>resolve(null));
    });
  }
  const exports={Client,Tabs,microphone};
  if(typeof module==='object' && module.exports)module.exports=exports;else root.HubLive=exports;
})(globalThis);
