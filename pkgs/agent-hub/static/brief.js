/* A compact review aid with links to its exact conversation sources. */
((root)=>{
  const content=typeof module==='object'&&module.exports?require('./message-content.js'):root.HubMessageContent;
  const escape=text=>String(text).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const plain=text=>String(text).replace(/!?\[([^\]]*)\]\([^)]*\)/g,'$1').replace(/[*`]/g,'').replace(/\s+/g,' ').trim();
  const clipped=text=>text.length<=180?text:text.slice(0,177).replace(/\s+\S*$/,'')+'…';
  const labels={request:'Asked',change:'Reported',decision:'Decision'};
  async function digest(text){
    if(!root.crypto?.subtle)return null;
    const bytes=await root.crypto.subtle.digest('SHA-256',new TextEncoder().encode(text));
    return [...new Uint8Array(bytes)].map(value=>value.toString(16).padStart(2,'0')).join('').slice(0,24);
  }
  async function verified(value,messages){
    if(!Array.isArray(value?.fields) || value.fields.length>4)return [];
    const sources=new Map(messages.map(message=>[message.id,message]));
    const visible=messages.filter(content.visible);
    const lastUser=visible.findLast(message=>message.role==='user'),lastReply=visible.findLast(message=>message.role==='assistant');
    const hashes=new Map();
    for(const field of value.fields){
      if(!field || typeof field!=='object' || !['request','change','decision','earlier_request'].includes(field.kind))return [];
      const source=field.source,message=sources.get(source?.message_id);
      if((field.kind==='request' && source?.message_id!==lastUser?.id) || (['change','decision'].includes(field.kind) && source?.message_id!==lastReply?.id))return [];
      if(!message || typeof message.text!=='string' || message.role!==source.role || !Number.isInteger(source.start) || !Number.isInteger(source.end))return [];
      const text=[...message.text];
      if(source.start<0 || source.end<=source.start || source.end>text.length || text.slice(source.start,source.end).join('')!==field.text)return [];
      if(!hashes.has(message.id))hashes.set(message.id,await digest(message.text));
      if(!hashes.get(message.id) || hashes.get(message.id)!==source.digest)return [];
    }
    return value.fields.filter(field=>labels[field.kind]);
  }
  function terminalFields(text){
    const blocks=root.AgentReader.parse(text),reply=blocks.findLastIndex(block=>block.type==='assistant');
    if(reply<0)return {fields:[],reply:''};
    const request=blocks.slice(0,reply).findLastIndex(block=>block.type==='input');
    const field=(kind,index,quote)=>({kind,text:quote,truncated:quote.length>240,source:{terminal:index,role:kind==='request'?'user':'assistant',text:blocks[index].text}});
    const fields=[];
    if(request>=0)fields.push(field('request',request,blocks[request].text));
    let fence=false;
    const lines=blocks[reply].text.split('\n').filter(line=>{
      if(/^\s*(?:```|~~~)/.test(line)){fence=!fence;return false;}
      return !fence && line.trim().length>=12 && !/^\s*[>|<#]/.test(line);
    }).map(line=>line.replace(/^\s*(?:[-*+] |\d+[.)] )/,'').trim());
    const question=text=>/^(?:\*\*)?(?:(?:Question|Decision|Next step):\s*)?(?:Which\b|Would you\b|Do you\b|Can you\b|Could you\b|Should (?:I|we)\b|Please (?:confirm|choose|approve|provide)\b|I need (?:you|your)\b)/i.test(text);
    const candidates=lines.filter(text=>!question(text) && !/^(?:I(?:'ll| will)|Next[, :]|Let me|Going to)/i.test(text));
    const change=candidates.find(text=>/\b(?:passed|failed|saved|added|removed|fixed|updated|measured|trained|evaluated|completed|deployed|changed|reduced|ready)\b/i.test(text)) || candidates[0];
    const decision=lines.findLast(question);
    if(change)fields.push(field('change',reply,change));
    if(decision)fields.push(field('decision',reply,decision));
    return {fields,reply:blocks[reply].text};
  }
  function useful(fields,reply,visible=()=>false){
    // A short reply already is its own brief. Sources actually visible in the
    // reader need no duplicate, including wide desktop layouts.
    const text=plain(reply);
    if(text.length<650 || root.AgentReader.shortReply(text) || !fields.some(field=>field.kind==='change'))return [];
    return fields.filter(field=>!visible(field));
  }
  function summaryUseful(summary,reply){
    const words=text=>String(text).trim().split(/\s+/u).length;
    return Boolean(summary?.trim() && !root.AgentReader.shortReply(reply) &&
      words(summary)*2<=words(reply) && summary.trim().length*2<=reply.trim().length);
  }
  function usefulUpdate(update,messages=[]){
    const lastUser=messages.findLastIndex(message=>message.role==='user');
    const reply=messages.slice(lastUser+1).findLast(message=>message.role==='assistant' &&
      ![message.phase,message.channel].some(value=>['commentary','analysis','reasoning','thinking','reasoning_summary'].includes(value)));
    // A cached Claude handoff can outlive its turn. Only the current reply's
    // own brief belongs here, even if a quick turn escaped the status poll.
    if(!reply || update.original_reply || !update.sources?.some(source=>source.message_id===reply.id))return false;
    const body=update.body || String(update.text || '').split('\n').slice(1).join('\n');
    return summaryUseful(body,reply.text);
  }
  function markup(fields){
    return '<dl>'+fields.map((field,index)=>`<div class="brief-row" data-kind="${field.kind}"><dt>${labels[field.kind]}</dt><dd><span>${escape(clipped(plain(field.text)))}${field.truncated && plain(field.text).length<=180?'…':''}</span><button type="button" class="brief-source" data-brief-source="${index}" aria-label="Open source for ${labels[field.kind].toLowerCase()}" title="Open source"><svg class="icon" aria-hidden="true"><use href="#i-arrow"/></svg></button></dd></div>`).join('')+'</dl>';
  }
  async function render(node,{facts,messages=[],text='',version,viewport,handoff,agentId,enabled=true,visible},afterRender){
    const signature=[enabled,agentId,handoff?.id,facts?.version,version ?? messages,messages.length?null:text,viewport];
    if(node._signature?.every((value,index)=>value===signature[index]))return;
    node._signature=signature;
    const ticket={};node._ticket=ticket;
    node.hidden=true;node._fields=[];
    if(!enabled || !handoff || handoff.agent_id!==agentId)return;
    let fields,reply;
    if(messages.length){
      if(facts?.agent_id!==agentId)return;
      fields=await verified(facts,messages);
      reply=messages.findLast(m=>m.role==='assistant')?.text || '';
    }else({fields,reply}=terminalFields(text));
    fields=useful(fields,reply,visible);
    if(node._ticket!==ticket || !fields.length)return;
    node.innerHTML=markup(fields);node._fields=fields;node.hidden=false;
    afterRender?.();
  }
  function sourceNode(field,container,messages,text){
    if(field.source.terminal!==undefined){
      const block=root.AgentReader.parse(text)[field.source.terminal];
      if(!block || block.text!==field.source.text || block.type!==(field.kind==='request'?'input':'assistant'))return null;
      return [...container.querySelectorAll('[data-terminal-index]')].find(node=>Number(node.dataset.terminalIndex)===field.source.terminal);
    }
    const message=messages.find(message=>message.id===field.source.message_id && message.role===field.source.role);
    if(!message)return null;
    return [...container.querySelectorAll('[data-message-id]')].find(node=>node.dataset.messageId===message.role+':'+message.id);
  }
  function sourceVisible(field,container,messages,text,reader){
    const node=sourceNode(field,container,messages,text),quote=plain(field.text);
    if(!node)return false;
    const bounds=reader.getBoundingClientRect();
    return [...node.querySelectorAll('.reader-prose p,.reader-prose li')].some(part=>{
      if(!part.getClientRects().length || !plain(part.textContent).includes(quote))return false;
      const rect=part.getBoundingClientRect();
      return rect.top>=bounds.top+4 && rect.bottom<=bounds.bottom-4;
    });
  }
  const exports={verified,terminalFields,useful,summaryUseful,usefulUpdate,markup,render,sourceNode,sourceVisible};
  if(typeof module==='object' && module.exports)module.exports=exports;else root.HubBrief=exports;
})(globalThis);
