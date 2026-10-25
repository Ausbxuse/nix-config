/* Small, editable assistant memory. All changes round-trip through the controller. */
(() => {
  const section=document.createElement('section');section.id='assistant-settings';section.className='status-section';section.hidden=true;
  section.innerHTML=`<h2>ARIA</h2>
    <form id="attention-form" class="assistant-form"><div class="assistant-inline"><label>Updates<select id="attention-mode"><option value="normal">All handoffs</option><option value="focus">Focus</option><option value="driving">Driving</option><option value="quiet">Quiet</option></select></label><label id="attention-duration-label">For<select id="attention-duration"><option value="15">15 minutes</option><option value="60">1 hour</option><option value="120" selected>2 hours</option><option value="480">8 hours</option></select></label><button class="text-button" type="submit">Apply</button></div><label id="attention-topic-label" hidden>Focus on<input id="attention-topic" maxlength="320" placeholder="NeMo checkpoint comparison"></label><p id="attention-description" class="muted"></p></form>
    <details id="assistant-memory"><summary>Saved requests <span id="assistant-memory-count"></span></summary><div id="assistant-records"></div><form id="assistant-record-form" class="assistant-form"><label class="sr-only" for="assistant-record-text">Request to remember</label><textarea id="assistant-record-text" rows="2" maxlength="320" required placeholder="What should I keep in mind?"></textarea><div class="assistant-inline"><select id="assistant-record-kind" aria-label="Request type"><option value="priority">Priority</option><option value="watch">Watch for</option></select><select id="assistant-record-agent" aria-label="Scope"><option value="">Across agents</option></select><button type="submit" class="text-button">Save</button><button type="button" id="assistant-edit-cancel" class="text-button" hidden>Cancel</button></div></form><p class="muted">Your words only. Watch requests are checked at handoffs and stay until you remove them.</p></details>
    <details id="assistant-tracking"><summary>Instructions <span id="assistant-tracking-count"></span></summary><div id="assistant-commitments"></div><p class="muted">Tracks instructions sent through ARIA. A response is ready for your review; it does not prove the task is complete.</p></details><p id="assistant-error" role="status" class="muted"></p>`;
  document.querySelector('.status-grid').prepend(section);
  const byId=id=>document.getElementById(id),escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const modeNames={normal:'All handoffs',focus:'Focus',driving:'Driving',quiet:'Quiet'};
  const statusNames={sending:'Sending',unconfirmed:'Unconfirmed',delivered:'Delivered',queued:'Queued',started:'Started',response:'Response available',paused:'Paused',closed:'Closed'};
  const stamp=value=>new Date(value*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
  let current=null,editing='',signature='',modeRevision=-1,agentOptions='';
  function controls(){const mode=byId('attention-mode').value;byId('attention-topic-label').hidden=mode!=='focus';byId('attention-duration-label').hidden=mode==='normal';}
  byId('attention-mode').onchange=controls;
  async function change(value){
    try{current=await window.api('/api/assistant',{...value,revision:current.revision});render(current);await poll();return true;}
    catch(error){byId('assistant-error').textContent=error.message;return false;}
  }
  byId('attention-form').onsubmit=async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;try{await change({action:'attention',mode:byId('attention-mode').value,minutes:Number(byId('attention-duration').value),text:byId('attention-mode').value==='focus'?byId('attention-topic').value:''});}finally{button.disabled=false;}};
  byId('assistant-record-form').onsubmit=async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;try{if(await change({action:'remember',id:editing,kind:byId('assistant-record-kind').value,text:byId('assistant-record-text').value,agent_id:byId('assistant-record-agent').value}))resetEditor();}finally{button.disabled=false;}};
  function resetEditor(){editing='';byId('assistant-record-text').value='';byId('assistant-edit-cancel').hidden=true;}
  byId('assistant-edit-cancel').onclick=resetEditor;
  section.addEventListener('click',async event=>{
    const edit=event.target.closest('[data-assistant-edit]'),remove=event.target.closest('[data-assistant-remove]'),close=event.target.closest('[data-assistant-close]'),open=event.target.closest('[data-assistant-open]');
    if(edit){const record=current.records.find(r=>r.id===edit.dataset.assistantEdit);if(!record)return;editing=record.id;byId('assistant-record-text').value=record.text;byId('assistant-record-kind').value=record.kind;byId('assistant-record-agent').value=record.agent_id;byId('assistant-edit-cancel').hidden=false;byId('assistant-record-text').focus();}
    if(remove){remove.disabled=true;try{await change({action:'forget',id:remove.dataset.assistantRemove});}finally{remove.disabled=false;}}
    if(close){close.disabled=true;try{await change({action:'close',id:close.dataset.assistantClose});}finally{close.disabled=false;}}
    if(open && agentById(open.dataset.assistantOpen))openAgent(open.dataset.assistantOpen);
  });
  function render(value){
    current=value;section.hidden=!value?.available;if(section.hidden)return;
    const attention=value.attention||{},mode=attention.mode||'normal';
    if(modeRevision!==value.revision || (!section.contains(document.activeElement) && byId('attention-mode').value!==mode)){
      byId('attention-mode').value=mode;byId('attention-topic').value=attention.text||'';modeRevision=value.revision;controls();
    }
    byId('attention-description').textContent=(mode==='normal'?'Meaningful handoffs only.':`${modeNames[mode]} until ${stamp(attention.until)}. `+({quiet:'Held updates stay in your inbox.',focus:`Only ${attention.text} interrupts you.`,driving:'Brief spoken handoffs while your call is open.'}[mode]||''));
    byId('assistant-error').textContent=value.error||'';
    const agents=state.snapshot?.agents||[],options='<option value="">Across agents</option>'+agents.map(a=>`<option value="${escape(a.id)}">${escape(agentTitle(a)+' · '+a.connection_label)}</option>`).join('');
    if(options!==agentOptions){const selected=byId('assistant-record-agent').value;byId('assistant-record-agent').innerHTML=options;byId('assistant-record-agent').value=selected;agentOptions=options;}
    const next=JSON.stringify([value.records,value.commitments,agents.map(a=>[a.id,a.display_title,a.connection_label])]);if(next===signature)return;signature=next;
    byId('assistant-memory-count').textContent=value.records.length?`· ${value.records.length}`:'';
    replaceMarkup(byId('assistant-records'),value.records.map(r=>`<article class="assistant-record" data-assistant-record="${escape(r.id)}"><p>${escape(r.text)}</p><div class="assistant-record-meta"><span>${r.kind==='watch'?'Watch':'Priority'}${r.agent_id?' · '+escape(agentTitle(agentById(r.agent_id))):''}</span><button type="button" class="text-button" data-assistant-edit="${escape(r.id)}">Edit</button><button type="button" class="text-button" data-assistant-remove="${escape(r.id)}">Remove</button></div><details class="assistant-source"><summary>From you · ${stamp(r.source.created)}</summary><p>${escape(r.source.quote)}</p></details></article>`).join('')||'<p class="muted">Nothing saved yet.</p>','data-assistant-record');
    byId('assistant-tracking-count').textContent=value.commitments.length?`· ${value.commitments.length}`:'';
    replaceMarkup(byId('assistant-commitments'),value.commitments.map(r=>{const agent=agentById(r.agent_id);return `<article class="assistant-record" data-assistant-commitment="${escape(r.id)}"><div class="assistant-record-meta"><span class="commitment-state" data-state="${escape(r.status)}">${escape(statusNames[r.status]||r.status)}</span><time>${stamp(r.created)}</time></div><p class="assistant-instruction">${escape(r.text||'Pause the agent')}</p><p class="muted">${escape(r.observation||r.result)}</p><div class="assistant-record-meta">${agent?`<button type="button" class="text-button" data-assistant-open="${escape(r.agent_id)}">${escape(agentTitle(agent)+' · '+agent.connection_label)}</button>`:'<span>Agent unavailable</span>'}<button type="button" class="text-button" data-assistant-close="${escape(r.id)}">Dismiss</button></div></article>`;}).join('')||'<p class="muted">No instructions tracked yet.</p>','data-assistant-commitment');
  }
  window.HubAssistant={changed:()=>render(state.snapshot?.assistant)};
})();
