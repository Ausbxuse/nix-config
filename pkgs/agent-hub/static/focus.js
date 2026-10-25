/* One task at a time. Agent history stays authoritative; the supervisor is contextual. */
(() => {
  const shell = document.querySelector('.app-shell'), workspace = document.querySelector('.workspace');
  const drawer = $('agent-drawer');
  drawer.append($('agent-list-view'));
  const composer = document.createElement('div'); composer.id = 'focus-composers';
  composer.innerHTML = '<div class="composer-caption"><details id="recipient-menu"><summary id="composer-recipient" aria-label="Change message recipient"><span class="recipient-to">To</span><span id="recipient-name">ARIA</span><span id="recipient-host" hidden></span><span class="recipient-chevron" aria-hidden="true"></span></summary><div class="recipient-popup"><input id="recipient-search" type="search" placeholder="Find an agent…" aria-label="Find a recipient" autocomplete="off"><div id="recipient-options"></div><p class="recipient-help">Say <span>ARIA, …</span> from any conversation.</p></div></details><span id="composer-status" class="sr-only" role="status" aria-live="polite"></span></div>';
  composer.querySelector('.composer-caption').append($('handoff-context'));
  window.HubImages?.mount(composer);
  composer.append($('agent-message-form'), $('supervisor-form'));
  composer.prepend($('voice-call'));
  composer.insertAdjacentHTML('afterbegin', '<svg class="composer-surface" aria-hidden="true" focusable="false"><rect x="0.5" y="0.5"/></svg>');
  workspace.append(composer);
  const discussion = document.createElement('section'); discussion.id = 'task-supervisor'; discussion.setAttribute('aria-label', 'ARIA conversation for this task');
  discussion.append($('chat'), $('chat-new-output'), $('supervisor-activity'));
  $('agent-reader').append(discussion);
  document.querySelector('.supervisor-panel').hidden = true;
  const menu = $('workspace-menu-items');
  const taskTools = document.createElement('div'); taskTools.id='task-menu';
  taskTools.append($('focus-agent'), $('pause-agent'), $('copy-attach'));
  const rawToggle=document.createElement('button'); rawToggle.id='focus-raw';rawToggle.textContent='View terminal';rawToggle.onclick=()=>{setOutputMode(detail.mode==='raw'?'reader':'raw');document.querySelector('.workspace-menu').open=false;};taskTools.append(rawToggle);
  const separator=document.createElement('hr');taskTools.append(separator);menu.prepend(taskTools);
  menu.addEventListener('click',event=>{if(event.target.closest('button'))document.querySelector('.workspace-menu').open=false;});
  const ui = { initialized:false, awaitingFirst:true, active:null, changing:false, mode:'agent', seen:new Set(), scroll:new Map(), restoreScroll:false, voice:false, listening:false, speaking:false, voicePaused:false, lastReply:'', announced:new Set() };
  const attention=new HubAttention.Attention({send:async value=>{
    await api('/api/notifications',value);
    ui.seen.add(value.attention.id);
    while(ui.seen.size>500)ui.seen.delete(ui.seen.values().next().value);
    const item=queue().find(item=>item.id===value.attention.id && item.version===value.attention.version);
    if(item)item.seen=nowSeconds();
    renderAgents();decorate();
  },current:()=>{
    const item=current(),reader=$('agent-reader'),agent=agentById(state.selected);
    const lastReply=detail.messages?.findLast(message=>message.role==='assistant');
    const response=lastReply?[...$('agent-output').querySelectorAll('[data-message-id]')].find(node=>node.dataset.messageId==='assistant:'+lastReply.id):[...$('agent-output').querySelectorAll('.reader-assistant')].at(-1);
    const visible=Boolean(item && !document.hidden && !document.documentElement.hasAttribute('data-starting') && document.hasFocus() && state.view==='agent' && ui.mode==='agent' &&
      agent && !agent.activity_confirmed && HubAttention.sameTurn(item,agent) &&
      detail.signature && detail.mode!=='raw' && $('preview-error').hidden && HubAttention.visibleEnd(response,reader));
    return {item,visible};
  }});
  document.addEventListener('visibilitychange',()=>attention.sync());
  window.addEventListener('focus',()=>attention.sync());
  window.addEventListener('blur',()=>attention.sync());
  $('agent-reader').addEventListener('scroll',()=>attention.sync(),{passive:true});
  const handoffBrief=document.createElement('section');handoffBrief.id='handoff-brief';handoffBrief.className='handoff-brief';handoffBrief.hidden=true;handoffBrief.setAttribute('aria-label','Current handoff brief');
  $('agent-reader').append(handoffBrief);
  handoffBrief.addEventListener('click',event=>{
    const button=event.target.closest('[data-brief-source]');
    const field=button && handoffBrief._fields?.[Number(button.dataset.briefSource)];
    if(!field)return;
    if(field.source.message_id)AgentReader.reveal($('agent-output'),field.source.role+':'+field.source.message_id);
    const target=HubBrief.sourceNode(field,$('agent-output'),detail.messages || [],detail.text || '');
    if(!target){toast('This source changed. Refresh the conversation.');return;}
    jumpTo(target);
  });
  ui.revision=0;ui.requests=new Map();
  ui.passed=new Set();
  new ResizeObserver(()=>{if(state.snapshot)decorate();}).observe($('agent-reader'));
  const icon = name => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const hostTone = id => [...String(id || '')].reduce((sum, char) => sum + char.charCodeAt(0), 0) % 4;
  const menuIcons={'focus-settings':'settings','focus-history':'clock','focus-priority':'pin','focus-new':'plus','focus-agent':'pin','pause-agent':'pause','copy-attach':'copy','focus-raw':'agents','focus-continue':'arrow','focus-archive':'search'};
  const stored = (key, fallback=null) => { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } };
  const save = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch {} };
  let reading=stored('hub-reading',{}),readingTimer,restoreTimer;
  function saveReading(){
    clearTimeout(readingTimer);
    if(state.view!=='agent' || !state.selected || ui.restoreScroll || detail.text===null || detail.mode!=='reader')return;
    reading[state.selected]=HubReading.capture($('agent-reader'),detail.messages || [],reading[state.selected]);
    reading=Object.fromEntries(Object.entries(reading).filter(([,item])=>Date.now()-item.at<30*86400000).sort((a,b)=>b[1].at-a[1].at).slice(0,60));
    save('hub-reading',reading);
    window.HubContinuity?.changed();
  }
  function stopRestoring(){ui.restoreAnchor=null;ui.restoreSupervisor=null;clearTimeout(restoreTimer);}
  new ResizeObserver(()=>{if(ui.restoreAnchor && state.view==='agent')HubReading.restore($('agent-reader'),ui.restoreAnchor);}).observe($('agent-output'));
  window.addEventListener('pagehide',saveReading);
  document.addEventListener('visibilitychange',()=>{if(document.hidden)saveReading();});
  const agentDraftStore=new HubDrafts.Drafts(()=>localStorage,()=>sessionStorage,'hub-drafts');
  const supervisorDraftStore=new HubDrafts.Drafts(()=>localStorage,()=>sessionStorage,'hub-supervisor-drafts');
  for (const [id, text] of Object.entries(agentDraftStore.value)) detail.drafts.set(id, String(text).slice(0,12000));
  ui.context=null;ui.supervisorDrafts=supervisorDraftStore.value;
  function persistDrafts(){
    agentDraftStore.save(Object.fromEntries(detail.drafts));
    if(ui.context!==null)ui.supervisorDrafts[ui.context]=$('supervisor-input').value;
    supervisorDraftStore.save(ui.supervisorDrafts);
    window.HubContinuity?.changed();
  }
  function contextChanged(id){
    ui.voiceInterrupt=null;
    if(ui.streamingReply)stopPlayback(true);
    ui.voiceRouteVersion=(ui.voiceRouteVersion || 0)+1;
    if(ui.context!==null)ui.supervisorDrafts[ui.context]=$('supervisor-input').value;
    ui.context=id || '';
    $('supervisor-input').value=ui.supervisorDrafts[ui.context] || '';
    persistDrafts();
  }
  const queue = () => state.snapshot?.handoffs?.items || [];
  const current = () => queue().find(item => item.agent_id === state.selected);
  const ready = () => queue().filter(item => item.deferred_until <= (state.snapshot?.server_time || nowSeconds()));
  const replyReady=()=>Boolean(current() && ui.mode==='agent' && $('agent-message').value.trim() &&
    HubRouting.supervisorRequest($('agent-message').value)===null && !$('send-agent').disabled && !$('send-agent').hidden);
  function badge(agent) {
    const kind = !agent.connection_online || nowSeconds()-interaction.lastState>12 ? 'offline' : !agent.live ? 'exited' : agent.recovery && agent.recovery.status!=='paused' && !agent.activity_confirmed ? 'recovering' : agent.state;
    const label = agent.acknowledged ? `${labels[agent.state] || agent.state} · reviewed` : {offline:'Host offline',exited:'Agent exited',running:'Working',recovering:'Recovering from model capacity',waiting:'Needs input',blocked:'Blocked',done:'Ready to review',idle:'Available'}[kind] || 'Status unknown';
    const symbol = ['running','recovering'].includes(kind) ? '<span class="running-ring"></span>' : kind === 'done' ? icon('check') : ['blocked','offline','exited'].includes(kind) ? icon('attention') : kind === 'waiting' ? '<span class="question-symbol">?</span>' : '<span class="idle-ring"></span>';
    return `<span class="agent-state ${esc(kind)}" role="img" aria-label="${label}" title="${label}">${symbol}</span>`;
  }
  function setMode(mode, focus=false) {
    const nextMode=!state.selected ? 'supervisor' : mode;
    if(ui.mode!==nextMode && ui.streamingReply)stopPlayback(true);
    if(ui.mode!==nextMode)ui.voiceRouteVersion=(ui.voiceRouteVersion || 0)+1;
    ui.mode = nextMode;
    composer.dataset.mode = ui.mode;
    window.HubImages?.render();
    $('agent-message-form').hidden = ui.mode !== 'agent';
    $('supervisor-form').hidden = ui.mode !== 'supervisor';
    const agent = agentById(state.selected);
    const addressed=ui.mode==='agent' ? HubRouting.supervisorRequest($('agent-message').value) : null;
    const supervisor=ui.mode==='supervisor' || addressed!==null;
    const title=supervisor?'ARIA':agentTitle(agent);
    $('recipient-name').textContent=title;
    $('recipient-host').hidden=supervisor || !agent;
    $('recipient-host').textContent=supervisor?'':agent?.connection_label || '';
    $('recipient-host').dataset.hostTone=hostTone(agent?.connection_id);
    $('composer-recipient').dataset.supervisor=String(supervisor);
    const destination=`To ${title}${!supervisor && agent?' on '+agent.connection_label:''}`;
    $('composer-recipient').title=destination+' · Change recipient';
    $('composer-recipient').setAttribute('aria-label',destination+'. Change recipient');
    $('composer-status').textContent=destination;
    $('agent-message').placeholder='Message…';
    if(addressed!==null){
      $('queue-agent').hidden=true;$('stop-agent').hidden=true;$('send-agent').hidden=false;
      const busy=state.snapshot?.supervisor.busy || state.pendingJob || interaction.sending;
      $('send-agent').disabled=!addressed || !state.snapshot?.supervisor.available || Boolean(busy);
      $('send-agent').title='Send to ARIA';
      $('agent-message-hint').classList.remove('needs-help');
      $('agent-message-hint').textContent=state.snapshot?.supervisor.available?'':'ARIA unavailable. Your draft will stay here.';
      $('agent-message-hint').hidden=Boolean(state.snapshot?.supervisor.available);
    }
    const send=$(ui.mode==='supervisor'?'send-supervisor':!$('queue-agent').hidden?'queue-agent':!$('stop-agent').hidden?'stop-agent':'send-agent');
    if($('focus-voice').nextElementSibling!==send)send.before($('focus-voice'));
    if($('recipient-menu').open) renderRecipients();
    renderQuota(supervisor ? null : agent);
    if (focus) $(ui.mode === 'supervisor' ? 'supervisor-input' : 'agent-message').focus();
    sizeSupervisorInput();
  }
  function renderQuota(agent) {
    const snapshot=state.snapshot;
    if(!snapshot)return;
    const {connection,provider}=HubUsage.context(snapshot,agent);
    const now=snapshot.server_time+Math.max(0,nowSeconds()-interaction.lastState);
    const sources=HubUsage.headerSources(snapshot,agent,now),descriptions=[],menu=$('quota-menu');
    for(const item of sources){
      const source=item.connection && nowSeconds()-interaction.lastState>12 ? {...item.connection,status:'offline'} : item.connection;
      const report=HubUsage.weekly(source,now,item.provider),row=report.row;
      const gauge=menu.querySelector(`[data-quota-provider="${item.provider}"]`),name=item.provider==='claude'?'Claude':'Codex';
      gauge.dataset.level=report.level;
      gauge.querySelector('.quota-value').textContent=report.value;
      gauge.querySelector('.quota-fill').setAttribute('width',row && !row.expired ? String(14*row.remaining/100) : '0');
      const description=`${name}${item.connection?' on '+item.connection.label:''}: ${!row?'weekly usage unavailable':row.expired?'waiting for the weekly reset report':row.value+' this week'+(row.stale?' · last known usage':'')}`;
      gauge.title=description;descriptions.push(description);
    }
    $('quota-indicator').setAttribute('aria-label',descriptions.join('; ')+'. Show usage details');
    $('quota-indicator').title=descriptions.join('\n');
    // Keep the closed control cheap; reuse the same rows as Settings when opened.
    if(menu.open){
      const seen=new Set(),details=[...sources,...HubUsage.detailSources(snapshot,connection,provider,now)].filter(item=>{
        if(!item.connection)return false;const key=item.provider+':'+item.connection.id;if(seen.has(key))return false;seen.add(key);return true;
      });
      const markup=details.length?details.map(item=>{
        const current=nowSeconds()-interaction.lastState>12?{...item.connection,status:'offline'}:item.connection;
        return usageMarkup(current,item.provider,now,`${item.provider==='claude'?'Claude':'Codex'} · ${item.connection.label}`);
      }).join(''):'<p class="usage-note">No usage report for this host.</p>';
      replaceMarkup($('quota-details'),markup,'data-usage-host');
    }
  }
  function renderRecipients() {
    const query=$('recipient-search').value.trim().toLowerCase();
    const agents=(state.snapshot?.agents || []).filter(a=>`${agentTitle(a)} ${a.title} ${a.connection_label} ${a.cwd}`.toLowerCase().includes(query));
    const groups=new Map();
    for(const agent of agents){if(!groups.has(agent.connection_id))groups.set(agent.connection_id,[]);groups.get(agent.connection_id).push(agent);}
    let markup=`<button id="composer-supervisor" type="button" class="recipient-option" aria-current="${ui.mode==='supervisor'}">${icon('chat')}<span>ARIA</span>${ui.mode==='supervisor'?icon('check'):''}</button>`;
    for(const [host, entries] of groups){
      markup+=`<div class="recipient-group" data-host-tone="${hostTone(host)}">${icon('server')}${esc(entries[0].connection_label)}</div>`;
      markup+=entries.map(a=>`<button type="button" class="recipient-option" data-recipient-id="${esc(a.id)}" aria-current="${ui.mode==='agent' && state.selected===a.id}" title="${esc(a.title+' · '+a.connection_label)}">${badge(a)}<span class="recipient-task">${esc(agentTitle(a))}${agentVariant(a)?`<small>${esc(agentVariant(a))}</small>`:""}</span>${ui.mode==='agent' && state.selected===a.id?icon('check'):''}</button>`).join('');
    }
    if(!agents.length)markup+='<p class="recipient-empty">No matching agents</p>';
    replaceMarkup($('recipient-options'),markup,'data-recipient-id');
  }
  function supervisorStarted(entry) {
    ui.requests.set(entry.request_id,{id:entry.request_id,context:entry.context_agent_id,revision:ui.revision,returnMode:ui.clarification?.returnMode || ui.mode,voiceSession:ui.voice?ui.voiceSession:null});
    ui.clarification=null;
    while(ui.requests.size>16)ui.requests.delete(ui.requests.keys().next().value);
    if(ui.voice)refreshVoiceState();
  }
  function supervisorFinished(job) {
    if(!job || job.status==='running' || !ui.requests.has(job.request_id))return;
    // State snapshots omit the complete reply. Keep the request until its
    // detailed job arrives, including when the last state event wins the race.
    if(typeof job.reply!=='string' && typeof job.error!=='string')return;
    const request=ui.requests.get(job.request_id);ui.requests.delete(job.request_id);
    if(request.voiceSession && request.voiceSession!==ui.voiceSession)ui.announced.add('request:'+job.request_id);
    if(request.liveSpeech){
      ui.announced.add('request:'+job.request_id);
      if(job.reply && request.voiceSession===ui.voiceSession && request.context===(state.context || '')){ui.lastReply=job.reply;$('voice-repeat').disabled=false;}
      if(request.speechMuted || !ui.voice || ui.voicePaused || request.voiceSession!==ui.voiceSession)return;
      const live=request.liveSpeech;
      if(job.status==='complete' && job.reply.startsWith(live.text)){
        live.playback.append(job.reply.slice(live.text.length));live.playback.finish();
        voiceCaption('assistant',job.reply,live.key);
        return;
      }
      stopPlayback(true);
    }
    const announce=text=>{
      if(!ui.voice || ui.voicePaused || request.voiceSession!==ui.voiceSession || !text)return;
      ui.announced.add('request:'+job.request_id);
      if(ui.speaking)ui.deferredSpeech={text,session:ui.voiceSession};else say(text);
    };
    const clarification=HubRouting.clarificationFor(job,request,state.context,ui.revision,state.snapshot.agents);
    if(clarification){
      ui.clarification={...clarification,returnMode:request.returnMode};
      setMode('supervisor');chatChanged();
      announce(job.reply);
      return;
    }
    const target=HubRouting.selectionTarget(job,request,state.context,ui.revision,state.snapshot.agents);
    if(!target){
      if(request.context===(state.context || ''))announce(job.reply || job.error);
      return;
    }
    openAgent(target.id);
    const text=`You're now talking to ${agentTitle(target)} on ${target.connection_label}.`;
    $('composer-status').textContent=text;
    announce(text);
  }
  function supervisorProgress(job){
    const request=job && ui.requests.get(job.request_id);
    if(!request || job.status!=='running' || !job.voice || !job.speech || request.speechMuted)return;
    if(!ui.voice || ui.voicePaused || request.voiceSession!==ui.voiceSession || request.context!==(state.context || '') || request.revision!==ui.revision)return;
    if(recognition?.recording || recognition?.processing || !playback || !state.snapshot?.voice?.playback?.available)return;
    let live=request.liveSpeech;
    if(!live){
      stopPlayback();
      const generation=ui.speechGeneration,key='reply-'+generation;
      live=request.liveSpeech={text:'',request,key,playback:playback.begin()};ui.streamingReply=live;
      ui.replyKey=key;ui.speaking=true;recognition.setPlayback(true);refreshVoiceState();
      ui.announced.add('request:'+job.request_id);
      live.playback.done.then(finished=>{
        if(generation!==ui.speechGeneration)return;
        ui.streamingReply=null;ui.speaking=ui.voicePlaying=false;
        syncVoiceCapture();recognition.setPlayback(false);voiceLevel(0);refreshVoiceState();
      }).catch(error=>{
        if(generation!==ui.speechGeneration)return;
        stopPlayback(true);voiceHelp(error.message || 'Audio failed. Read the reply in the chat.',true);refreshVoiceState();
      });
    }
    if(!job.speech.startsWith(live.text)){stopPlayback(true);return;}
    if(job.speech===live.text)return;
    live.playback.append(job.speech.slice(live.text.length));live.text=job.speech;
    ui.lastReply=live.text;$('voice-repeat').disabled=false;
    voiceCaption('assistant',live.text,live.key);
  }
  function clarificationId() {
    const pending=ui.clarification;
    if(pending && (pending.context!==(state.context || '') || pending.revision!==ui.revision || pending.expires<=nowSeconds()))ui.clarification=null;
    return ui.clarification?.id || '';
  }
  function renderClarification() {
    const pending=clarificationId() && ui.clarification;
    const message=pending && [...$('chat').children].find(node=>node.dataset.messageKey==='assistant:'+pending.requestId);
    for(const node of $('chat').querySelectorAll('.supervisor-choices'))if(node.parentElement!==message)node.remove();
    if(!message)return;
    let list=message.querySelector('.supervisor-choices');
    if(!list){list=document.createElement('div');list.className='supervisor-choices';list.setAttribute('role','group');list.setAttribute('aria-label','Choose an agent');message.append(list);}
    const markup=pending.choices.map((choice,index)=>{
      const agent=agentById(choice.agent_id),title=agent?.title || choice.title,host=agent?.connection_label || choice.host;
      const duplicate=pending.choices.filter(c=>c.title===choice.title && c.host===choice.host).length>1;
      const location=host+(duplicate && choice.pane?' · '+choice.pane:'');
      const unavailable=!agent || !agent.live?'Unavailable':!agent.connection_online?'Offline':'';
      return `<button type="button" class="supervisor-choice" data-choice-agent="${esc(choice.agent_id)}" ${unavailable?'disabled':''}><span class="choice-number" aria-hidden="true">${index+1}</span><span><span class="choice-task">${esc(choice.reason || title)}</span><small>${esc(title)} · ${esc(location)}${unavailable?' · '+unavailable:''}</small></span>${icon('arrow')}</button>`;
    }).join('')+'<button type="button" class="text-button choice-cancel" data-choice-cancel>Cancel</button>';
    if(list._markup!==markup){list.innerHTML=markup;list._markup=markup;}
  }
  function cancelClarification() {
    const mode=ui.clarification?.returnMode || ui.mode;
    ui.clarification=null;ui.revision++;setMode(mode,true);decorate();
  }
  function navigate(name) {
    if(name!=='agent')saveReading();
    if (name === 'home') {home();return true;}
    if (name === 'agents') { window.HubArchive?.agents(); $('agent-list-view').hidden=false; if(!drawer.open)drawer.showModal(); renderAgents(); decorate(); return true; }
    return false;
  }
  function decorate() {
    for(const [id,name] of Object.entries(menuIcons)){
      const button=$(id);if(button && !button.querySelector('svg'))button.insertAdjacentHTML('afterbegin',icon(name));
    }
    const agent = agentById(state.selected);
    const task = state.view === 'agent' && agent;
    taskTools.hidden=!task;
    const secondary=!['agent','home'].includes(state.view);
    if($('workspace-drawer').dataset.secondary!==String(secondary)){
      $('workspace-drawer').innerHTML=icon(secondary?'back':'menu')+'<span id="queue-dot" hidden></span>';
      $('workspace-drawer').dataset.secondary=String(secondary);
    }
    $('workspace-drawer').setAttribute('aria-label',state.view==='archive'?window.HubArchive?.backLabel() || 'Back to history search':secondary?'Back to conversation':'Browse agents and handoffs');
    composer.hidden = !['agent','home'].includes(state.view) && $('voice-call').hidden;
    $('focus-empty').hidden = state.view !== 'home';
    const archive=state.view==='archive' && window.HubArchive;
    $('focus-title').textContent = archive?archive.title():task ? agentTitle(agent) : ({home:'ARIA',research:'Research',status:'Settings',notifications:'Update history',launch:'New agent',connection:'Add host',activity:'Activity'}[state.view] || 'Agent Hub');
    $('focus-title').title=task?agent.title:'';
    $('rename-agent').disabled=!task;
    const renameLabel=task ? `Name this task: ${agentTitle(agent)} on ${agent.connection_label}` : $('focus-title').textContent;
    $('rename-agent').setAttribute('aria-label',renameLabel);
    $('rename-agent').title=task ? agent.title+' · Edit names' : '';
    const subtitle=archive?`<span class="archive-host" data-host-tone="${hostTone(archive.host())}">${esc(archive.subtitle())}</span>`:task ? `<button id="focus-host" data-host-tone="${hostTone(agent.connection_id)}" title="Browse ${esc(agent.connection_label)}">${icon('server')}<span>${esc(agent.connection_label)}</span></button><span class="task-runtime">${esc(agent.runtime)}</span>${badge(agent)}` : '<span>Your workspace</span>';
    replaceMarkup($('focus-subtitle'),subtitle,'id');
    document.querySelectorAll('[data-host-group]').forEach(group=>{group.dataset.hostTone=hostTone(group.dataset.hostGroup);});
    const item = current(), candidate=HubRouting.nextHandoff(ready(),state.snapshot?.agents || [],state.selected,ui.passed);
    const delivery=interaction.receipts.get(state.selected),newInstruction=delivery && ['sending','sent','uncertain'].includes(delivery.status) && !delivery.responseSeen && delivery.created>(item?.created || 0);
    const reader=$('agent-reader'),scroll=reader.scrollTop,follow=reader.scrollHeight-reader.scrollTop-reader.clientHeight<80 && !isReadingOutput();
    HubBrief.render(handoffBrief,{facts:detail.facts,messages:detail.messages,text:detail.text || '',version:detail.signature,handoff:item,agentId:state.selected,
      viewport:reader.clientWidth+':'+reader.clientHeight,
      visible:field=>HubBrief.sourceVisible(field,$('agent-output'),detail.messages || [],detail.text || '',reader),
      enabled:Boolean(task && ui.mode==='agent' && !newInstruction && !agent.activity_confirmed && !['running','recovering'].includes(agent.state))},()=>{
      if(follow && reader.scrollTop===scroll)reader.scrollTop=reader.scrollHeight;
    });
    const upcoming=candidate?.agent_id!==state.selected?candidate:null;
    $('handoff-context').hidden = !task || (!item && !upcoming);
    $('defer-menu').hidden=!item;
    const nextLabel=upcoming?`Next: ${upcoming.title} · ${upcoming.host}`:'Next handoff';
    const reply=replyReady();
    $('focus-reviewed').title=reply?'Reply and next (Ctrl Enter)':item?'Reviewed':nextLabel;
    $('focus-reviewed').setAttribute('aria-label',reply?'Send reply, mark this handoff reviewed and continue':item?'Mark reviewed and continue':nextLabel);
    $('focus-reviewed').disabled = ui.changing;
    $('focus-reviewed').classList.toggle('reply-next',reply);
    replaceMarkup($('focus-reviewed'),reply?'<span>Reply &amp; next</span>'+icon('arrow'):icon(item?'check':'arrow'),'id');
    if(task && !item && upcoming){$('handoff-summary').textContent=`Next: ${upcoming.title} · ${upcoming.host}`;$('handoff-summary').title=upcoming.reason;}
    if (task && item) {
      const user = (detail.messages || []).findLast(m => m.role === 'user' && !/^(# AGENTS|<)/.test(m.text));
      const request = user?.text.replace(/\s+/g,' ').trim();
      $('handoff-summary').textContent = request ? `You asked: ${request.length > 150 ? request.slice(0,147)+'…' : request}` : item.reason;
      $('handoff-summary').title = request || item.reason;
      $('defer-menu').setAttribute('aria-busy', String(ui.changing));
    }
    const future = queue().filter(i=>i.deferred_until > (state.snapshot?.server_time || nowSeconds()));
    $('focus-queue-count').textContent = ready().length || '';
    $('queue-dot').hidden = !ready().some(i=>i.agent_id !== state.selected);
    const running = state.snapshot?.agents.filter(a=>a.connection_online && a.state === 'running').length || 0;
    const offline=state.snapshot?.connections.filter(c=>c.status==='offline').length || 0;
    $('focus-empty').querySelector('h1').textContent=offline?'Host unavailable':'All clear';
    $('focus-empty-description').textContent = `${offline ? offline+' host'+(offline===1?' is':'s are')+' offline. ' : ''}${running ? running+' agent'+(running===1?'':'s')+' working.' : 'No handoffs waiting.'}${future.length ? ' '+future.length+' set aside for later.' : ''}`;
    const priority = state.snapshot?.handoffs?.priority?.text;
    $('priority-note').hidden = !priority;
    $('priority-note').textContent = priority ? `Priority · ${priority}` : '';
    const deferredMarkup = future.length ? '<div class="drawer-label">Later</div>'+future.map(i=>`<button class="deferred-item" data-restore-handoff="${i.id}"><span>${esc(i.title)}</span><small>${new Date(i.deferred_until*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',hourCycle:'h23'})} · ${esc(i.host)}</small></button>`).join('') : '';
    replaceMarkup($('deferred-list'),deferredMarkup,'data-restore-handoff');
    const jobHere=interaction.sending?.context_agent_id === (state.context || '') || (interaction.job && (interaction.job.context_agent_id || '') === (state.context || '') && state.pendingJob);
    discussion.hidden = !['agent','home'].includes(state.view) || (!discussion.querySelector('.chat-message') && !jobHere);
    $('supervisor-activity').hidden ||= !jobHere;
    renderClarification();setMode(ui.mode);
    attention.sync();
    window.HubContinuity?.changed();
    if(ui.voice)refreshVoiceState();
  }
  function home() {
    if(state.selected)state.previous={agent:state.selected};
    saveReading();stopRestoring();
    ui.revision++;
    contextChanged('');
    if (state.selected) detail.drafts.set(state.selected, $('agent-message').value);
    state.selected=state.context=null; state.view='home'; ui.active=null;
    Object.values(viewIds).forEach(id=>$(id).hidden=true);
    shell.dataset.page='home'; shell.dataset.mobileView='workspace';
    mobilePane('workspace');
    workspace.insertBefore(discussion,composer);
    save('hub-current-agent', null); renderChat(); decorate();
  }
  function next() {
    drawer.close();
    const items=ready(),currentItem=current();
    ui.passed=new Set([...ui.passed].filter(id=>items.some(item=>item.id===id)));
    if(currentItem)ui.passed.add(currentItem.id);
    if(items.every(item=>ui.passed.has(item.id)))ui.passed.clear();
    const item=HubRouting.nextHandoff(items,state.snapshot?.agents || [],state.selected,ui.passed);
    if (item) { openAgent(item.agent_id); return item; }
    home(); return null;
  }
  function opened(id) {
    ui.revision++;
    $('recipient-menu').open=false;
    contextChanged(id);
    ui.awaitingFirst=false;
    drawer.close();
    if (discussion.parentElement !== $('agent-reader')) $('agent-reader').append(discussion);
    ui.active=current()?.id || null; ui.restoreScroll=true;stopRestoring();ui.readBaseline=reading[id];
    ui.openPosition={bottom:true};
    save('hub-current-agent',id);
    $('agent-message').value=detail.drafts.get(id) || ''; sizeAgentInput();
    setMode('agent'); decorate();
  }
  function preview() {
    reconcileChat();
    decorate();
    HubReading.mark($('agent-output'),detail.messages || [],ui.readBaseline);
    if (ui.restoreScroll) {
      const position=ui.openPosition || {bottom:true};
      if(!position.bottom && position.anchor)AgentReader.reveal($('agent-output'),position.anchor.id);
      HubReading.restore($('agent-reader'),position);ui.restoreAnchor=position;restoreTimer=setTimeout(stopRestoring,2000);
      ui.openPosition=null;
      ui.restoreScroll=false;
    }
    restoreSupervisorReading();
    clearTimeout(readingTimer);readingTimer=setTimeout(saveReading,350);
  }
  function render() {
    if (!state.snapshot) return;
    // An older in-flight snapshot must not restore a marker just cleared here.
    let readChanged=false;
    for(const item of queue())if(ui.seen.has(item.id) && !item.seen){item.seen=nowSeconds();readChanged=true;}
    if(readChanged)renderAgents();
    if (!ui.initialized) {
      ui.initialized=true;
      if(new URL(location.href).searchParams.has('archive') || HubPush.route(location.href)){ui.awaitingFirst=false;home();return;}
      const id=stored('hub-current-agent');
      const deferred=queue().some(i=>i.agent_id===id && i.deferred_until > state.snapshot.server_time);
      if (id && agentById(id) && !deferred) openAgent(id); else next();
    }
    if(ui.awaitingFirst && !drawer.open && state.view==='home' && ready().length && !$('supervisor-input').value && !discussion.querySelector('.chat-message')) next();
    // New work never steals the active task, reading position, or draft.
    decorate();
    window.HubStartup?.check();
    supervisorProgress(state.snapshot.supervisor.job);
    supervisorFinished(state.snapshot.supervisor.job);
    const liveItem=current();
    if (liveItem) ui.active=liveItem.id;
    if(ui.voice){speakDeferred();refreshVoiceState();}
    if (ui.voice && !ui.voicePaused && !ui.speaking && !voiceWaiting() && !recognition?.recording && !recognition?.processing) {
      const reply=state.snapshot.messages.filter(m=>['assistant','error'].includes(m.role) && (m.context_agent_id || '') === (state.context || '')).at(-1);
      if (reply && !ui.announced.has(reply.id) && !ui.announced.has('request:'+reply.request_id) && reply.created >= ui.voiceSince) {ui.announced.add(reply.id);if(reply.request_id)ui.announced.add('request:'+reply.request_id);say(reply.text);}
      else if(!ui.speaking && !ui.changing){
        const arrival=ready().find(i=>i.interrupt!==false && !ui.voiceHandoffs?.has(i.id));
        if(arrival){ui.voiceHandoffs.add(arrival.id);say(spokenHandoff(arrival),ui.voiceSession,arrival);}
      }
    }
  }
  function spokenHandoff(item) {
    if(!item)return "There's nothing waiting for your review.";
    if(item.brief)return `${item.title} on ${item.host}. ${item.brief}`;
    const update={waiting:'is waiting for your answer',blocked:'needs your help to continue',done:'has a reply for you',exited:'has stopped'}[item.kind] || 'has an update for you';
    return `${item.title} on ${item.host} ${update}.`;
  }
  async function complete(action, until) {
    if (ui.changing) return;
    const item=current();
    if (!item) { if(ui.voice)say("There's nothing to review in this conversation."); return; }
    ui.awaitingFirst=false;ui.changing=true; decorate();
    try {
      const result=await api('/api/handoffs', {action,id:item.id,...(until?{until}:{})});
      ui.scroll.set(state.selected, $('agent-reader').scrollTop);
      await poll();
      $('defer-menu').open=false;
      const upcoming=next();
      const receipt=action==='later'?'Set aside.':'Marked reviewed.';
      if(ui.voice) say((action==='later'?"I've saved that for later. ":"I've marked that as reviewed. ")+spokenHandoff(upcoming),ui.voiceSession,upcoming);
      toast(receipt,false,result.undo?{label:'Undo',run:async()=>{const restored=await api('/api/handoffs',{action:'undo',token:result.undo});await poll();if(agentById(restored.agent_id))openAgent(restored.agent_id);toast('Restored.');}}:null);
    } catch(error) {toast(error.message,true); if(ui.voice)say(error.message);}
    finally {ui.changing=false;decorate();}
  }
  async function replyAndNext(){
    if(ui.changing || !replyReady())return false;
    const item=current(),revision=ui.revision,agentId=state.selected,message=$('agent-message').value;
    ui.changing=true;decorate();
    try{
      const delivered=await sendAgentMessage(message,$('agent-message'),{handoff:item});
      if(!delivered)return false;
      // A manual selection or newly typed draft takes precedence over this
      // earlier request to move on. A reload never advances automatically.
      if(ui.revision===revision && state.selected===agentId && ui.mode==='agent' && !$('agent-message').value.trim())next();
      return true;
    }finally{ui.changing=false;decorate();}
  }
  function tomorrow() {const d=new Date(); d.setDate(d.getDate()+1); d.setHours(9,0,0,0);return d.getTime()/1000;}
  $('workspace-drawer').onclick=()=>{if(state.view==='archive'&&window.HubArchive)HubArchive.back();else if(['agent','home'].includes(state.view))navigate('agents');else closeBuffer();};
  $('focus-browse').onclick=()=>navigate('agents');
  $('drawer-close').onclick=()=>drawer.close();
  $('focus-next').onclick=next;
  $('focus-reviewed').onclick=()=>replyReady()?replyAndNext():current()?complete('handled'):next();
  $('focus-settings').onclick=()=>{document.querySelector('.workspace-menu').open=false;openBuffer('status');decorate();};
  const quotaMenu=$('quota-menu');
  const closeQuota=(focus=false)=>{quotaMenu.open=false;if(focus)$('quota-indicator').focus({preventScroll:true});};
  $('quota-indicator').onclick=event=>{event.preventDefault();quotaMenu.open=!quotaMenu.open;if(quotaMenu.open)setMode(ui.mode);};
  quotaMenu.addEventListener('toggle',()=>{if(quotaMenu.open){document.querySelector('.workspace-menu').open=false;setMode(ui.mode);}});
  quotaMenu.addEventListener('keydown',event=>{if(event.key==='Escape' && quotaMenu.open){event.preventDefault();event.stopPropagation();closeQuota(true);}});
  document.addEventListener('pointerdown',event=>{if(!quotaMenu.contains(event.target))closeQuota();});
  document.addEventListener('focusin',event=>{if(!quotaMenu.contains(event.target))closeQuota();});
  $('quota-close').onclick=()=>closeQuota(true);
  $('quota-all').onclick=()=>{closeQuota();$('focus-settings').click();document.querySelector('.weekly-usage').scrollIntoView({block:'start'});};
  $('focus-history').onclick=()=>{document.querySelector('.workspace-menu').open=false;openBuffer('notifications');decorate();};
  $('focus-new').onclick=()=>{document.querySelector('.workspace-menu').open=false;$('launch-button').click();decorate();};
  $('focus-priority').onclick=()=>{document.querySelector('.workspace-menu').open=false;$('priority-text').value=state.snapshot?.handoffs?.priority?.text || '';$('priority-dialog').showModal();};
  $('priority-close').onclick=()=>$('priority-dialog').close();
  async function priority(text) {await api('/api/handoffs',{action:'priority',text});await poll();}
  $('priority-form').onsubmit=async e=>{e.preventDefault();try{await priority($('priority-text').value);$('priority-dialog').close();}catch(err){toast(err.message,true);}};
  $('priority-clear').onclick=async()=>{try{await priority('');$('priority-dialog').close();}catch(err){toast(err.message,true);}};
  $('composer-recipient').addEventListener('click',()=>{if(!$('recipient-menu').open){$('recipient-search').value='';renderRecipients();}});
  $('recipient-menu').addEventListener('toggle',()=>{if($('recipient-menu').open){renderRecipients();if(!phoneLayout.matches)$('recipient-search').focus();$('defer-menu').open=false;}});
  $('recipient-search').oninput=renderRecipients;
  $('recipient-menu').addEventListener('keydown',event=>{
    if(event.key==='Escape') {event.preventDefault();event.stopPropagation();$('recipient-menu').open=false;$('composer-recipient').focus({preventScroll:true});}
    if(['ArrowDown','ArrowUp'].includes(event.key)){
      event.preventDefault();const choices=[...$('recipient-options').querySelectorAll('button')],index=choices.indexOf(document.activeElement);
      choices[(index+(event.key==='ArrowDown'?1:-1)+choices.length)%choices.length]?.focus();
    }
    if(event.key==='Enter' && event.target===$('recipient-search')) {event.preventDefault();$('recipient-options').querySelector('[data-recipient-id]')?.click();}
  });
  $('agent-to-supervisor').onclick=()=>{ui.revision++;setMode('supervisor',true);};
  $('agent-reader').addEventListener('scroll',()=>{if(state.selected && !ui.restoreScroll){ui.scroll.set(state.selected,$('agent-reader').scrollTop);clearTimeout(readingTimer);readingTimer=setTimeout(saveReading,350);}});
  $('agent-message').addEventListener('input',persistDrafts);
  $('supervisor-input').addEventListener('input',persistDrafts);
  $('defer-menu').addEventListener('keydown',event=>{
    if(event.key==='Escape' && $('defer-menu').open){
      event.preventDefault();event.stopPropagation();$('defer-menu').open=false;
      $('defer-menu').querySelector('summary').focus({preventScroll:true});
    }
  });
  document.addEventListener('click',async e=>{
    if(!e.target.closest('#recipient-menu'))$('recipient-menu').open=false;
    const recipient=e.target.closest('[data-recipient-id]');
    if(recipient){openAgent(recipient.dataset.recipientId);$('agent-message').focus();}
    const choice=e.target.closest('[data-choice-agent]');
    if(choice && clarificationId() && ui.clarification.choices.some(c=>c.agent_id===choice.dataset.choiceAgent)){
      const target=agentById(choice.dataset.choiceAgent);
      if(target?.connection_online && target.live){
        if(ui.clarification.purpose==='coordinate'){
          const index=ui.clarification.choices.findIndex(c=>c.agent_id===target.id);
          choice.disabled=true;
          try{await submitSupervisor(`The ${['first','second','third','fourth','fifth','sixth'][index]} one.`,null,Boolean(ui.voice));}
          finally{if(choice.isConnected)choice.disabled=false;}
        }else{openAgent(target.id);$('agent-message').focus();if(ui.voice)say(`You're now talking to ${agentTitle(target)} on ${target.connection_label}.`);}
      }
    }
    if(e.target.closest('[data-choice-cancel]'))cancelClarification();
    if(e.target.closest('#composer-supervisor')){
      $('recipient-menu').open=false;ui.awaitingFirst=false;
      home();renderChat(true);$('supervisor-input').focus();
    }
    if(!e.target.closest('#defer-menu'))$('defer-menu').open=false;
    if(e.target.closest('#focus-host')) revealHost(agentById(state.selected)?.connection_id);
    const defer=e.target.closest('[data-defer]');
    if(defer) complete('later',defer.dataset.defer==='tomorrow'?tomorrow():nowSeconds()+Number(defer.dataset.defer));
    const restore=e.target.closest('[data-restore-handoff]');
    if(restore) {const item=queue().find(i=>i.id===restore.dataset.restoreHandoff);try{await api('/api/handoffs',{action:'restore',id:item.id});await poll();openAgent(item.agent_id);}catch(err){toast(err.message,true);}}
  });
  function showSupervisor() {ui.revision++;setMode('supervisor',true);$('agent-reader').scrollTop=$('agent-reader').scrollHeight;}
  let chatSignature='';
  function chatChanged(force=false) {
    const scroller=state.view==='home'?discussion:$('agent-reader');
    const atBottom=scroller.scrollHeight-scroller.scrollTop-scroller.clientHeight<100;
    const signature=[...$('chat').children].map(n=>n._signature||'').join('')+clarificationId();
    if(signature!==chatSignature || force){chatSignature=signature;decorate();if(force || atBottom)requestAnimationFrame(()=>{scroller.scrollTop=scroller.scrollHeight;});}
  }

  // Follow the latest message through keyboard and composer resizing. Touching
  // the history releases it; another keystroke brings the conversation back.
  let composerFollow=null;
  function releaseComposerScroll() {
    composerFollow=null;
  }
  function nudgeComposerScroll() {
    const bounds=composer.getBoundingClientRect();document.documentElement.style.setProperty('--toast-bottom',(bounds.height?Math.max(12,innerHeight-bounds.top+8):12)+'px');
    const follow=composerFollow;
    if(!follow)return;
    if((window.visualViewport && Math.abs(window.visualViewport.scale-1)>.01) ||
      document.activeElement!==follow.input || state.selected!==follow.agent || state.view!==follow.view ||
      !follow.input.getClientRects().length || !follow.scroller.getClientRects().length){releaseComposerScroll();return;}
    // ResizeObserver runs before paint. Deferring this to another animation
    // frame lets the latest line disappear under the growing composer first.
    follow.scroller.scrollTop=follow.scroller.scrollHeight;
  }
  function followComposer(event) {
    stopRestoring();
    if(!event.target.matches('#agent-message,#supervisor-input'))return;
    const scroller=state.view==='home'?discussion:state.view==='agent'?$(detail.mode==='raw'?'terminal':'agent-reader'):null;
    if(!scroller)return;
    // Preserve desktop reading; wide touch screens still need keyboard nudges.
    // An already-following conversation stays at the bottom as the draft grows.
    if(!phoneLayout.matches && !touchInput.matches && !composerFollow && scroller.scrollHeight-scroller.scrollTop-scroller.clientHeight>80)return;
    if(event.target.id==='agent-message')AgentReader.latest($('agent-output'));
    composerFollow={input:event.target,scroller,agent:state.selected,view:state.view};
    nudgeComposerScroll();
  }
  const composerResize=new ResizeObserver(nudgeComposerScroll);
  for(const content of [composer,$('chat'),$('agent-output')])composerResize.observe(content,{box:'border-box'});
  for(const scroller of [$('agent-reader'),$('terminal'),discussion]) {
    composerResize.observe(scroller);
    for(const event of ['pointerdown','touchstart','wheel','keydown'])scroller.addEventListener(event,releaseComposerScroll,{passive:true});
    for(const event of ['pointerdown','touchstart','wheel','keydown'])scroller.addEventListener(event,stopRestoring,{passive:true});
  }
  composer.addEventListener('focusin',followComposer);
  composer.addEventListener('input',followComposer);
  composer.addEventListener('focusout',releaseComposerScroll);
  function jumpTo(node){
    stopRestoring();ui.restoreScroll=false;
    node.scrollIntoView({block:'start',behavior:'instant'});
    node.classList.add('brief-source-target');setTimeout(()=>node.classList.remove('brief-source-target'),1800);
  }
  function continuitySnapshot(){
    if(!ui.initialized || !['agent','home'].includes(state.view) || ui.restoreScroll && state.view==='agent')return null;
    const mode=state.selected?ui.mode:'supervisor';
    const reader=$('agent-reader'),top=reader.getBoundingClientRect().top;
    const first=state.selected?[...reader.querySelectorAll('.reader-entry[data-message-id],.chat-message[data-message-key]')].find(node=>node.getClientRects().length&&node.getBoundingClientRect().bottom>top):null;
    const scope=!state.selected||first?.classList.contains('chat-message')?'supervisor':first?'agent':mode;
    const bookmark=scope==='agent'?(detail.mode==='reader'&&detail.text!==null?HubReading.capture(reader,detail.messages || [],reading[state.selected]):reading[state.selected]):
      HubReading.position(state.selected?$('agent-reader'):discussion,[...$('chat').querySelectorAll('[data-message-key]')],node=>node.dataset.messageKey);
    return {agent_id:state.selected || '',mode,draft:$(mode==='agent'?'agent-message':'supervisor-input').value,bookmark:bookmark?{...bookmark,scope}:null};
  }
  function continuityDraft(value){
    if(value.mode==='agent')return value.agent_id===state.selected?$('agent-message').value:detail.drafts.get(value.agent_id) || '';
    return value.agent_id===(ui.context || '')?$('supervisor-input').value:ui.supervisorDrafts[value.agent_id] || '';
  }
  function restoreSupervisorReading(){
    const saved=ui.restoreSupervisor;
    if(!saved || saved.context!==(state.context || '') || ui.mode!==saved.mode)return;
    HubReading.restore(state.selected?$('agent-reader'):discussion,saved.bookmark,[...$('chat').querySelectorAll('[data-message-key]')],node=>node.dataset.messageKey);
    if(!state.selected||detail.text!==null){clearTimeout(restoreTimer);restoreTimer=setTimeout(stopRestoring,2000);}
  }
  function continueHere(value,draft){
    if(value.agent_id && !agentById(value.agent_id))throw Error('This agent is no longer available. The saved draft is still here.');
    if(ui.voice || ui.voiceOpening)endVoice();
    saveReading();persistDrafts();releaseComposerScroll();stopRestoring();
    ui.awaitingFirst=false;
    if(value.agent_id)openAgent(value.agent_id);else home();
    setMode(value.mode);
    if(value.mode==='agent'){
      detail.drafts.set(value.agent_id,draft);$('agent-message').value=draft;sizeAgentInput();
      setOutputMode('reader');updateDetailMeta();
    }else{
      ui.restoreScroll=false;
      $('supervisor-input').value=draft;sizeSupervisorInput();renderChat();
    }
    if(value.bookmark){
      if((value.bookmark.scope || value.mode)==='supervisor'){
        ui.restoreScroll=false;ui.restoreSupervisor={context:value.agent_id,mode:value.mode,bookmark:value.bookmark};
        const revision=ui.revision;requestAnimationFrame(()=>{if(ui.revision===revision)restoreSupervisorReading();});
      }else{reading[value.agent_id]={...value.bookmark,at:Date.now()};save('hub-reading',reading);ui.readBaseline=reading[value.agent_id];ui.openPosition=value.bookmark;ui.restoreScroll=true;}
    }
    persistDrafts();decorate();
  }
  function leaveConversation(){saveReading();persistDrafts();stopRestoring();releaseComposerScroll();ui.revision++;ui.awaitingFirst=false;if(ui.voice||ui.voiceOpening)endVoice();}
  function scopeMessages(messages) {
    const context=state.context || '',handoff=queue().find(item=>item.agent_id===context);
    return messages.filter(message=>{
      if(message.role!=='update')return (message.context_agent_id || '')===context;
      if(!message.assistant_summary)return false;
      if(!context)return true;
      // An old handoff does not describe the turn now shown in this agent view.
      if(message.agent_id!==context || !handoff || message.handoff_id!==handoff.id)return false;
      // The reader has the original. Add only a substantially shorter brief of
      // that exact current reply; the inbox retains the full update history.
      return HubBrief.usefulUpdate(message,detail.messages || []);
    });
  }
  window.HubFocus={hostTone,voiceActive:()=>ui.voice,saveReading,render,navigate,opened,preview,decorate,badge,showSupervisor,home,chatChanged,persistDrafts,replyAndNext,supervisorStarted,supervisorProgress,supervisorFinished,clarificationId,endCall,jumpTo,stopRestoring,continuitySnapshot,continuityDraft,continueHere,leaveConversation,scopeMessages};

  // One spoken instruction per turn. During playback, sustained speech may
  // interrupt the audio; it never creates an automatic follow-up queue.
  let recognition=null,playback=null;
  let voiceTimer,speechTimer,microphoneTimer;
  function voiceWaiting(){
    if(ui.voiceCommand || state.pendingJob || interaction.sending || state.snapshot?.supervisor.busy)return true;
    if(ui.mode!=='agent' || !state.selected)return false;
    const agent=agentById(state.selected),receipt=interaction.receipts.get(state.selected);
    return agent?.state==='running' || detail.sending.has(state.selected) || Boolean(receipt?.status==='sent' && !receipt.responseSeen && !receipt.wasRunning && nowSeconds()-receipt.created<30);
  }
  function syncVoiceCapture(){
    if(!recognition)return;
    // A user can interrupt preparation and gaps between clips, too.
    recognition.setInterruptible(ui.voice && !ui.voicePaused && ui.speaking);
    const blocked=!ui.voice || ui.voicePaused || (voiceWaiting() && !ui.voiceInterrupt) || Boolean(ui.readyCue);
    if(blocked && !recognition.blocked)discardVoiceDrafts();
    recognition.setBlocked(blocked);
  }
  function voiceControls(){
    const active=ui.voice && !ui.voicePaused,interrupt=active && ui.speaking;
    const label=ui.voice?(ui.voicePaused?'Unmute microphone':interrupt?'Stop speaking':'Mute microphone'):'Start voice call';
    const button=$('focus-voice');
    button.dataset.call=ui.voice?(ui.voicePaused?'muted':'active'):'off';
    button.title=label;button.setAttribute('aria-label',label);
    if(interrupt)button.removeAttribute('aria-pressed');else button.setAttribute('aria-pressed',String(Boolean(active)));
    button.querySelector('use').setAttribute('href',interrupt?'#i-stop':ui.voice && ui.voicePaused?'#i-mic-off':'#i-mic');
    $('voice-aura').dataset.call=ui.voice?(ui.voicePaused?'muted':'active'):'off';
    $('voice-end').title=ui.voice?'End call':'Dismiss';
    $('voice-end').setAttribute('aria-label',ui.voice?'End voice call':'Dismiss voice message');
  }
  function voiceHelp(text,attention=false){
    const help=$('voice-help');help.textContent=text;help.dataset.attention=String(attention);
    help.hidden=!text || !attention;
  }
  function expandVoice(expanded){
    $('voice-details').hidden=!expanded;
    $('voice-details-toggle').setAttribute('aria-expanded',String(expanded));
    $('voice-details-toggle').title=expanded?'Hide call transcript':'Show call transcript';
    voiceHelp($('voice-help').textContent,$('voice-help').dataset.attention==='true');
    if(expanded)$('voice-captions').scrollTop=$('voice-captions').scrollHeight;
  }
  function voicePreview(){
    const entry=$('voice-captions').lastElementChild,preview=$('voice-caption-preview');
    preview.textContent=entry?`${entry.querySelector('small').textContent}: ${entry.querySelector('p').textContent}${entry.dataset.status==='failed'?' · Not sent':entry.dataset.status==='sending'?' · Sending':''}`:'';
    preview.hidden=!entry;preview.dataset.status=entry?.dataset.status || '';
  }
  function voiceState(text,phase='idle') {
    if($('voice-state').textContent!==text)$('voice-state').textContent=text;
    $('voice-call').dataset.state=phase;
    voiceControls();
  }
  function refreshVoiceState(){
    const ready=ui.voice && !ui.voicePaused && !ui.voiceStarting && !ui.speaking && (!voiceWaiting() || ui.voiceInterrupt) && recognition?.active && recognition.phase==='listening' && !recognition.processing;
    if(!ready)ui.wasVoiceReady=false;
    else if(!ui.wasVoiceReady){
      ui.wasVoiceReady=true;
      const cue=ui.readyCue=HubVoice.readyCue(recognition.context);
      cue.finished.finally(()=>{if(ui.readyCue===cue){ui.readyCue=null;refreshVoiceState();}});
    }
    syncVoiceCapture();
    if(!ui.voice || ui.voicePaused)return;
    if(ui.voiceStarting)voiceState('Connecting…','starting');
    else if(ui.speaking)voiceState(ui.voicePlaying?'Speaking':'Preparing voice…',ui.voicePlaying?'speaking':'thinking');
    else if(voiceWaiting() && !ui.voiceInterrupt)voiceState('Thinking','thinking');
    else if(recognition?.recording)voiceState('Listening','recording');
    else if(recognition?.processing)voiceState('Finishing your words…','transcribing');
    else if(ui.readyCue)voiceState('Ready…','starting');
    else voiceState('Listening','listening');
  }
  function voiceLevel(level){$('voice-call').style.setProperty('--voice-level',Math.max(0,Math.min(1,level)).toFixed(3));}
  function voiceCaption(role,text,key,status=''){
    const log=$('voice-captions'),atBottom=log.scrollHeight-log.scrollTop-log.clientHeight<60;
    let entry=[...log.children].find(node=>node.dataset.key===String(key));
    if(!entry){
      entry=document.createElement('div');entry.className='voice-caption '+role;entry.dataset.key=key;
      const heading=document.createElement('small'),body=document.createElement('p'),meta=document.createElement('span');
      heading.textContent=role==='user'?'You':'ARIA';meta.className='voice-caption-status';
      entry.append(heading,body,meta);log.append(entry);
      const id=role==='user'?'voice-transcript':'voice-reply';$(id)?.removeAttribute('id');body.id=id;
      while(log.children.length>20)log.firstElementChild.remove();
    }
    entry.dataset.status=status;entry.querySelector('p').textContent=text;
    const meta=entry.querySelector('.voice-caption-status');meta.textContent=status==='sending'?'Sending':status==='interrupted'?'Interrupted':status==='failed'?'Not sent':'';
    meta.hidden=!meta.textContent;
    voicePreview();
    if(atBottom)log.scrollTop=log.scrollHeight;
    return entry;
  }
  function stopPlayback(interrupted=false){
    ui.readyCue?.stop();ui.readyCue=null;
    if(ui.streamingReply){ui.streamingReply.request.speechMuted=true;ui.streamingReply=null;}
    if(interrupted && ui.speaking && ui.replyKey){
      const entry=[...$('voice-captions').children].find(node=>node.dataset.key===ui.replyKey);
      if(entry)voiceCaption('assistant',entry.querySelector('p').textContent,ui.replyKey,'interrupted');
    }
    ui.speechGeneration=(ui.speechGeneration||0)+1;ui.speaking=ui.voicePlaying=false;
    playback?.stop();syncVoiceCapture();recognition?.setPlayback(false);voiceLevel(0);voiceControls();
  }
  function interruptVoice(){
    if(!ui.voice || ui.voicePaused || !ui.speaking)return;
    ui.voiceInterrupt={session:ui.voiceSession};ui.wasVoiceReady=true;ui.deferredSpeech=null;
    stopPlayback(true);refreshVoiceState();
  }
  function voiceError(title,help){
    clearTimeout(voiceTimer);clearTimeout(microphoneTimer);
    ui.voiceStarting=ui.listening=false;ui.voicePaused=true;ui.voiceInterrupt=null;stopPlayback(true);recognition?.abort();
    discardVoiceDrafts();voiceState(title);voiceHelp(help,true);
  }
  function discardVoiceDrafts(){
    for(const turn of ui.voiceTurns?.values() || []){
      const entry=[...$('voice-captions').children].find(node=>node.dataset.key===turn.key);
      if(!entry)continue;
      const text=entry.querySelector('p').textContent;
      if(text==='…')entry.remove();else voiceCaption('user',text,turn.key,'failed');
    }
    ui.voiceTurns?.clear();
    voicePreview();
  }
  function listen(){
    clearTimeout(voiceTimer);
    syncVoiceCapture();
    if(!ui.voice || ui.voicePaused || ui.voiceStarting || recognition?.active || !recognition)return;
    ui.voiceStarting=true;voiceState('Connecting…','starting');
    microphoneTimer=setTimeout(()=>voiceError('Microphone did not start','Allow microphone access, then tap Unmute to retry.'),20000);
    try{recognition.start();}catch(error){voiceError('Microphone did not start',error.message || 'Tap Unmute to retry.');}
  }
  async function say(text,session=ui.voiceSession,handoff=null){
    if(!ui.voice || session!==ui.voiceSession || !text)return;
    ui.lastReply=text;$('voice-repeat').disabled=false;
    if(ui.voicePaused)return;
    // An arriving answer waits while the user is still speaking.
    if(recognition?.recording || recognition?.processing){ui.deferredSpeech={text,session,handoff};return;}
    stopPlayback();
    const generation=ui.speechGeneration;
    ui.replyKey='reply-'+generation;
    voiceCaption('assistant',text,ui.replyKey);
    if(!state.snapshot?.voice?.playback?.available){voiceHelp('Local voice is unavailable. You can still talk and read replies.',true);return;}
    ui.speaking=true;recognition.setPlayback(true);refreshVoiceState();
    try{
      const finished=await playback.speak(text);
      if(generation!==ui.speechGeneration)return;
      ui.speaking=ui.voicePlaying=false;syncVoiceCapture();recognition.setPlayback(false);voiceLevel(0);
      if(finished){refreshVoiceState();if(handoff)attention.record(handoff,'heard');}
    }catch(error){
      if(generation!==ui.speechGeneration)return;
      stopPlayback();voiceHelp(error.message || 'Audio failed. Open the call transcript to read the reply.',true);refreshVoiceState();
    }
  }
  async function voiceCommand(text){
    if(endCall(text))return true;
    const session=ui.voiceSession,addressed=HubRouting.supervisorRequest(text),request=addressed??text;
    const command=request.trim().toLowerCase().replace(/[.!?]+$/,'');
    if(/^(?:stay quiet|pause listening|stop listening|mute)$/.test(command)){pauseVoice();return true;}
    if(/^(?:stop|wait|hold on|stop talking)$/.test(command)){stopPlayback(true);refreshVoiceState();return true;}
    if(ui.mode==='agent' && addressed===null){
      const agent=agentById(state.selected),sent=await sendAgentMessage(text,null);
      const receipt=ui.mode==='agent' && state.selected===agent?.id?"I've sent that.":`I've sent that to ${agentTitle(agent)} on ${agent?.connection_label}.`;
      say(sent?receipt:'I couldn’t send that. The agent is busy or unavailable. Say “ARIA” to ask for help.',session);return sent;
    }
    if(/^(?:next|show (?:me )?(?:the )?next(?: one)?|something else)$/.test(command)){const item=next();say(spokenHandoff(item),session,item);return true;}
    if(/^(?:later|skip|pass|leave (?:this|it) for later)$/.test(command)){await complete('later',nowSeconds()+1800);return true;}
    if(/^(?:reviewed|mark (?:this |it )?(?:reviewed|handled))$/.test(command)){await complete('handled');return true;}
    if(/^(?:repeat|say that again)$/.test(command)){say(ui.lastReply,session);return true;}
    if(/^(?:leave|defer|save) (?:this|it) (?:for|until) tomorrow$/.test(command)){await complete('later',tomorrow());return true;}
    const focus=request.match(/^(?:focus on|prioritize) (.+?)(?: today)?[.!]?$/i);
    if(focus){try{await priority(focus[1]);say(`I've set your priority to ${focus[1]}.`,session);}catch(err){say(err.message,session);}return true;}
    const sent=await submitSupervisor(request,null,true);
    if(!state.pendingJob && !interaction.sending){const failed=interaction.outbox.at(-1);if(failed?.error){say(failed.error,session);return false;}}
    return sent;
  }
  async function runVoiceCommand(text,item=null){
    if(!ui.voice || ui.voiceCommand)return;
    const operation={session:ui.voiceSession};ui.voiceCommand=operation;
    refreshVoiceState();
    try{
      const sent=await voiceCommand(text);
      if(item && operation.session===ui.voiceSession)voiceCaption('user',text,item.key,sent?'sent':'failed');
    }catch(error){voiceError('Request failed',error.message);}
    finally{if(ui.voiceCommand===operation){ui.voiceCommand=null;refreshVoiceState();}}
  }
  function speakDeferred(){
    if(!ui.voice || ui.voicePaused || ui.speaking || voiceWaiting() || recognition?.recording || recognition?.processing)return;
    if(ui.deferredSpeech){const reply=ui.deferredSpeech;ui.deferredSpeech=null;say(reply.text,reply.session,reply.handoff);}
  }
  function configureRecognition(){
    recognition.onstart=()=>{
      clearTimeout(microphoneTimer);ui.voiceStarting=false;
      if(!ui.voice || ui.voicePaused){recognition.abort();return;}
      ui.listening=true;refreshVoiceState();voiceHelp('Speak to interrupt, or tap Stop. Say “ARIA, …” to reach ARIA.');
      if(state.snapshot?.voice?.playback?.available)playback.preload("I've sent that.");
    };
    recognition.onlevel=level=>{if(!ui.voicePlaying)voiceLevel(level);};
    recognition.oninterrupt=interruptVoice;
    recognition.onspeechstart=event=>{
      if(!ui.voice || ui.voicePaused || ui.speaking || (voiceWaiting() && !ui.voiceInterrupt)){refreshVoiceState();return;}
      stopPlayback(true);ui.deferredSpeech=null;
      const turn={key:'turn-'+event.utterance,revision:ui.voiceRouteVersion,target:state.selected,mode:ui.mode};
      ui.voiceTurns.set(event.utterance,turn);ui.currentVoiceTurn=event.utterance;
      voiceCaption('user','…',turn.key,'partial');refreshVoiceState();
    };
    recognition.onprocessing=()=>refreshVoiceState();
    recognition.onnomatch=event=>{
      const turn=ui.voiceTurns.get(event.utterance);
      if(turn){
        if(event.silent)[...$('voice-captions').children].find(node=>node.dataset.key===turn.key)?.remove();
        else voiceCaption('user','No speech detected.',turn.key,'failed');
      }
      ui.voiceTurns.delete(event.utterance);
      ui.voiceInterrupt=null;refreshVoiceState();
      voicePreview();
    };
    recognition.onsettled=()=>{refreshVoiceState();speakDeferred();};
    recognition.onend=()=>{clearTimeout(microphoneTimer);ui.voiceStarting=ui.listening=false;if(ui.voice && !ui.voicePaused)voiceTimer=setTimeout(listen,600);};
    recognition.onresult=event=>{
      if(!ui.voice || ui.voicePaused || ui.speaking || recognition.playing || recognition.blocked || (voiceWaiting() && !ui.voiceInterrupt)){refreshVoiceState();return;}
      const turn=ui.voiceTurns.get(event.utterance),result=event.results[event.results.length-1];if(!turn || !result?.[0])return;
      const text=result[0].transcript;
      if(!result.isFinal){voiceCaption('user',text,turn.key,'partial');return;}
      // Only an accepted final transcript can close this call.
      if(endCall(text))return;
      ui.voiceTurns.delete(event.utterance);
      const interrupted=ui.voiceInterrupt;ui.voiceInterrupt=null;
      if(turn.revision!==ui.voiceRouteVersion || turn.target!==state.selected || turn.mode!==ui.mode){voiceCaption('user',text,turn.key,'failed');voiceError('Recipient changed','That message was not sent. Speak again to the recipient shown above.');return;}
      // An unfinished request may still be executing accepted actions. Never
      // cancel it or queue this instruction merely because its audio stopped.
      // Keep unsent words visible and editable when the recipient is still busy.
      if(interrupted && voiceWaiting() && !/^(?:stop|wait|hold on|stop talking|mute|stay quiet|pause listening|stop listening)[.!?]*$/i.test(HubRouting.supervisorRequest(text)??text)){
        const addressed=HubRouting.supervisorRequest(text),supervisor=ui.mode==='supervisor' || addressed!==null;
        const unavailable=supervisor?(state.pendingJob || interaction.sending || state.snapshot?.supervisor.busy):voiceWaiting();
        if(unavailable){
          const input=$(ui.mode==='supervisor'?'supervisor-input':'agent-message');
          if(!input.value.trim()){
            input.value=ui.mode==='supervisor'?(addressed??text):text;
            if(ui.mode==='agent')detail.drafts.set(state.selected,text);
            sizeAgentInput();sizeSupervisorInput();persistDrafts();
          }
          voiceCaption('user',text,turn.key,'failed');
          voiceHelp('Still working. Your words were not sent. Send them when ready.',true);refreshVoiceState();return;
        }
      }
      voiceCaption('user',text,turn.key,'sending');
      ui.deferredSpeech=null;runVoiceCommand(text,turn);
    };
    recognition.onerror=event=>{
      if(!ui.voice || ['aborted','no-speech'].includes(event.error))return;
      const errors={
        'audio-interrupted':['Audio paused','Tap the microphone to resume.'],
        'not-allowed':['Microphone access blocked','Allow microphone access for this site, then tap Unmute to retry.'],
        'audio-capture':['No microphone available','Check the microphone and its permissions, then tap Unmute.']
      };
      voiceError(...(errors[event.error] || ['Voice interrupted',event.message || 'Tap Unmute to retry.']));
    };
    playback=new HubVoice.LocalPlayback(()=>state.snapshot?.csrf_token || '',()=>recognition.context);
    playback.onstart=()=>{ui.voicePlaying=true;refreshVoiceState();};
    playback.onlevel=level=>{if(ui.voicePlaying)voiceLevel(level);};
  }
  function pauseVoice(){
    ui.voicePaused=true;ui.voiceInterrupt=null;stopPlayback(true);recognition?.abort();ui.deferredSpeech=null;
    discardVoiceDrafts();
    clearTimeout(voiceTimer);clearTimeout(microphoneTimer);voiceState('Muted');voiceHelp('Tap the microphone to unmute.');
  }
  function endVoice(){
    const restoreFocus=$('voice-call').contains(document.activeElement);
    ui.voiceSession=(ui.voiceSession||0)+1;ui.voice=false;ui.voicePaused=true;stopPlayback();
    ui.voiceStarting=ui.listening=false;ui.voiceTurns?.clear();ui.voiceCommand=null;ui.voiceInterrupt=null;ui.deferredSpeech=null;
    ui.wasVoiceReady=false;
    ui.releaseMicrophone?.();ui.releaseMicrophone=null;
    playback?.clear();
    clearTimeout(voiceTimer);clearTimeout(microphoneTimer);clearInterval(speechTimer);
    recognition?.close();$('voice-call').hidden=true;voiceControls();
    composer.hidden=!['agent','home'].includes(state.view);
    if(restoreFocus && !composer.hidden)$('focus-voice').focus({preventScroll:true});
    ui.wakeLock?.release().catch(()=>{});ui.wakeLock=null;
  }
  function endCall(text){
    if(!HubRouting.endCallRequested(text))return false;
    const active=ui.voice;
    endVoice();toast(active?'Call ended.':'Microphone is off.');return true;
  }
  $('focus-voice').onclick=async()=>{
    if(ui.voiceOpening)return;
    if(ui.voice){
      if(!ui.voicePaused && ui.speaking){interruptVoice();listen();}
      else if(!ui.voicePaused)pauseVoice();
      else{ui.voicePaused=false;voiceHelp('Allow microphone access if your browser asks.',true);listen();}
      return;
    }
    if(!recognition){
      try{if(state.snapshot?.voice?.available && window.HubVoice?.supported())recognition=new HubVoice.LocalRecognition(()=>state.snapshot?.csrf_token || '');}catch{}
      if(recognition)configureRecognition();
    }
    $('voice-call').hidden=false;composer.hidden=false;expandVoice(false);
    $('voice-captions').replaceChildren();ui.lastReply='';$('voice-repeat').disabled=true;
    voicePreview();$('voice-secure').hidden=true;
    for(const id of ['voice-next','voice-later'])$(id).disabled=!recognition;
    if(!window.isSecureContext){
      voiceState('Open the secure hub');voiceHelp('Connect Tailscale, then open the secure address for microphone access.',true);
      try{const url=new URL(state.snapshot?.nodes?.find(node=>node.local)?.url);if(url.protocol==='https:' && !url.username && !url.password){$('voice-secure').href=url.href;$('voice-secure').hidden=false;}}catch{}
      return;
    }
    if(!recognition){voiceState('Voice unavailable');voiceHelp(state.snapshot?.voice?.available?'Update Firefox and allow microphone access for this site.':'Local transcription is not installed on this hub.',true);return;}
    const openingSession=ui.voiceSession;
    ui.voiceOpening=true;
    const release=window.HubLive?await HubLive.microphone():()=>{};
    ui.voiceOpening=false;
    if(ui.voiceSession!==openingSession){release?.();return;}
    if(!release){voiceState('Call open in another tab');voiceHelp('End that call, then tap the microphone here.',true);return;}
    ui.releaseMicrophone=release;
    ui.voiceSession=(ui.voiceSession||0)+1;ui.voice=true;ui.voicePaused=false;ui.voiceTurns=new Map();ui.deferredSpeech=null;
    ui.voiceSince=nowSeconds();ui.voiceHandoffs=new Set(queue().map(i=>i.id));state.voice=false;
    voiceHelp('Allow microphone access if your browser asks.',true);
    listen();
    const session=ui.voiceSession;
    navigator.wakeLock?.request('screen').then(lock=>{if(ui.voice && ui.voiceSession===session)ui.wakeLock=lock;else lock.release();}).catch(()=>{});
    clearInterval(speechTimer);
    speechTimer=setInterval(()=>{if(ui.voice){listen();speakDeferred();refreshVoiceState();}},250);
  };
  $('voice-end').onclick=endVoice;
  $('voice-details-toggle').onclick=()=>expandVoice($('voice-details').hidden);
  composer.addEventListener('focusin',event=>{if(event.target.matches('textarea'))expandVoice(false);});
  $('voice-repeat').onclick=()=>say(ui.lastReply);
  $('voice-next').onclick=()=>runVoiceCommand('ARIA, next');$('voice-later').onclick=()=>runVoiceCommand('ARIA, later');
  if(state.snapshot)render();
})();
