/* Task handoffs and host-scoped sign-in. Neither control runs on selection. */
(() => {
  const icon=name=>`<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const menuButton=(id,label,name)=>{
    const button=document.createElement('button');button.id=id;button.type='button';
    button.innerHTML=icon(name)+label;$('task-menu').append(button);return button;
  };
  const makeDialog=(id,title,body)=>{
    const dialog=document.createElement('dialog');dialog.id=id;dialog.className='agent-switch-dialog';
    dialog.setAttribute('aria-labelledby',id+'-title');
    dialog.innerHTML=`<header><div><strong id="${id}-title">${title}</strong><p class="switch-target"></p></div><button type="button" class="icon-button switch-close" aria-label="Close ${title.toLowerCase()}">${icon('close')}</button></header>${body}`;
    document.body.append(dialog);dialog.querySelector('.switch-close').onclick=()=>dialog.close();return dialog;
  };
  const handoff=makeDialog('agent-switch-dialog','Switch agent',
    '<form id="agent-switch-form"><label for="switch-runtime">Continue with</label><select id="switch-runtime"><option value="codex">Codex</option><option value="claude">Claude</option></select><label for="switch-context">Task and context</label><textarea id="switch-context" rows="9" maxlength="10000" placeholder="What should the new agent continue?"></textarea><p class="switch-note">Same project and host. The original conversation stays available.</p><p id="switch-status" class="switch-note" role="status"></p><footer><button type="button" id="switch-refresh" class="text-button">Refresh context</button><button type="submit" id="switch-apply" class="primary-button" disabled>Switch</button></footer></form>');
  const account=makeDialog('codex-account-dialog','Codex account',
    '<div class="account-current"><span id="codex-account-label">Loading…</span><span id="codex-account-plan" class="switch-note"></span></div><p class="switch-note">Applies to all Codex agents on this host. Existing turns may finish on the previous account.</p><div id="codex-signin" hidden><p class="switch-note">Open sign-in and choose the account you want on this host.</p><div class="account-code"><code id="codex-login-code"></code><button type="button" class="icon-button" id="codex-copy-code" aria-label="Copy sign-in code">'+icon('copy')+'</button></div><a id="codex-login-link" class="primary-button" target="_blank" rel="noopener noreferrer">Open sign-in</a></div><p id="codex-account-status" class="switch-note" role="status"></p><footer><button type="button" id="codex-account-refresh" class="text-button">Refresh</button><button type="button" id="codex-account-start" class="primary-button" disabled>Sign in to another account</button><button type="button" id="codex-account-cancel" class="text-button" hidden>Cancel sign-in</button></footer>');
  let source='',handoffInfo=null,handoffEpoch=0,handoffBusy=false,originalContext='';
  let accountSource='',accountInfo=null,accountEpoch=0,accountBusy=false,timer=0;
  const info=(id,kind)=>api('/api/agent-switch?'+new URLSearchParams({agent_id:id,kind}));
  const hostTitle=id=>{const agent=agentById(id);return agent?agentTitle(agent)+' · '+agent.connection_label:'';};
  function handoffControls(){
    const data=handoffInfo;
    $('switch-apply').disabled=handoffBusy||!data||Boolean(data.queued||data.blocked)||!$('switch-context').value.trim();
    $('switch-apply').textContent=data?.busy?'Pause & review':'Switch to '+($('switch-runtime').value==='codex'?'Codex':'Claude');
    $('switch-refresh').disabled=handoffBusy;$('switch-runtime').disabled=handoffBusy||!data;
    handoff.querySelector('.switch-close').disabled=handoffBusy;
  }
  async function loadHandoff(keepEdit=false){
    const epoch=++handoffEpoch,edited=keepEdit&&$('switch-context').value!==originalContext;
    handoffInfo=null;$('switch-status').textContent='Reading conversation…';handoffControls();
    try{
      const result=await info(source,'handoff');if(epoch!==handoffEpoch||!handoff.open)return;
      handoffInfo=result;
      if(!edited)$('switch-context').value=result.context;
      originalContext=result.context;
      $('switch-runtime').value=result.target;
      for(const option of $('switch-runtime').options)option.disabled=option.value===result.runtime;
      $('switch-status').textContent=result.blocked|| (result.queued?`${result.queued} queued message${result.queued===1?'':'s'} stay with this agent. Finish or remove them before switching.`:
        result.busy?'Pause this agent before handing off its work.':!result.context?'Write a handoff here; this conversation is not linked yet.':edited?'Context refreshed. Your edits are kept.':'');
    }catch(error){if(epoch===handoffEpoch)$('switch-status').textContent=error.message;}
    finally{if(epoch===handoffEpoch)handoffControls();}
  }
  menuButton('switch-agent','Switch agent','arrow').onclick=()=>{
    if(!state.selected)return;source=state.selected;originalContext='';$('switch-context').value='';
    document.querySelector('.workspace-menu').open=false;handoff.querySelector('.switch-target').textContent=hostTitle(source);
    handoff.showModal();loadHandoff();
  };
  $('switch-context').oninput=handoffControls;$('switch-runtime').onchange=handoffControls;
  $('switch-refresh').onclick=()=>loadHandoff(true);
  handoff.addEventListener('cancel',event=>{if(handoffBusy)event.preventDefault();});
  handoff.addEventListener('close',()=>{handoffEpoch++;handoffInfo=null;});
  $('agent-switch-form').onsubmit=async event=>{
    event.preventDefault();if($('switch-apply').disabled)return;
    handoffBusy=true;handoffControls();
    try{
      if(handoffInfo.busy){
        await api('/api/action',{type:'interrupt',agent_id:source,command_id:requestId()});
        for(let attempt=0;attempt<6;attempt++){
          await loadHandoff(true);if(!handoffInfo?.busy)break;
          await new Promise(resolve=>setTimeout(resolve,350));
        }
        return;
      }
      const result=await api('/api/action',{type:'handoff_start',agent_id:source,command_id:requestId(),
        version:handoffInfo.version,runtime:$('switch-runtime').value,context:$('switch-context').value});
      $('switch-status').textContent='Opening the new agent…';
      for(let attempt=0;attempt<8;attempt++){
        await poll();
        const next=state.snapshot.agents.find(agent=>agent.connection_id===result.launched?.connection_id&&agent.pane===result.launched?.pane);
        if(next){handoff.close();openAgent(next.id);toast(result.message);return;}
        await new Promise(resolve=>setTimeout(resolve,350));
      }
      handoff.close();toast('Agent started. Refresh the agent list to open it.');
    }catch(error){$('switch-status').textContent=error.message;}
    finally{handoffBusy=false;handoffControls();}
  };
  function renderAccount(){
    const value=accountInfo,login=value?.login,pending=login?.status==='pending';
    $('codex-account-label').textContent=value?.account?.label||(value?'Account unavailable':'Loading…');
    $('codex-account-plan').textContent=value?.account?.plan?.replaceAll('_',' ')||'';
    $('codex-signin').hidden=!pending;
    $('codex-account-start').hidden=pending;$('codex-account-cancel').hidden=!pending;
    $('codex-account-start').disabled=accountBusy||!value?.available;
    $('codex-account-cancel').disabled=accountBusy;$('codex-account-refresh').disabled=accountBusy;
    account.querySelector('.switch-close').disabled=accountBusy;
    if(pending){$('codex-login-code').textContent=login.code;$('codex-login-link').href=login.url;}
    else{$('codex-login-code').textContent='';$('codex-login-link').removeAttribute('href');}
    $('codex-account-status').textContent=value?.note||({pending:'Waiting for sign-in…',complete:'Account updated on this host.',failed:'Sign-in was not completed. You can try again.',expired:'The sign-in code expired. Start again.',cancelled:'Sign-in cancelled.'}[login?.status]||'');
  }
  async function loadAccount(){
    const epoch=accountEpoch;clearTimeout(timer);
    try{const result=await info(accountSource,'account');if(epoch!==accountEpoch||!account.open)return;
      accountInfo=result;renderAccount();if(result.login?.status==='pending')timer=setTimeout(loadAccount,2500);
    }catch(error){if(epoch===accountEpoch&&account.open){$('codex-account-status').textContent=error.message;timer=setTimeout(loadAccount,5000);}}
  }
  menuButton('codex-account-settings','Codex account','settings').onclick=()=>{
    if(!state.selected)return;accountSource=state.selected;accountInfo=null;accountEpoch++;
    document.querySelector('.workspace-menu').open=false;account.querySelector('.switch-target').textContent=hostTitle(accountSource);
    renderAccount();account.showModal();loadAccount();
  };
  account.addEventListener('close',()=>{accountEpoch++;clearTimeout(timer);});
  account.addEventListener('cancel',event=>{if(accountBusy)event.preventDefault();});
  $('codex-account-refresh').onclick=loadAccount;
  async function changeAccount(cancel=false){
    if(accountBusy||!accountInfo)return;
    const epoch=accountEpoch;accountBusy=true;clearTimeout(timer);renderAccount();
    $('codex-account-status').textContent=cancel?'Cancelling…':'Starting secure sign-in…';
    try{
      const result=await api('/api/action',{type:cancel?'account_cancel':'account_login',agent_id:accountSource,command_id:requestId(),
        ...(cancel?{login_id:accountInfo.login?.id}:{version:accountInfo.version})});
      if(epoch!==accountEpoch||!account.open)return;
      accountInfo=result.account;renderAccount();if(accountInfo.login?.status==='pending')timer=setTimeout(loadAccount,1500);
    }catch(error){if(epoch===accountEpoch)$('codex-account-status').textContent=error.message;}
    finally{accountBusy=false;account.querySelector('.switch-close').disabled=false;$('codex-account-start').disabled=!accountInfo?.available;$('codex-account-cancel').disabled=$('codex-account-refresh').disabled=false;}
  }
  $('codex-account-start').onclick=()=>changeAccount();$('codex-account-cancel').onclick=()=>changeAccount(true);
  $('codex-copy-code').onclick=async()=>{try{await navigator.clipboard.writeText($('codex-login-code').textContent);toast('Sign-in code copied');}catch{toast('Select the sign-in code to copy it.');}};
})();
