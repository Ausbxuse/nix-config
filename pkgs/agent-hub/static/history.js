/* Page visible conversation entries. No generated summaries or text guesses. */
((root)=>{
  const content=typeof module==='object'&&module.exports?require('./message-content.js'):root.HubMessageContent;
  const key=message=>message.role+':'+message.id;
  const visible=content.visible;
  const progress=message=>message.role==='tool'||message.role==='assistant'&&(message.phase==='commentary'||message.channel==='commentary');
  const sourceKey=source=>source?JSON.stringify([source.host,source.session,source.generation]):'';
  class Preview {
    constructor(){this.reset();}
    reset(){this.original=null;}
    update(value){
      const previous=this.original,structured=Boolean(value.conversation_id||value.session_verified||value.messages?.length);
      const same=previous&&(value.conversation_id&&previous.conversation_id?
        value.conversation_id===previous.conversation_id:
        value.history_source&&sourceKey(value.history_source)===sourceKey(previous.history_source));
      const generationChanged=same&&value.history_source&&previous.history_source&&
        sourceKey(value.history_source)!==sourceKey(previous.history_source);
      // Capturing a repaint or an older matching terminal reply cannot replace
      // a formatted conversation. This cache is reset whenever the user changes
      // agents; it never supplies identity for an action sent to the runtime.
      const last=value.messages?.at(-1),oldLast=previous?.messages?.at(-1);
      const regressed=same&&!generationChanged&&last&&oldLast&&key(last)!==key(oldLast)&&
        previous.messages.some(message=>key(message)===key(last))&&
        !value.messages.some(message=>key(message)===key(oldLast));
      if(previous&&(!structured||regressed)){
        return {...value,...previous,conversation_pending:true};
      }
      if(!structured)return value;
      const history_source=value.history_source||(
        same&&!generationChanged&&value.conversation_id?previous.history_source:undefined);
      this.original={messages:value.messages||[],source:value.source,session_verified:value.session_verified,
        conversation_id:value.conversation_id,history_source,facts:value.facts};
      return {...value,...this.original,conversation_pending:false};
    }
  }
  function groups(messages,decision=false){
    const result=[];
    for(const message of content.messages(messages)){
      const previous=result.at(-1);
      if(progress(message)&&previous?.type==='progress')previous.messages.push(message);
      else result.push({key:(progress(message)?'progress:':'')+key(message),type:progress(message)?'progress':'message',messages:[message]});
    }
    const last=result.at(-1);
    if(decision && last?.type==='progress' && last.messages.at(-1)?.role==='assistant'){
      const message=last.messages.pop();if(!last.messages.length)result.pop();
      result.push({key:key(message),type:'message',messages:[message]});
    }
    return result;
  }
  class Window {
    constructor({limit=32,page=16,characters=180000}={}){this.limit=limit;this.page=page;this.characters=characters;this.groups=[];this.start=0;this.end=0;}
    bounded(start,end,backward=false){
      const selected=this.groups;start=Math.max(0,start);end=Math.min(selected.length,end);
      if(backward)start=Math.max(start,end-this.limit);else end=Math.min(end,start+this.limit);
      const cost=group=>group.type==='progress'?0:group.messages[0].text.length;
      let size=0;
      if(backward){let next=end;while(next>start&&(next===end||size+cost(selected[next-1])<=this.characters)){size+=cost(selected[--next]);}start=next;}
      else{let next=start;while(next<end&&(next===start||size+cost(selected[next])<=this.characters)){size+=cost(selected[next++]);}end=next;}
      this.start=start;this.end=end;
    }
    update(groups,follow=false,anchor=''){
      const first=this.groups[this.start]?.key,last=this.groups[this.end-1]?.key,atLatest=this.end===this.groups.length;
      this.groups=groups;
      if(follow||!first){this.latest();return;}
      // Holding the viewport is not paging into the past. Keep the live page
      // current while preserving the message being read when it still fits.
      if(atLatest){this.latest();if(anchor)this.reveal(anchor);return;}
      const start=groups.findIndex(group=>group.key===first),end=groups.findIndex(group=>group.key===last);
      if(start<0&&end<0)this.latest();
      else this.bounded(start<0?Math.max(0,end-this.limit+1):start,end<0?start+this.limit:end+1);
      if(anchor)this.reveal(anchor);
    }
    latest(){this.bounded(0,this.groups.length,true);}
    older(){
      if(!this.start)return false;
      const previous=this.start,step=Math.min(this.page,Math.max(1,Math.floor((this.end-this.start)/2)));
      this.bounded(0,this.end-step,true);
      // Cost varies by reply. Fill toward the previous page, never leave a gap.
      if(this.start>=previous)this.bounded(0,previous,true);
      return true;
    }
    newer(){
      if(this.end===this.groups.length)return false;
      const previous=this.end,step=Math.min(this.page,Math.max(1,Math.floor((this.end-this.start)/2)));
      this.bounded(this.start+step,this.groups.length);
      if(this.end<=previous)this.bounded(previous,this.groups.length);
      return true;
    }
    reveal(identity){
      const index=this.groups.findIndex(group=>group.key===identity||group.messages.some(message=>key(message)===identity));
      if(index<0)return false;
      if(index<this.start||index>=this.end)this.bounded(index,index+this.limit);
      return true;
    }
    get view(){return this.groups.slice(this.start,this.end);}
  }
  class Timeline {
    constructor({limit=256,characters=2000000}={}){this.limit=limit;this.characters=characters;this.source=null;this.live=[];this.buffer=null;}
    update(messages,source){
      const identity=sourceKey(source);
      const changed=this.identity!==identity;
      if(changed)this.buffer=null;
      this.identity=identity;this.source=source||null;this.live=messages;
      return changed;
    }
    get messages(){return this.buffer?.messages||this.live;}
    get past(){return Boolean(this.buffer);}
    get earlier(){return this.buffer?this.buffer.earlier:Boolean(this.source?.earlier);}
    get later(){return Boolean(this.buffer?.later);}
    latest(){this.buffer=null;}
    request(direction){
      if(!this.source||!(direction==='older'?this.earlier:this.later))return null;
      const messages=this.messages.filter(message=>['user','assistant'].includes(message.role));
      const anchor=direction==='older'?(this.buffer?messages[0]?.id:this.source.anchor):messages.at(-1)?.id;
      if(!anchor)return null;
      return {identity:this.identity,direction,base:{messages,earlier:this.earlier,later:this.later},
        parameters:{host:this.source.host,session:this.source.session,generation:this.source.generation,message:anchor,direction:direction==='older'?'before':'after'}};
    }
    accept(request,page){
      if(request.identity!==this.identity)return false;
      if(page.session!==this.source.session||page.generation!==this.source.generation||page.host!==this.source.host)throw Error('This history changed. Return to Latest and try again.');
      const older=request.direction==='older',seen=new Set(),incoming=content.messages(page.messages);
      const messages=(older?[...incoming,...request.base.messages]:[...request.base.messages,...incoming]).filter(message=>{const id=key(message);if(seen.has(id))return false;seen.add(id);return true;});
      let earlier=older?incoming.length>0&&page.earlier:request.base.earlier,later=older?request.base.later:incoming.length>0&&page.later;
      let size=messages.reduce((sum,message)=>sum+message.text.length,0);
      while(messages.length>1&&(messages.length>this.limit||size>this.characters)){
        size-=(older?messages.pop():messages.shift()).text.length;
        if(older)later=true;else earlier=true;
      }
      this.buffer={messages,earlier:Boolean(earlier),later:Boolean(later)};
      return true;
    }
  }
  const api={groups,Window,Timeline,Preview,key,visible,progress};
  if(typeof module==='object'&&module.exports)module.exports=api;else root.HubHistory=api;
})(globalThis);
