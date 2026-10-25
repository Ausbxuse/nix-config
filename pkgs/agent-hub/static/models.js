/* Session controls: every Apply is bound to the agent and settings we read. */
(() => {
  const button=document.createElement('button');button.id='agent-model-settings';button.type='button';
  button.innerHTML='<svg class="icon" aria-hidden="true"><use href="#i-settings"/></svg>Model &amp; effort';
  $('task-menu').prepend(button);
  const dialog=document.createElement('dialog');dialog.id='agent-model-dialog';dialog.setAttribute('aria-labelledby','agent-model-title');
  dialog.innerHTML='<form id="agent-model-form"><header><div><strong id="agent-model-title">Model &amp; effort</strong><p id="agent-model-target"></p></div><button type="button" id="agent-model-close" class="icon-button" aria-label="Close model settings"><svg class="icon" aria-hidden="true"><use href="#i-close"/></svg></button></header><p id="agent-model-status" role="status">Loading…</p><div id="agent-model-fields" hidden><label for="agent-model-choice">Model</label><select id="agent-model-choice"></select><label for="agent-effort-choice">Effort</label><select id="agent-effort-choice"></select></div><p id="agent-model-note"></p><footer><button type="button" id="agent-model-refresh" class="text-button">Refresh</button><button type="submit" id="agent-model-apply" class="primary-button" disabled>Apply</button></footer></form>';
  document.body.append(dialog);
  const model=$('agent-model-choice'),effort=$('agent-effort-choice'),apply=$('agent-model-apply'),status=$('agent-model-status');
  let target='',settings=null,generation=0,saving=false;
  const labels={low:'Low',medium:'Medium',high:'High',xhigh:'Extra high',max:'Max',ultra:'Ultra',auto:'Auto',ultracode:'Ultracode (extra high + workflows)'};
  const option=(value,label)=>{const node=document.createElement('option');node.value=value ?? '';node.textContent=label;return node;};
  const selected=()=>settings?.models.find(row=>row.id===model.value);
  function update(){
    const choice=selected(),initial=settings?.models.find(row=>row.id===settings.model);
    const same=settings && model.value===settings.model && (effort.value || null)===(settings.effort ?? initial?.default_effort ?? null);
    apply.disabled=saving || !choice || same;
  }
  function efforts(preferred){
    const choice=selected();effort.replaceChildren();
    for(const value of choice?.efforts || [])effort.append(option(value,labels[value] || value));
    if(!effort.options.length)effort.append(option(null,'Default'));
    effort.value=(choice?.efforts.includes(preferred)?preferred:choice?.default_effort) || '';
    effort.disabled=saving || !choice?.efforts.length;update();
  }
  async function load(){
    const ticket=++generation;settings=null;status.textContent='Loading…';status.hidden=false;
    $('agent-model-fields').hidden=true;$('agent-model-note').textContent='';apply.disabled=true;
    $('agent-model-refresh').disabled=true;
    try{
      const result=await api('/api/action',{type:'model_options',agent_id:target,command_id:requestId()});
      if(ticket!==generation || !dialog.open)return;
      settings=result.settings;model.replaceChildren();
      for(const choice of settings.models)model.append(option(choice.id,choice.label));
      if(!settings.models.some(row=>row.id===settings.model)){
        const current=option(settings.model,settings.model+' (current)');current.disabled=true;model.prepend(current);
      }
      model.value=settings.model;model.disabled=false;efforts(settings.effort);
      $('agent-model-note').textContent=settings.note || '';$('agent-model-fields').hidden=false;status.hidden=true;
    }catch(error){if(ticket===generation)status.textContent=error.message;}
    finally{if(ticket===generation)$('agent-model-refresh').disabled=false;}
  }
  button.onclick=()=>{
    const agent=agentById(state.selected);if(!agent)return;
    document.querySelector('.workspace-menu').open=false;
    target=agent.id;$('agent-model-target').textContent=agentTitle(agent)+' · '+agent.connection_label;
    dialog.showModal();load();
  };
  $('agent-model-close').onclick=()=>dialog.close();
  dialog.addEventListener('cancel',event=>{if(saving)event.preventDefault();});
  dialog.addEventListener('close',()=>{generation++;settings=null;});
  $('agent-model-refresh').onclick=load;
  model.onchange=()=>efforts(effort.value);effort.onchange=update;
  $('agent-model-form').onsubmit=async event=>{
    event.preventDefault();if(apply.disabled || !settings)return;
    const ticket=generation;
    saving=true;apply.disabled=true;model.disabled=effort.disabled=true;
    $('agent-model-close').disabled=$('agent-model-refresh').disabled=true;
    status.textContent='Applying…';status.hidden=false;
    try{
      const result=await api('/api/action',{type:'model_set',agent_id:target,command_id:requestId(),
        session:settings.session,version:settings.version,model:model.value,effort:effort.value || null});
      if(ticket!==generation)return;
      dialog.close();toast(result.message);await poll();
    }catch(error){
      if(ticket===generation){status.textContent=error.message;settings=null;}
    }finally{
      saving=false;$('agent-model-close').disabled=$('agent-model-refresh').disabled=false;
      model.disabled=!settings;effort.disabled=!settings || !selected()?.efforts.length;update();
    }
  };
})();
