"use strict";
const appVersion = document.currentScript?.src.match(/\/static\/([a-f0-9]{24})\/app\.js(?:[?#]|$)/)?.[1] || '';
const $ = (id) => document.getElementById(id);
const state = { snapshot: null, filter: "all", query: "", selected: null, context: null, cursor: null, view: "agents", returnFocus: null, listScroll: 0, voice: false, chatId: 0, spokenChatId: 0, initialized: false, pendingJob: null, polling: false, detailPolling: false, refreshing: false };
let foldedHosts=new Set(),hostSearch='';
const searchFoldedHosts=new Set();
try { const saved=JSON.parse(localStorage.getItem('hub-folded-hosts'));if(Array.isArray(saved))foldedHosts=new Set(saved.filter(id=>typeof id==='string')); } catch {}
const hostFolds=()=>state.query?searchFoldedHosts:foldedHosts;
function foldHost(id,folded) {
  const folds=hostFolds();if(folded)folds.add(id);else folds.delete(id);
  if(!state.query)try { localStorage.setItem('hub-folded-hosts',JSON.stringify([...foldedHosts])); } catch {}
  renderAgents();
}
function revealHost(id) {
  state.query='';$('search').value='';state.filter='all';foldHost(id,false);showView('agents');render();
  const heading=[...$('agents').querySelectorAll('[data-toggle-host]')].find(node=>node.dataset.toggleHost===id);
  heading?.focus({preventScroll:true});heading?.scrollIntoView({block:'nearest'});
}
const detail = { history: true, mode: "reader", text: null, signature: null, pending: null, preview: new HubHistory.Preview(), order: [], drafts: new Map(), sending: new Set(), generation: 0 };
const stopAgent=document.createElement('button');stopAgent.id='stop-agent';stopAgent.type='button';stopAgent.className='icon-button';stopAgent.hidden=true;
stopAgent.setAttribute('aria-label','Interrupt agent');stopAgent.title='Interrupt agent';
stopAgent.innerHTML='<svg class="icon" aria-hidden="true"><use href="#i-stop"/></svg>';
$('send-agent').before(stopAgent);
const interaction = { outbox: [], receipts: new Map(), queues: new Map(), observations: new Map(), sending: null, job: null, jobPolling: false, jobError: false, lastState: 0, lastJob: 0, busySince: 0 };
const nowSeconds = () => Date.now() / 1000;
const requestId = () => `web-${Date.now().toString(36)}-${Array.from(crypto.getRandomValues(new Uint32Array(2)), n => n.toString(36)).join("")}`;
const terminalView=HubTerminal.mount($('terminal'),{back:()=>setOutputMode('reader'),copy:async text=>{try{await navigator.clipboard.writeText(text);return true;}catch{toast('Could not copy terminal output.',true);return false;}}});
const nativeQueueNode=document.createElement('section');nativeQueueNode.id='agent-native-queue';$('agent-reader').append(nativeQueueNode);
const nativeQueue=HubQueue.mount(nativeQueueNode,{send:value=>api('/api/action',{...value,command_id:requestId()}),refresh:()=>detailPoll(true)});
const approvalNode=document.createElement('section');approvalNode.id='agent-approvals';$('agent-reader').append(approvalNode);
const nativeApprovals=HubApprovals.mount(approvalNode,{send:value=>api('/api/action',{...value,command_id:requestId()}),refresh:()=>detailPoll(true)});
let deliveryStore;
try { deliveryStore=new HubDelivery.Store(localStorage); } catch { deliveryStore=new HubDelivery.Store(null); }
const deliveries=new Map();
function rememberDelivery(entry){
  if(typeof entry.baseline==='string')entry.baselineDigest=HubReading.digest({text:entry.baseline});
  deliveries.set(entry.request_id,entry);
  const saved=deliveryStore.save(entry);
  if(!saved && !interaction.storageWarning){interaction.storageWarning=true;queueMicrotask(()=>toast('This browser could not save delivery receipts. Keep this tab open until delivery is confirmed.',true));}
}
function restoreDelivery(entry){
  if(entry.status==='sending'){entry.status='uncertain';entry.error='Checking delivery…';}
  entry.needsCheck=!entry.confirmed&&!entry.dismissed;deliveries.set(entry.request_id,entry);
  if(entry.dismissed)return;
  if(entry.kind==='supervisor'){entry.role='user';interaction.outbox.push(entry);}
  else if(entry.kind==='queue'){
    const items=interaction.queues.get(entry.agent_id)||[];items.push(entry);interaction.queues.set(entry.agent_id,items);
    if(entry.status==='sent')interaction.receipts.set(entry.agent_id,{...entry,confirmed:false});
  }else interaction.receipts.set(entry.agent_id,entry);
}
for(const entry of deliveryStore.load())restoreDelivery(entry);
deliveryStore.prune();
let reconcilingDeliveries=false;
async function reconcileDeliveries(force=false){
  if(!state.snapshot || document.hidden || reconcilingDeliveries)return;
  if(force)for(const entry of deliveries.values()){entry.checkedAt=0;entry.checks=0;}
  const pending=[...deliveries.values()].filter(entry=>entry.needsCheck && !entry.confirmed && !entry.dismissed &&
    interaction.sending!==entry && !detail.sending.has(entry.agent_id) && (entry.checks||0)<3 && nowSeconds()-(entry.checkedAt||0)>10);
  if(!pending.length)return;
  reconcilingDeliveries=true;
  try{
    for(const entry of pending.slice(-4)){
      entry.checkedAt=nowSeconds();entry.checks=(entry.checks||0)+1;
      try{
        const result=await api('/api/receipt',{kind:entry.kind,request:entry.request,...(entry.owner?{owner:entry.owner}:{})});
        HubDelivery.apply(entry,result);
        if(entry.kind==='queue' && entry.status==='sent' && !entry.dismissed){
          entry.confirmed=true;
          const prior=interaction.receipts.get(entry.agent_id);
          if(!prior || prior.created<=entry.created)interaction.receipts.set(entry.agent_id,{...entry,confirmed:false});
        }
        rememberDelivery(entry);
      }catch{ /* A host outage leaves the existing receipt and draft intact. */ }
    }
    if(state.snapshot){renderChat();if(state.view==='agent'){renderAgentConversation();updateDetailMeta();}}
  }finally{reconcilingDeliveries=false;}
}
window.addEventListener('storage',event=>{
  if(!event.key?.startsWith('hub-delivery-v1:') || !event.newValue)return;
  // Only merge requests this tab knows. Another tab's send must not change the
  // current conversation, draft, activity timer or recipient.
  let value;try{value=JSON.parse(event.newValue);}catch{return;}
  const entry=deliveries.get(value.request_id);if(!entry)return;
  if(value.dismissed)dismissDelivery(entry,false);
  if(HubDelivery.accepted(value)){entry.status=value.status;entry.error='';entry.confirmed ||= value.confirmed;entry.needsCheck=false;}
});
const labels = { running: "Working", waiting: "Needs input", blocked: "Blocked", done: "Needs review", idle: "Available", unknown: "Unknown" };
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const relativeTime = (epoch) => { const seconds = Math.max(0, Date.now() / 1000 - epoch); return seconds < 60 ? "just now" : seconds < 3600 ? `${Math.floor(seconds / 60)}m ago` : `${Math.floor(seconds / 3600)}h ago`; };
const agentById = (id) => state.snapshot?.agents.find((agent) => agent.id === id);
const agentTitle = agent => agent?.display_title || agent?.title || 'Agent';
const agentVariant = agent => (state.snapshot?.agents || []).some(other=>other.id!==agent.id && other.connection_id===agent.connection_id && agentTitle(other)===agentTitle(agent))?`${agent.title} · ${agent.window || agent.pane}`:'';
const shortPath = (path) => path.replace(/^\/home\/[^/]+/, "~");
let toastTimer, toastNotices = [];
const renderedMarkup = new WeakMap();
let restoringPhoneRoute = false;
function toast(message, error = false, action = null) {
  toastNotices = action?.notices || [];
  const node=$('toast');node.replaceChildren();node.classList.toggle('error',error);node.classList.toggle('toast-link',Boolean(action?.whole));node.hidden=false;clearTimeout(toastTimer);
  if(!action?.whole)node.append(document.createTextNode(message));
  if(action){
    const button=document.createElement('button');button.type='button';button.textContent=action.whole?message:action.label;
    if(action.whole){
      button.setAttribute('aria-label',`${action.label}: ${message}`);
      const signal=document.createElement('span');signal.className='toast-signal';signal.setAttribute('aria-hidden','true');
      signal.innerHTML='<svg class="icon"><use href="#i-bell"/></svg>';
      const copy=document.createElement('span');copy.className='toast-copy';copy.textContent=message;
      const arrow=document.createElement('span');arrow.className='toast-arrow';arrow.setAttribute('aria-hidden','true');arrow.innerHTML='<svg class="icon"><use href="#i-arrow"/></svg>';
      button.replaceChildren(signal,copy,arrow);
    }
    button.onclick=async()=>{
      if(action.whole)clearTimeout(toastTimer);button.disabled=true;
      try{await action.run();if(action.whole && node.contains(button))node.hidden=true;}
      catch(error){toast(error.message,true);}
      finally{button.disabled=false;}
    };
    if(action.whole){
      button.onfocus=button.onpointerenter=()=>clearTimeout(toastTimer);
      button.onblur=button.onpointerleave=()=>{if(!button.disabled && !button.matches(':focus, :hover') && node.contains(button))toastTimer=setTimeout(()=>{node.hidden=true;},8000);};
    }
    node.append(button);
  }
  toastTimer=setTimeout(()=>{node.hidden=true;},error?9000:action?8000:4500);
}
async function api(path, body) {
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), body === undefined ? 10000 : body.type === "fork" ? 115000 : 25000);
  try {
    const response = await fetch(path, { ...(body === undefined ? { cache: "no-store" } : { method: "POST", headers: { "Content-Type": "application/json", "X-Hub-Token": state.snapshot?.csrf_token || "" }, body: JSON.stringify(body) }), signal: controller.signal });
    const result = await response.json();
    if (!response.ok) { const error = new Error(result.error || `Request failed (${response.status})`); error.status = response.status; throw error; }
    return result;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("The hub did not respond in time.");
    throw error;
  } finally { clearTimeout(timer); }
}
const liveBases = new Map();
async function liveApi(path) {
  const base=liveBases.get(path), separator=path.includes('?')?'&':'?';
  let packet=await api(path+separator+'updates=1'+(base?'&since='+encodeURIComponent(base.version):''));
  // Older hosts and diagnostic fixtures may still return full JSON.
  if(typeof packet.version!=='string')return packet;
  let value;
  try { value=HubUpdates.apply(base,packet); }
  catch { liveBases.delete(path);packet=await api(path+separator+'updates=1');value=HubUpdates.apply(null,packet); }
  liveBases.delete(path);liveBases.set(path,{version:packet.version,value});
  while(liveBases.size>5)liveBases.delete(liveBases.keys().next().value);
  return structuredClone(value);
}
function replaceMarkup(element, markup, attribute) {
  if (renderedMarkup.get(element) === markup) return;
  const active = document.activeElement?.closest(`[${attribute}]`);
  const focused = active && element.contains(active) ? active.getAttribute(attribute) : null;
  element.innerHTML = markup;
  renderedMarkup.set(element, markup);
  if (focused) [...element.querySelectorAll(`[${attribute}]`)].find((node) => node.getAttribute(attribute) === focused)?.focus({ preventScroll: true });
}
function mobilePane(name) {
  if (window.HubFocus && name === "supervisor") { window.HubFocus.showSupervisor(); name = "workspace"; }
  const shell = document.querySelector(".app-shell");
  shell.dataset.mobileView = name;
  shell.dataset.page = state.view;
  const page = name === "supervisor" ? "supervisor" : ["notifications", "status"].includes(state.view) ? state.view : "workspace";
  document.querySelectorAll("[data-mobile-pane],[data-mobile-page]").forEach((button) => {
    const active = (button.dataset.mobilePane || button.dataset.mobilePage) === (window.innerWidth > 800 ? (["notifications", "status"].includes(state.view) ? state.view : "workspace") : page);
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
  });
  $("mobile-page-title").textContent = name === "supervisor" ? "ARIA" : ({notifications:"Updates",status:"Status",launch:"New agent",connection:"Add host",activity:"Activity"}[state.view] || "Agents");
  if (name === "supervisor") sizeSupervisorInput();
  if (window.matchMedia("(max-width:800px)").matches && !restoringPhoneRoute) {
    const route = {view:state.view,pane:name,agent:state.selected,trail:[...bufferTrail]};
    const previous = history.state?.hubRoute;
    if (!previous) history.replaceState({...history.state,hubRoute:route}, "");
    else if (["view","pane","agent"].some(key => route[key] !== previous[key])) history.pushState({hubRoute:route}, "");
  }
}
let supervisorInputSize=null;
function sizeSupervisorInput() {
  const input = $("supervisor-input");
  if (!window.matchMedia("(max-width:800px)").matches) { input.style.height = ""; supervisorInputSize=null; return; }
  if (!input.getClientRects().length) return;
  // Re-measuring an unchanged draft briefly expands the conversation and
  // clamps its scroll position, even when the final input height is unchanged.
  if (supervisorInputSize?.value===input.value && supervisorInputSize.width===input.clientWidth) return;
  input.style.height = "44px";
  if (input.value) input.style.height = `${Math.min(120, Math.max(44, input.scrollHeight))}px`;
  supervisorInputSize={value:input.value,width:input.clientWidth};
}
const viewIds = { agents: "agent-list-view", agent: "agent-view", connection: "connection-view", launch: "launch-view", activity: "activity-view", notifications: "notifications-view", status: "status-view" };
const bufferTrail = [];
function showView(name) {
  if (window.HubFocus?.navigate(name)) return;
  state.view = name;
  Object.entries(viewIds).forEach(([key, id]) => { $(id).hidden = key !== name; });
  $("activity-button").setAttribute("aria-expanded", String(name === "activity"));
  mobilePane("workspace");
  updatePath();
  window.HubFocus?.decorate();
}
function openBuffer(name) {
  if (state.view !== name) bufferTrail.push({view:state.view,pane:document.querySelector(".app-shell").dataset.mobileView});
  if (state.view === "agents") {
    state.listScroll = $("agents").scrollTop;
    const active = document.activeElement;
    state.returnFocus = active?.closest("[data-agent]") ? { agent: active.closest("[data-agent]").dataset.agent } : { id: active?.id };
  }
  showView(name);
}
function closeBuffer() {
  const previous = bufferTrail.pop() || {view:"agents",pane:"workspace"};
  showView(previous.view);
  mobilePane(previous.pane);
  $("agents").scrollTop = state.listScroll;
  const target = state.returnFocus?.agent ? [...document.querySelectorAll("[data-agent]")].find((row) => row.dataset.agent === state.returnFocus.agent) : $(state.returnFocus?.id);
  if (previous.view === "agents" && previous.pane === "workspace") target?.focus({ preventScroll: true });
}
function updatePath() {
  const agent = agentById(state.selected);
  $("workspace-path").textContent = state.view === "agent" && agent ? `/agents/${agent.connection_label}/${agent.title}` : state.view === "connection" ? "/connections/new" : state.view === "launch" ? "/agents/new" : state.view === "activity" ? "/activity" : state.view === "notifications" ? "/updates" : "/agents";
}
function setFilter(filter) { state.filter = filter; showView("agents"); render(); }
function speak(text) {
  if (!state.voice || !("speechSynthesis" in window)) return;
  const speech = new SpeechSynthesisUtterance(text.replace(/[*#`]/g, "").slice(0, 2200));
  speech.rate = 1.04;
  speech.onstart = () => { $("voice-label").textContent = "Speaking"; };
  speech.onend = speech.onerror = () => { $("voice-label").textContent = state.voice ? "Voice on" : "Voice off"; };
  window.speechSynthesis.speak(speech);
}
function briefLocally() {
  const agents = state.snapshot?.agents || [];
  const attention = agents.filter((a) => a.needs_attention && !a.acknowledged);
  const running = agents.filter((a) => a.connection_online && a.state === "running");
  const offline = state.snapshot?.connections.filter((c) => c.status === "offline") || [];
  const details = attention.slice(0, 4).map((a) => `${a.title} on ${a.connection_label}: ${a.summary}.`).join(" ");
  return `${agents.length} agents across ${state.snapshot?.connections.length || 0} connections. ${running.length} working. ${attention.length} need attention. ${details}${offline.length ? ` ${offline.length} connection is unavailable; its status is stale.` : ""}`;
}
function renderConnections() {
  replaceMarkup($("connections"), state.snapshot.connections.map((connection) => `<div class="connection-item"><button class="connection-button" data-connection="${esc(connection.id)}" title="${esc(connection.error || (connection.kind === "local" ? "Local tmux" : connection.target))}"><span class="dot ${connection.status === "online" ? "green" : connection.status === "offline" ? "red" : "amber"}"></span><span class="connection-name"><strong>${esc(connection.label)}</strong><small>${connection.status === "offline" ? "Offline · retained state" : connection.kind === "local" ? "local" : connection.kind === "peer" ? (connection.peer_role === "worker" ? "worker" : "synced host") : esc(connection.target)}</small></span><span class="connection-count">${connection.agent_count}</span></button>${connection.id !== "local" && !connection.managed && connection.kind !== "peer" ? `<button class="connection-remove" data-remove-connection="${esc(connection.id)}" aria-label="Remove ${esc(connection.label)} connection">×</button>` : ""}</div>`).join(""), "data-connection");
}
function renderAgents() {
  if(hostSearch!==state.query){hostSearch=state.query;searchFoldedHosts.clear();}
  const agents = state.snapshot.agents;
  const attentionIds = HubAttention.pendingAgents(state.snapshot);
  const attention = agents.filter((a) => attentionIds.has(a.id));
  const actions=paletteActions().filter(action=>state.query && (action.search+' '+action.label.toLowerCase()).includes(state.query.replace(/^>\s*/,'')));
  const actionMarkup=actions.length?'<div class="palette-actions" aria-label="Actions">'+actions.map(action=>`<button class="palette-action" data-palette-action="${action.id}" data-item-key="action:${action.id}" ${action.disabled?'disabled':''}><svg class="icon" aria-hidden="true"><use href="#i-${action.icon}"/></svg><span>${esc(action.label)}</span></button>`).join('')+'</div>':'';
  $("nav-count").textContent = agents.length;
  $("mobile-agent-count").textContent = agents.length;
  $("attention-count").textContent = attention.length;
  let shown = agents.filter((a) => state.filter === "all" || (state.filter === "attention" ? attentionIds.has(a.id) : a.state === state.filter && a.connection_online));
  if (state.query) shown = shown.filter((a) => `${agentTitle(a)} ${a.title} ${a.cwd} ${a.connection_label} ${a.runtime} ${a.summary}`.toLowerCase().includes(state.query));
  $("list-heading").textContent = state.filter === "attention" ? "Attention" : state.filter === "running" ? "Working" : state.filter === "idle" ? "Available" : "Agents";
  $("list-caption").textContent = shown.length === agents.length ? "" : `${shown.length} of ${agents.length}`;
  document.querySelectorAll(".filter-pill").forEach((el) => el.classList.toggle("active", el.dataset.filter === state.filter));
  if (!state.snapshot.connections.length && !actions.length) {
    renderedMarkup.delete($("agents"));
    $("agents").innerHTML = '<div class="empty-state"><h3>No hosts connected</h3><p>Connect a host to see its agents.</p></div>';
    return;
  }
  if (shown.length && !shown.some((agent) => agent.id === state.cursor)) state.cursor = shown[0].id;
  const attentionIndicator = '<span class="agent-attention" role="img" aria-label="Needs your attention" title="Needs your attention"><svg class="icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 4v4.5M8 11.5v.01"/></svg></span>';
  const agentRow = (agent) => `<button class="agent-card ${agent.id === state.cursor ? "cursor" : ""} ${attentionIds.has(agent.id) ? "attention" : ""} ${!agent.connection_online ? "offline" : ""}" data-agent="${esc(agent.id)}" data-item-key="agent:${esc(agent.id)}"><span class="runtime-icon ${esc(agent.runtime)}"><svg class="icon"><use href="#i-agents"/></svg></span><span class="card-title">${attentionIds.has(agent.id) ? attentionIndicator : ''}<span class="card-name">${esc(agentTitle(agent))}</span>${agent.mark.focused ? '<span class="focus-chip">focus</span>' : ""}</span>${window.HubFocus ? HubFocus.badge(agent) : `<span class="state-badge">${esc(labels[agent.state] || agent.state)}</span>`}<span class="card-path">${esc(agentVariant(agent) || shortPath(agent.cwd))}</span><span class="card-time">${agent.acknowledged ? "reviewed" : relativeTime(agent.changed_at)}</span>${!["Working", "ready", "Idle", "No lifecycle hook yet", "Turn ended; ready for review", "finished"].includes(agent.summary) ? `<span class="card-summary">${esc(agent.summary)}</span>` : ""}</button>`;
  const markup = state.snapshot.connections.map(host => {
    const members = shown.filter(agent => agent.connection_id === host.id);
    const all=agents.filter(agent=>agent.connection_id===host.id),pending=all.filter(agent=>attentionIds.has(agent.id)).length;
    const folded=hostFolds().has(host.id),bodyId='host-members-'+encodeURIComponent(host.id);
    const count=members.length===all.length?String(all.length):`${members.length}/${all.length}`;
    const label=`${host.label}, ${all.length} agents${pending?`, ${pending} need attention`:''}${host.status!=='online'?', offline':''}`;
    return `<section class="host-group" data-host-group="${esc(host.id)}" data-host-tone="${window.HubFocus?.hostTone(host.id) || 0}" aria-label="${esc(host.label)} agents"><h2 class="host-group-heading"><button class="host-group-toggle" data-toggle-host="${esc(host.id)}" data-item-key="host:${esc(host.id)}" aria-expanded="${!folded}" aria-controls="${esc(bodyId)}" aria-label="${esc(label)}"><span class="host-chevron" aria-hidden="true"></span><span class="host-group-name">${esc(host.label)}</span>${host.status !== "online" ? '<span class="host-offline">offline</span>' : ''}<span class="host-agent-count" aria-hidden="true">${pending?'<span class="host-pending"></span>':''}${count}</span></button></h2><div id="${esc(bodyId)}"${folded?' hidden':''}>${members.map(agentRow).join('') || `<p class="host-empty">${all.length?'No matches':'No agents'}</p>`}</div></section>`;
  }).join("");
  replaceMarkup($("agents"), actionMarkup+markup, "data-item-key");
}
function deliveryMarkup(message, target) {
  if (!message.status || (target === "supervisor" && message.confirmed && message.status === "sent")) return "";
  const label = { sending: "Sending…", queued: "Queued", sent: "Sent", failed: "Send failed", uncertain: "Delivery unconfirmed" }[message.status];
  return `<div class="delivery-state ${message.status}"><span>${esc(label)}</span>${message.error ? `<span class="delivery-error">${esc(message.error)}</span><button class="text-button" data-restore-${target}="${esc(message.request_id)}">Restore draft</button><button class="text-button" data-dismiss-delivery="${esc(message.request_id)}" aria-label="Dismiss delivery warning">Dismiss</button>` : ""}</div>`;
}
function dismissDelivery(entry,persist=true){
  entry.dismissed=true;entry.needsCheck=false;
  if(interaction.receipts.get(entry.agent_id)?.request_id===entry.request_id)interaction.receipts.delete(entry.agent_id);
  if(persist)rememberDelivery(entry);
  if(state.snapshot){renderChat();renderAgentConversation();updateDetailMeta();}
}
async function restoreDeliveryDraft(entry,input){
  const text=HubMessageContent.displayText(entry),before=input.value,selected=state.selected,context=state.context;
  const unchanged=()=>input.value===before&&(input.id==='agent-message'?state.selected===selected:state.context===context);
  if(before && before!==text){toast('Your draft already has text. Clear it before restoring this message.',true);return;}
  try{
    if(entry.attachments?.length)await HubImages.restoreAttachments(entry.attachments,input,unchanged);
    if(!unchanged()){toast('Your new draft is kept. The old message is still available.',true);return;}
    input.value=text;
    if(input.id==='agent-message'){detail.drafts.set(state.selected,text);sizeAgentInput();}
    else sizeSupervisorInput();
    window.HubFocus?.persistDrafts();dismissDelivery(entry);input.focus();
  }catch(error){toast(error.message||'Could not restore this draft.',true);}
}
function updateStatus(item) {
  return item.handled_at ? "Handled" : item.held ? "Held" : item.read_at ? "Seen · Pending" : "New · Pending";
}
function updateActions(item) {
  const id = item.notification_id || item.id;
  return `<div class="update-actions"><span class="update-status${item.handled_at ? " handled" : ""}">${updateStatus(item)}</span><button class="text-button" ${item.handled_at ? "data-reopen-notification" : "data-handle-notification"}="${esc(id)}">${item.handled_at ? "Reopen" : "Mark handled"}</button></div>`;
}
function updateContent(item) {
  const agent = agentById(item.agent_id);
  const lines = (item.text || "").split("\n");
  const title = item.title || lines.shift() || "Agent update";
  const body = item.body || lines.join("\n");
  return {
    title: agent ? `${agent.title} · ${agent.connection_label}` : title,
    body: item.original_reply ? body : body.replace(/^Agent reports:\s*/, ""),
  };
}
function updateBody(item) {
  const body=updateContent(item).body;
  return item.original_reply && window.HubMarkdown
    ? `<div class="message-text rich-message" data-agent-id="${esc(item.agent_id || '')}">${HubMarkdown.render(body)}</div>`
    : `<div class="message-text">${esc(body)}</div>`;
}
function updateSource(item) {
  const title=esc(updateContent(item).title),id=esc(item.notification_id || item.id);
  return agentById(item.agent_id)
    ? `<button type="button" class="update-source update-source-link" data-notification-agent="${esc(item.agent_id)}" data-notification-id="${id}" aria-label="View ${title} conversation">${title}</button>`
    : `<span class="update-source">${title}</span>`;
}
let updateFilter = "pending";
function reconcileChat(forceScroll = false) {
  const chat = $("chat"), atBottom = chat.scrollHeight - chat.scrollTop - chat.clientHeight < 80;
  const messages = (window.HubFocus ? HubFocus.scopeMessages(state.snapshot.messages) : state.snapshot.messages).filter(message => message.role !== 'update' || !message.handled_at);
  for (const pending of interaction.outbox) {
    if (messages.some((message) => message.role === "user" && message.request_id === pending.request_id)) { pending.confirmed = true; pending.status = "sent"; pending.error = ""; if(pending.request)rememberDelivery(pending); }
  }
  const visible = messages.map((message) => ({ ...message, ...(message.role === "user" ? interaction.outbox.find((entry) => entry.request_id === message.request_id) : null), key: message.request_id ? `${message.role}:${message.request_id}` : `server:${message.id}` }));
  for (const entry of interaction.outbox.filter((entry) => !entry.confirmed && !entry.dismissed && (!window.HubFocus || (entry.context_agent_id || "") === (state.context || "")))) {
    const after = visible.findIndex((message) => message.key === entry.afterKey);
    visible.splice(after + 1, 0, { ...entry, key: `user:${entry.request_id}` });
  }
  const existing = new Map([...chat.querySelectorAll("[data-message-key]")].map((node) => [node.dataset.messageKey, node]));
  if (visible.length) chat.querySelector(".chat-empty")?.remove();
  let addedReply = false;
  visible.forEach((message, index) => {
    let node = existing.get(message.key);
    if (!node) { node = document.createElement("div"); node.dataset.messageKey = message.key; node.className = `chat-message ${message.role} message-arrive`; addedReply ||= ["assistant", "error", "update"].includes(message.role); }
    const signature = JSON.stringify([message.role, message.text, message.attachments, message.created, message.status, message.error, message.read_at, message.handled_at, message.held, message.agent_id, message.context_agent_id, message.original_reply, Boolean(agentById(message.agent_id)), message.role === "update" ? updateContent(message).title : null]);
    const changed = node._signature !== signature;
    if (changed) {
      node.dataset.agentId = message.context_agent_id || message.agent_id || "";
      node.innerHTML = `<span class="message-role">${message.role === "user" ? "You" : message.role === "error" ? "Error" : message.role === "update" ? "ARIA update" : "ARIA"} <time>${esc(new Date(message.created * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }))}</time></span><div class="message-text${message.role === "assistant" ? " rich-message" : ""}">${message.role === "assistant" && window.HubMarkdown ? AgentReader.replyHTML(HubMarkdown.render(message.text),message.text,message.key) : esc(message.text)}</div>${message.role === "user" ? deliveryMarkup(message, "supervisor") : ""}`;
      if(message.role==='user'){
        const body=node.querySelector('.message-text');body.textContent=HubMessageContent.displayText(message);
        body.insertAdjacentHTML('beforebegin',window.HubImages?.html(message.attachments)||'');
      }
      if (message.role === "update") {
        node.classList.toggle('unread', !message.read_at);
        node.innerHTML = `<span class="message-role">${updateSource(message)}<time>${esc(new Date(message.created * 1000).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit",hourCycle:"h23"}))}</time></span>${updateBody(message)}${updateActions(message)}`;
      }
      node._signature = signature;
    }
    if (chat.children[index] !== node) chat.insertBefore(node, chat.children[index] || null);
    if (changed) { AgentReader.prepareReplies(node); window.HubMarkdown?.enhance(node); }
    existing.delete(message.key);
  });
  existing.forEach((node) => node.remove());
  if (!visible.length && !chat.querySelector(".chat-empty")) chat.innerHTML = '<div class="chat-empty">Ask for an update, or give an agent its next task.</div>';
  if (forceScroll || atBottom) { chat.scrollTop = chat.scrollHeight; $("chat-new-output").hidden = true; }
  else if (addedReply) $("chat-new-output").hidden = false;
}
function renderChat(forceScroll = false) {
  const messages = window.HubFocus ? HubFocus.scopeMessages(state.snapshot.messages) : state.snapshot.messages;
  const latest = messages.at(-1)?.id || 0;
  reconcileChat(forceScroll);
  const busy = state.snapshot.supervisor.busy || Boolean(state.pendingJob) || Boolean(interaction.sending);
  const supervisor = state.snapshot.supervisor;
  $("add-connection").hidden = supervisor.scope === "local";
  $("send-supervisor").disabled = busy || !supervisor.available;
  $("supervisor-status").textContent = !supervisor.available ? `ARIA unavailable${supervisor.owner ? ` on ${supervisor.owner}` : ""}` : busy ? "Thinking…" : supervisor.scope === "local" ? `This host only · ${supervisor.owner}` : supervisor.owner ? `Ready on ${supervisor.owner}` : "All connections";
  $("supervisor-dot").className = `dot ${!supervisor.available ? "red" : busy ? "amber" : "green"}`;
  $("supervisor-dot").classList.toggle("activity-pulse", busy && supervisor.available && nowSeconds() - interaction.lastState < 12);
  $("provider").textContent = state.snapshot.supervisor.provider;
  $("provider").title = [state.snapshot.supervisor.model || state.snapshot.supervisor.provider, state.snapshot.supervisor.reasoning_effort].filter(Boolean).join(" · ");
  const context = agentById(state.context);
  $("selected-context").hidden = !context;
  if (context) replaceMarkup($("selected-context"), `<button id="open-context" class="context-agent" title="Open this agent">${esc(context.connection_label)} / ${esc(context.title)}</button><button id="clear-context" aria-label="Clear selected agent">×</button>`, "id");
  if (state.initialized && !state.pendingJob && latest !== state.spokenChatId) {
    const message = messages.at(-1);
    if (message?.role === "assistant") speak(message.text);
    state.spokenChatId = latest;
  }
  if (!state.initialized) state.spokenChatId = latest;
  const nodes = state.snapshot.nodes || [];
  const controllers = nodes.filter((node) => node.can_supervise !== false);
  $("supervisor-owner").closest("label").hidden = controllers.length < 2;
  $("handoff-supervisor").hidden = controllers.length < 2;
  $("node-controls").hidden = nodes.length < 2;
  if (nodes.length > 1) {
    const options = nodes.map((node) => `<option value="${esc(node.id)}">${esc(node.id)}</option>`).join("");
    if ($("node-endpoint").dataset.options !== options) {
      $("node-endpoint").innerHTML = options;
      $("node-endpoint").value = supervisor.node_id;
      $("node-endpoint").dataset.options = options;
    }
    const ownerOptions = controllers.map((node) => `<option value="${esc(node.id)}">${esc(node.id)}</option>`).join("");
    if ($("supervisor-owner").dataset.options !== ownerOptions || $("supervisor-owner").dataset.owner !== supervisor.owner) {
      $("supervisor-owner").innerHTML = ownerOptions;
      $("supervisor-owner").value = supervisor.owner;
      $("supervisor-owner").dataset.options = ownerOptions;
      $("supervisor-owner").dataset.owner = supervisor.owner;
    }
    $("handoff-supervisor").disabled = busy || !supervisor.available || $("supervisor-owner").value === supervisor.owner;
  }
  renderActivity();
  window.HubFocus?.chatChanged(forceScroll);
}
function render() {
  if (!state.snapshot) return;
  renderConnections(); renderAgents(); renderChat(); renderNotifications(); renderUsage();

  updatePath();
  $("events").innerHTML = state.snapshot.events.map((event) => `<div class="event-row"><span class="event-marker ${esc(event.kind)}"></span><span class="event-text">${esc(event.text)}</span><span class="event-time">${relativeTime(event.created)}</span></div>`).join("") || '<p class="muted">No activity yet.</p>';
  const online = state.snapshot.connections.filter((c) => c.status === "online").length;
  $("sync-state").innerHTML = `<span class="dot ${online === state.snapshot.connections.length ? "green" : "amber"}"></span>${online}/${state.snapshot.connections.length} hosts online`;
  window.HubFocus?.render();
  window.HubBackup?.changed();
  window.HubAssistant?.changed();
}
const seenNotifications = new Set();
let watchChanging = false, pushChecking = false;
let usageProvider = "codex";
try { if (localStorage.getItem("hub-usage-provider") === "claude") usageProvider = "claude"; } catch {}
function usageMarkup(connection, provider, now, heading = connection.peer_id || connection.label) {
    const providerName = provider === "claude" ? "Claude" : "Codex";
    const usage = HubUsage.readings(connection, now, provider);
    const host = heading;
    const checked = usage.observed ? `Checked ${relativeTime(usage.observed)}` : "";
    if (!usage.rows.length) {
      const authNote = usage.reason === "not_signed_in" ? "No Claude usage credentials on this host" : usage.reason === "auth_required" ? "Usage access needs renewal on this host" : usage.reason === "rate_limited" ? "Usage service busy · retrying shortly" : "";
      const note = connection.status !== "online" ? "Host offline · no usage report" : usage.status === "loading" ? "Reading usage…" : authNote || (usage.status === "not_reported" ? `Account reports no ${provider === "codex" ? "weekly" : "subscription"} limit` : connection.kind === "ssh" ? "Usage requires a hub on this host" : "Usage unavailable");
      return `<div class="weekly-limit unavailable"><div class="weekly-limit-heading"><span>${esc(host)}</span><span>—</span></div><p class="usage-note">${esc(note)}</p></div>`;
    }
    return usage.rows.map(row => `<div class="weekly-limit ${row.level}" title="${esc(checked)}"><div class="weekly-limit-heading"><span>${esc(host)}<small> · ${row.label !== providerName ? `${esc(row.label)} · ` : ""}${row.period}</small></span><strong>${esc(row.value)}</strong></div>${row.expired ? "" : `<meter min="0" max="100" value="${row.remaining}" aria-label="${esc(host)} ${esc(row.label)} ${row.period} limit remaining">${row.remaining}%</meter>`}<p class="usage-note">${row.expired ? "Reset time passed; waiting for a new report" : esc(HubUsage.resetTime(row.reset))}${usage.note ? `<span> · ${esc(usage.note)}${checked ? ` · ${esc(checked.toLowerCase())}` : ""}</span>` : ""}</p></div>`).join("");
}
function renderUsage() {
  const providerName = usageProvider === "claude" ? "Claude" : "Codex";
  document.querySelectorAll("[data-usage-provider]").forEach(button => button.setAttribute("aria-pressed", String(button.dataset.usageProvider === usageProvider)));
  $("weekly-limits").setAttribute("aria-label", `${providerName} usage limits per host account`);
  const markup = state.snapshot.connections.map(connection => usageMarkup(connection, usageProvider, state.snapshot.server_time)).join("");
  replaceMarkup($("weekly-limits"), markup, "data-usage-host");
}
function renderPushStatus() {
  if(!state.snapshot?.watch)return;
  const watch=state.snapshot.watch;
  const push=HubPush.status(state.snapshot,{connected:nowSeconds()-interaction.lastState<12,elapsed:Math.max(0,nowSeconds()-interaction.lastState)});
  $('notification-help').textContent=push.help;
  $('push-status-label').textContent=push.label;
  $('push-status-label').dataset.status=push.state;
  $('push-indicator').dataset.status=push.state;
  const pushDescription=`${push.label}. ${push.help}`;
  $('push-indicator').title=pushDescription;
  $('push-indicator').setAttribute('aria-label',pushDescription);
  $('push-indicator').querySelector('use').setAttribute('href',push.state==='paused'||push.state==='off'?'#i-bell-off':'#i-bell');
  $('push-outcome').textContent=push.outcome;
  $('push-outcome').hidden=!push.outcome;
  $('push-accepted').hidden=!push.accepted;
  $('push-accepted').textContent=push.accepted ? `Last push accepted ${new Date(push.accepted*1000).toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',hourCycle:'h23'})}.` : '';
  $('check-push').disabled=!watch.push_available || pushChecking;
  $('check-push').setAttribute('aria-busy',String(pushChecking));
}
function renderNotifications() {
  const watch = state.snapshot.watch;
  if (!watch) { $("watch-label").textContent = "Watching unavailable"; return; }
  const fresh = watch.checked_at && state.snapshot.server_time - watch.checked_at < 20;
  $("watch-label").textContent = !watch.eligible ? "Watching on controller" : !watch.enabled ? "Watching paused" : watch.error ? "Watch reconnecting…" : fresh ? "Watching all connections" : "Starting watch…";
  $("watch-dot").className = `dot ${watch.error ? "red" : watch.enabled && fresh ? "green" : "amber"}`;
  $("notification-count").textContent = watch.unhandled ? `(${watch.unhandled})` : "";
  $("mobile-update-count").textContent = watch.unhandled || "";
  $("notification-button").setAttribute("aria-label", `Updates, ${watch.unhandled || 0} pending`);
  $("toggle-watch").textContent = watch.enabled ? "Pause watching" : "Resume watching";
  $("toggle-watch").disabled = !watch.eligible || watchChanging;
  $("enable-push").disabled = !watch.push_available;
  $("notification-help").classList.toggle("watch-paused", watch.eligible && !watch.enabled);
  renderPushStatus();
  $("read-notifications").disabled = !watch.unread;
  const items = linkedNotice && !(watch.items || []).some(item=>item.id===linkedNotice.id)?[...(watch.items || []),linkedNotice]:(watch.items || []);
  if (toastNotices.some(id => items.some(item => item.id === id && item.handled_at))) {
    $('toast').hidden = true; clearTimeout(toastTimer); toastNotices = [];
  }
  const visible = items.filter(item => updateFilter === "all" || (updateFilter === "handled" ? item.handled_at : !item.handled_at));
  $("updates-pending-count").textContent = watch.unhandled ?? items.filter(item => !item.handled_at).length;
  $("updates-handled-count").textContent = watch.handled ?? items.filter(item => item.handled_at).length;
  document.querySelectorAll("[data-update-filter]").forEach(button => {
    button.classList.toggle("active", button.dataset.updateFilter === updateFilter);
    button.setAttribute("aria-pressed", String(button.dataset.updateFilter === updateFilter));
  });
  $("handle-shown-updates").hidden = updateFilter === "handled";
  $("handle-shown-updates").disabled = !visible.some(item => !item.handled_at);
  const total = updateFilter === "pending" ? watch.unhandled : updateFilter === "handled" ? watch.handled : (watch.unhandled || 0) + (watch.handled || 0);
  $("updates-showing").textContent = total > visible.length ? `Showing the latest ${visible.length} of ${total} ${updateFilter === "all" ? "" : updateFilter + " "}updates.` : "";
  replaceMarkup($("notifications"), visible.map(item => `<article class="notification-entry${item.handled_at ? " is-handled" : item.read_at ? "" : " unread"}" data-update-id="${esc(item.id)}"><h3>${updateSource(item)}</h3>${item.handled_at ? `<details><summary>View update</summary>${updateBody(item)}</details>` : updateBody(item)}<time>${relativeTime(item.created)}</time>${updateActions(item)}</article>`).join("") || `<p class="muted">${updateFilter === "pending" ? "No pending updates." : updateFilter === "handled" ? "No handled updates yet." : "No updates yet."}</p>`, "data-update-id");
  window.HubMarkdown?.enhance($("notifications"));
  const freshItems = (watch.items || []).filter(item => item.interrupt!==false && !item.handled_at && !item.read_at && !seenNotifications.has(item.id));
  if (state.initialized && freshItems.length) {
    const freshIds=new Set(freshItems.map(item=>item.id));
    $('notifications').querySelectorAll('[data-update-id]').forEach(node=>{if(freshIds.has(node.dataset.updateId))node.classList.add('notice-arrive');});
    const first=freshItems[0],singleConversation=first.agent_id && freshItems.every(item=>item.agent_id===first.agent_id);
    const notice=freshItems.length===1 || singleConversation?first:null;
    toast(freshItems.length === 1 ? first.title : `${freshItems.length} new agent updates`,false,{
      whole:true,label:notice?.agent_id?'Open conversation':'Open updates',notices:freshItems.map(item=>item.id),
      run:()=>notice?openUpdate(notice.id,notice.agent_id):openBuffer('notifications'),
    });
    speak(freshItems.slice(0, 3).map(item => item.title + ". " + item.body).join(" "));
  }
  for (const item of watch.items || []) if(item.interrupt!==false || item.read_at || item.handled_at)seenNotifications.add(item.id);
}
function latestResponse(text) { return AgentReader.parse(text || "").findLast((block) => block.type === "assistant")?.text || ""; }
const normalizedInput = (value) => value.replace(/\s+/g, " ").trim();
function matchingInputs(text, message) { return AgentReader.parse(text || "").filter((block) => block.type === "input" && normalizedInput(block.text) === normalizedInput(message)).length; }
function responseAfter(text, receipt) {
  const blocks = AgentReader.parse(text || ""), last = blocks.findLastIndex((block) => block.type === "assistant");
  if (last < 0) return false;
  const input = blocks.findLastIndex((block) => block.type === "input" && normalizedInput(block.text) === normalizedInput(receipt.text));
  const changed=typeof receipt.baseline==='string'?blocks[last].text!==receipt.baseline:receipt.baselineDigest && HubReading.digest({text:blocks[last].text})!==receipt.baselineDigest;
  return Boolean(changed || (input >= 0 && last > input && matchingInputs(text, receipt.text) > receipt.baselineInputs));
}
function observeAgents() {
  const now = nowSeconds();
  for (const agent of state.snapshot.agents) {
    if (detail.observation?.agent_id === agent.id && detail.observation.observed_at > (agent.observed_at || 0)) Object.assign(agent, detail.observation);
    const observation = interaction.observations.get(agent.id) || { changed: now, started: now };
    // Ignore CLI spinners and duration footers: only visible content is activity.
    const signature = agent.activity_fingerprint || JSON.stringify(AgentReader.parse(agent.preview || "").map((block) => [block.type, block.text]));
    if (signature !== observation.signature) observation.changed = now;
    if (agent.state === "running" && observation.state !== "running") observation.started = Math.min(now, now + (agent.changed_at || state.snapshot.server_time) - state.snapshot.server_time);
    if (Number.isFinite(agent.activity_elapsed)) observation.started = now - agent.activity_elapsed;
    Object.assign(observation, { signature, state: agent.state });
    interaction.observations.set(agent.id, observation);
    const receipt = interaction.receipts.get(agent.id);
    if (receipt && agent.state === "running") receipt.wasRunning = true;
  }
  const job = HubRouting.withReply(state.snapshot.supervisor.job,state.snapshot.messages);
  if (job) {
    state.snapshot.supervisor.job = job;
    interaction.job = job;
    if (job.status === "running" && !state.pendingJob) {
      const supervisor = state.snapshot.supervisor;
      state.pendingJob = supervisor.owner && supervisor.owner !== supervisor.node_id ? `${supervisor.owner}/${job.id}` : job.id;
    }
    if ((typeof job.reply==='string' || typeof job.error==='string') && job.status !== "running" && state.pendingJob?.split("/").at(-1) === job.id) state.pendingJob = null;
  }
  if (state.snapshot.supervisor.busy && !interaction.busySince) interaction.busySince = now;
  else if (!state.snapshot.supervisor.busy && !state.pendingJob) interaction.busySince = 0;
}
function paintActivity(element, value) {
  element.hidden = !value;
  if (!value) return;
  if (!element.childElementCount) element.innerHTML = '<span class="feedback-dots" aria-hidden="true"><i></i><i></i><i></i></span><div class="feedback-copy"><div><span class="feedback-label"></span><span class="feedback-elapsed" aria-hidden="true"></span></div><p class="feedback-note"></p></div>';
  element.dataset.kind = value.kind;
  const label = element.querySelector(".feedback-label"), note = element.querySelector(".feedback-note");
  if (label.textContent !== value.title) label.textContent = value.title;
  if (note.textContent !== value.note) note.textContent = value.note;
  note.hidden = !value.note;
  element.querySelector(".feedback-elapsed").textContent = value.since ? HubActivity.elapsed(nowSeconds() - value.since) : "";
}
function renderAgentConversation() {
  const candidate = interaction.receipts.get(state.selected), receipt = candidate?.dismissed ? null : candidate, sent = $("agent-sent"), output = $("agent-output"), previous = $("agent-previous-output");
  if (receipt) {
    if (sent.dataset.request !== receipt.request_id) { sent.dataset.request = receipt.request_id; sent.innerHTML = '<div class="agent-message-bubble message-arrive"></div>'; }
    const node = sent.firstElementChild;
    const markup = `<span class="message-role">You <time>${esc(new Date(receipt.created * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }))}</time></span>${window.HubImages?.html(receipt.attachments)||''}<div class="message-text">${esc(HubMessageContent.displayText(receipt))}</div>${deliveryMarkup(receipt, "agent")}`;
    if (node._markup !== markup) { node.innerHTML = markup; node._markup = markup; }
  } else { sent.querySelector('.agent-message-bubble')?.remove(); delete sent.dataset.request; }
  const confirmed = receipt && (detail.messages || []).some(message => message.role === "user" &&
    message.text.trim() === receipt.text.trim() && HubQueue.sameImages(receipt.attachments,message.attachments) && Date.parse(message.created) / 1000 >= receipt.created - 5);
  if (confirmed && !receipt.confirmed) {receipt.confirmed = true; if(receipt.request)rememberDelivery(receipt);}
  const queued=interaction.queues.get(state.selected) || [];
  const captured=nativeQueue.texts(AgentReader.queued(detail.text || ''));
  for(const entry of HubQueue.confirmations(queued,captured,detail.messages,nativeQueue.items())){
    entry.confirmed=true;entry.needsCheck=false;entry.error='';
    if(entry.status==='sending'||entry.status==='uncertain')entry.status='queued';
    if(entry.request)rememberDelivery(entry);
  }
  const pending=queued.filter(entry=>!entry.confirmed&&!entry.dismissed);
  if(receipt?.confirmed && sent.firstElementChild)sent.firstElementChild.hidden=true;
  else if(sent.firstElementChild)sent.firstElementChild.hidden=false;
  let queueNode=sent.querySelector('.pending-queue');
  if(pending.length){
    if(!queueNode){queueNode=document.createElement('div');queueNode.className='pending-queue';sent.append(queueNode);}
    const markup=pending.map(entry=>`<div class="pending-queue-row"><span class="queue-arrow" aria-hidden="true"><svg class="icon"><use href="#i-queued"/></svg></span><div>${window.HubImages?.html(entry.attachments)||''}<div class="message-text">${esc(HubMessageContent.displayText(entry))}</div>${deliveryMarkup(entry,'queue')}</div></div>`).join('');
    if(queueNode._markup!==markup){queueNode.innerHTML=markup;queueNode._markup=markup;}
  }else queueNode?.remove();
  sent.hidden = (!receipt || Boolean(receipt.confirmed)) && !pending.length;
  previous.hidden = true;
  const reader = $("agent-reader");
  // Keep the conversation chronological while an instruction is in flight.
  // Reparent only when needed so refreshes do not disturb selection or video.
  if (output.parentElement !== reader || output.nextElementSibling !== sent) reader.insertBefore(output, sent);
  if (sent.nextElementSibling !== $("agent-activity")) reader.insertBefore($("agent-activity"), sent.nextSibling);

}
function renderActivity() {
  if (!state.snapshot) return;
  renderPushStatus();
  const now = nowSeconds(), connected = now - interaction.lastState < 12, supervisor = state.snapshot.supervisor;
  const sending = interaction.sending?.status === "sending" && !interaction.sending.confirmed ? interaction.sending : null;
  const job = interaction.job?.status === "running" || state.pendingJob ? interaction.job : null;
  const supervisorActivity = HubActivity.supervisor({ sending, job, busy: supervisor.busy || Boolean(state.pendingJob), connected,
    available: supervisor.available, since: interaction.busySince, now });
  paintActivity($("supervisor-activity"), supervisorActivity);
  if (supervisorActivity) {
    $("supervisor-status").textContent = supervisorActivity.title;
    $("supervisor-dot").className = `dot ${supervisorActivity.kind === "offline" ? "red" : "amber"}${supervisorActivity.kind === "active" ? " activity-pulse" : ""}`;
  }
  if (state.view !== "agent") {window.HubFocus?.decorate();return;}
  const agent = agentById(state.selected), observation = interaction.observations.get(state.selected);
  const connection = state.snapshot.connections.find((entry) => entry.id === agent?.connection_id);
  const freshHost = connection && state.snapshot.server_time - connection.last_seen + now - interaction.lastState < 30;
  nativeApprovals.setConnected(Boolean(connected && freshHost && !observation?.previewError));
  const activity = HubActivity.agent({ agent, receipt: interaction.receipts.get(state.selected), observation,
    connected: connected && freshHost && !observation?.previewError, now });
  paintActivity($("agent-activity"), activity.kind === "idle" ? null : activity);
  if (["offline", "quiet", "attention"].includes(activity.kind)) $("detail-state").textContent = activity.title;
  window.HubFocus?.decorate();
}
function receiveSnapshot(snapshot) {
  if(state.snapshot?.server_time>snapshot.server_time)return;
  const previous=state.snapshot;
  state.snapshot=snapshot;
  interaction.lastState=nowSeconds();
  observeAgents();
  $('server-error').hidden=true;
  render();
  if(previous && state.voice && !snapshot.watch){
    const changed=snapshot.agents.filter(a=>a.needs_attention && !a.acknowledged && previous.agents.some(old=>old.id===a.id && (old.state!==a.state || !old.connection_online)));
    if(changed.length)speak(changed.map(a=>`${a.title} on ${a.connection_label}: ${a.summary}.`).join(' '));
  }
  state.initialized=true;
  reconcileDeliveries(previous?.csrf_token!==snapshot.csrf_token);
  if(state.view==='agent')updateDetailMeta();
  liveClient?.sync();
  window.HubStartup?.check();
}
async function poll(force = false) {
  if (state.polling) {
    if (!force) return state.polling;
    await state.polling;
  }
  if (state.refreshing && !force) return;
  state.polling = (async () => {
    try {
      let snapshot = force ? await api("/api/state") : await liveApi("/api/state");
      // Also renew the CSRF token after a service restart before requesting a scan.
      if (force) {state.snapshot=snapshot;snapshot=await api('/api/refresh', {});}
      receiveSnapshot(snapshot);
      if(force)reconcileDeliveries(true);
      return true;
    } catch (error) {
      $("server-error").textContent = `Connection to the hub interrupted. ${error.message}`;
      $("server-error").hidden = false;
      $("sync-state").innerHTML = '<span class="dot red"></span> Disconnected';
      interaction.lastState = 0;
      renderActivity();
      window.HubStartup?.waiting('Couldn’t reach the hub. Retrying…');
      return false;
    } finally { state.polling = false; }
  })();
  return state.polling;
}
async function refreshWorkspace() {
  if (state.refreshing) return;
  state.refreshing = true;
  const button = $('refresh-workspace');
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  button.setAttribute('aria-label', 'Refreshing…');
  try {
    const synced = await poll(true);
    const updateReady = synced && appVersion && state.snapshot?.app_version && appVersion !== state.snapshot.app_version;
    if(updateReady && !window.HubFocus?.voiceActive() && !interaction.sending && !detail.sending.size){
      reloadWorkspace();
      return;
    }
    const output = state.view === 'agent' ? await detailPoll(true) : true;
    const errors = state.snapshot?.refresh_errors || [];
    const offline = (state.snapshot?.connections || []).filter(host => host.status !== 'online').map(host => host.label);
    if (!synced || output === false) toast('Couldn’t refresh everything. Try again.', true);
    else if (errors.length) toast(`Updated available hosts. ${errors.join(' ')}`, true);
    else if (offline.length) toast(`Updated available hosts. Unavailable: ${offline.join(', ')}.`, true);
    else if(updateReady)toast('App update ready',false,{label:'Reload',run:reloadWorkspace});
    else toast('Up to date');
  } catch(error) {
    toast(`Couldn’t refresh. ${error.message}`,true);
  } finally {
    state.refreshing = false;
    liveClient?.reconnect();
    button.disabled = false;
    button.removeAttribute('aria-busy');
    button.setAttribute('aria-label', 'Refresh');
  }
}
function reloadWorkspace(){
  if(state.selected)detail.drafts.set(state.selected,$('agent-message').value);
  window.HubFocus?.persistDrafts();
  window.HubFocus?.saveReading();
  location.reload();
}
$('refresh-workspace').onclick = refreshWorkspace;
function updateDetailMeta() {
  const agent = agentById(state.selected);
  if (!agent) { $("detail-state").textContent = "Exited"; $("detail-meta").textContent = "This agent is no longer in the workspace."; stopAgent.hidden=true; ["send-agent", "pause-agent", "focus-agent", "acknowledge-agent", "copy-attach", "rename-agent"].forEach((id) => { $(id).disabled = true; }); return; }
  $("rename-agent").disabled = false;
  $("detail-title").textContent = agentTitle(agent);
  $("detail-title").title = agent.title;
  $("detail-connection").textContent = agent.connection_label;
  $("detail-connection").dataset.connection = agent.connection_id;
  $("detail-connection").title = `Show agents on ${agent.connection_label}`;
  $("detail-connection").setAttribute("aria-label", `Host ${agent.connection_label}; show its agents`);
  $("send-agent").title = `Send to ${agent.title} on ${agent.connection_label}`;
  $("detail-state").textContent = !agent.connection_online ? "Offline" : !agent.live ? "Exited" : agent.state === "running" && agent.activity_confirmed ? `${agent.activity_phase || "Working"}${Number.isFinite(agent.activity_elapsed) ? " · " + HubActivity.elapsed(agent.activity_elapsed) : ""}` : labels[agent.state] || agent.state;
  $("detail-state").className = `state-badge ${!agent.connection_online ? "blocked" : agent.state}`;
  $("detail-meta").innerHTML = `<span>${esc(agent.runtime)}</span><span title="${esc(agent.cwd)}">${esc(shortPath(agent.cwd))}</span>`;
  $("focus-agent").disabled = $("copy-attach").disabled = false;
  const canSend = agent.connection_online && agent.live && !["running", "waiting"].includes(agent.state);
  const receipt = interaction.receipts.get(agent.id);
  const awaitingStart = receipt?.status === "sent" && !receipt.responseSeen && !receipt.wasRunning && nowSeconds() - receipt.created < 30;
  const queueAvailable=agent.runtime==='codex' && agent.state==='running' && agent.connection_online && agent.live && HubRouting.supervisorRequest($('agent-message').value)===null;
  const hasDraft=Boolean($('agent-message').value.trim()||window.HubImages?.has($('agent-message')));
  $('queue-agent').hidden=!queueAvailable || !hasDraft;
  $('queue-agent').disabled=!queueAvailable || (!$("agent-message").value.trim()&&!window.HubImages?.has($('agent-message'))) || detail.sending.has(agent.id);
  stopAgent.hidden=agent.state!=='running'||HubRouting.supervisorRequest($('agent-message').value)!==null;
  stopAgent.disabled=!agent.connection_online||!agent.live||stopAgent.dataset.pending==='true';
  $('send-agent').hidden=!stopAgent.hidden;
  $("send-agent").disabled = !canSend || detail.sending.has(agent.id) || awaitingStart;
  const retrying = agent.recovery && !agent.activity_confirmed && ['retrying','checking','waiting'].includes(agent.recovery.status);
  $("pause-agent").disabled = !agent.connection_online || !agent.live || (agent.state !== "running" && !retrying);
  const pauseLabel = retrying ? 'Pause retries' : 'Request pause';
  if ($('pause-agent').dataset.label !== pauseLabel) { $('pause-agent').textContent = pauseLabel; $('pause-agent').dataset.label = pauseLabel; }
  $("acknowledge-agent").disabled = !agent.needs_attention || agent.acknowledged;
  $("focus-agent").textContent = agent.mark.focused ? "Unfocus" : "Focus";
  // Normal work already has an activity indicator. A second busy hint makes
  // the composer jump as terminal observations change between tools and replies.
  const hint = $("agent-message-hint");
  const help = !agent.connection_online ? "Host offline. Your draft will stay here." : !agent.live ? "Agent exited. The terminal still has its last output." : agent.state === "waiting" ? (nativeApprovals.hasItems()?'Review the request above.':'Needs a decision in tmux · Actions → Copy tmux command.') : "";
  hint.classList.toggle("needs-help", Boolean(help));
  const hintText = help || (agent.state === "running" ? "" : "Enter to send · Shift Enter for a new line");
  if (hint.textContent !== hintText) hint.textContent = hintText;
  const position = detail.order.indexOf(agent.id);
  $("agent-position").textContent = position >= 0 ? `${position + 1} / ${detail.order.length}` : "";
  $("previous-agent").disabled = position <= 0;
  $("next-agent").disabled = position < 0 || position >= detail.order.length - 1;
  renderActivity();
}
function setOutputMode(mode) {
  const changed=detail.mode!==mode;
  detail.mode = mode;
  $("agent-reader").hidden = mode !== "reader";
  $("terminal").hidden = mode !== "raw";
  $("terminal-view").hidden = mode !== "raw";
  const toggle=$('focus-raw');if(toggle)toggle.lastChild.textContent=mode==='raw'?'View conversation':'View terminal';
  document.querySelectorAll("[data-output-mode]").forEach((button) => { const selected = button.dataset.outputMode === mode; button.setAttribute("aria-selected", String(selected)); button.tabIndex = selected ? 0 : -1; });
  if (mode === "raw") {terminalView.show();$("terminal").scrollTop = $("terminal").scrollHeight;}
  renderOutputNavigation();
  if(changed)queueMicrotask(()=>{liveClient?.sync();detailPoll(true);});
}
function renderOutputNavigation(){
  const button=$('new-output'),past=detail.mode==='reader'&&$('agent-output').dataset.historyPast==='true';
  button.hidden=!(past||detail.pending||detail.unreadOutput);
  button.firstChild.textContent=past?'Latest ':'New output ';
}
function terminalCapture(result){return result.terminal || {ansi:result.text || 'No terminal output yet.',theme:'dark'};}
function previewSignature(result) {
  return JSON.stringify([detail.history,result.history_source,agentById(state.selected)?.state==='waiting'&&!nativeApprovals.hasItems(),nativeQueue.suppressTerminal(), result.session_verified || result.messages?.length ? ["original", result.messages, result.session_verified || nativeQueue.suppressTerminal()?null:AgentReader.queued(result.text || '')] : ["capture", AgentReader.parse(result.text || "").map(block => [block.type, block.text, block.duration || ''])]]);
}
function applyPreview(result, jump = false) {
  if (typeof result === "string") result = { text: result };
  result={...result,messages:HubMessageContent.messages(result.messages)};
  const text = result.text;
  const reader = $("agent-reader"), terminal = $("terminal");
  const signature = previewSignature(result), changed = detail.signature !== signature;
  const holdScroll = detail.text !== null && isReadingOutput();
  const open = new Map([...reader.querySelectorAll("details[data-reader-key]")].map((node) => [node.dataset.readerKey, node.open]));
  const scroll = reader.scrollTop, readerAtBottom = reader.scrollHeight - reader.scrollTop - reader.clientHeight < 80;
  const follow = jump || (readerAtBottom && !holdScroll);
  const previousReply=detail.messages?.findLast(message=>message.role==='assistant'&&message.phase!=='commentary');
  const top = reader.getBoundingClientRect().top;
  const focusedAnchor=reader.contains(document.activeElement)?document.activeElement.closest('.reader-entry, details[data-reader-key]'):null;
  const anchor = focusedAnchor || [...reader.querySelectorAll('.reader-entry, details[data-reader-key]')].find(node => node.getBoundingClientRect().bottom > top);
  const anchorTop = anchor?.getBoundingClientRect().top;
  const rawBottom = terminal.scrollHeight - terminal.scrollTop - terminal.clientHeight < 60;
  if (changed) {
    AgentReader.update($("agent-output"), result.session_verified ? '' : text, result.messages, detail.history, nativeQueue.suppressTerminal(),{follow,anchor:anchor?.dataset.messageId||anchor?.dataset.readerKey,source:result.history_source,decision:agentById(state.selected)?.state==='waiting'&&!nativeApprovals.hasItems()});
    $("agent-output").dataset.agentId = state.selected;
    window.HubMarkdown?.enhance($("agent-output"));
  }
  detail.messages = result.messages || [];
  detail.facts = result.facts;
  $("history-note").hidden = true;
  $("history-note").textContent = result.history_note || "Available terminal history. Older output may have left the tmux scrollback.";
  $("output-source").hidden = true;
  reader.querySelectorAll("details[data-reader-key]").forEach((node) => { if (open.has(node.dataset.readerKey)) node.open = open.get(node.dataset.readerKey); });
  reader.scrollTop = follow ? reader.scrollHeight : scroll;
  if (!follow && anchor?.isConnected) reader.scrollTop += anchor.getBoundingClientRect().top - anchorTop;
  terminalView.update(terminalCapture(result));
  if (jump || rawBottom) terminal.scrollTop = terminal.scrollHeight;
  detail.text = text; detail.signature = signature; detail.pending = null;
  renderAgentConversation();
  if (jump) reader.scrollTop = reader.scrollHeight;
  const reply=result.messages?.at(-1),composerFocused=$('focus-composers')?.contains(document.activeElement);
  if(phoneLayout.matches && !jump && follow && !composerFocused && reply?.role==='assistant' && reply.phase!=='commentary' &&
      reply.text.length>=900 && reply.id!==previousReply?.id){
    const node=[...$('agent-output').querySelectorAll('[data-message-id]')].find(node=>node.dataset.messageId==='assistant:'+reply.id);
    if(node)reader.scrollTop+=node.getBoundingClientRect().top-reader.getBoundingClientRect().top-8;
  }
  detail.unreadOutput = !follow && (changed || detail.unreadOutput);
  renderOutputNavigation();
  window.HubFocus?.preview();
}
function isReadingOutput() {
  const container = detail.mode === "reader" ? $("agent-reader") : $("terminal");
  if(detail.mode==='reader'&&$('agent-output').dataset.historyPast==='true')return true;
  if (window.HubMarkdown?.isPreviewing?.(container)) return true;
  const selection = window.getSelection();
  if (selection && !selection.isCollapsed && (container.contains(selection.anchorNode) || container.contains(selection.focusNode))) return true;
  if (container.contains(document.activeElement) && document.activeElement !== container) return true;
  return detail.mode === "reader" ? [...container.querySelectorAll("video")].some(video => !video.paused) || container.scrollHeight - container.scrollTop - container.clientHeight > 80 : container.scrollHeight - container.scrollTop - container.clientHeight > 60;
}
function receivePreview(selected,result,force=false) {
  if(state.selected!==selected || state.view!=='agent')return;
  result={...result,messages:HubMessageContent.messages(result.messages)};
  if(result.observation?.observed_at && detail.observation?.agent_id===selected && result.observation.observed_at<detail.observation.observed_at)return false;
  result=detail.preview.update(result);
  const queueReader=$('agent-reader'),queueFollow=queueReader.scrollHeight-queueReader.scrollTop-queueReader.clientHeight<80&&!isReadingOutput();
  nativeQueue.update(selected,HubQueue.presentation(result.queue,AgentReader.queued(result.text||''),interaction.queues.get(selected)||[]));
  nativeApprovals.update(selected,result.approvals);
  if(queueFollow)queueReader.scrollTop=queueReader.scrollHeight;
  const agent=agentById(selected);
  if(agent && result.observation && (!result.observation.observed_at || result.observation.observed_at>=(agent.observed_at || 0))){
    detail.observation={agent_id:selected,...result.observation};Object.assign(agent,result.observation);observeAgents();
  }
  $('preview-error').hidden=true;
  const observation=interaction.observations.get(selected);if(observation)observation.previewError=false;
  const receipt=interaction.receipts.get(selected);if(receipt && responseAfter(result.text,receipt))receipt.responseSeen=true;
  if(force)applyPreview(result,detail.text===null);
  else if(detail.signature===previewSignature(result)){
    detail.text=result.text;
    detail.facts=result.facts;
    if(detail.mode==='raw' && isReadingOutput() && !terminalView.matches(terminalCapture(result))){detail.pending=result;renderOutputNavigation();}
    else{
      const terminal=$('terminal'),atBottom=terminal.scrollHeight-terminal.scrollTop-terminal.clientHeight<60;
      terminalView.update(terminalCapture(result));if(atBottom)terminal.scrollTop=terminal.scrollHeight;
      detail.pending=null;renderOutputNavigation();
    }
  }else if(detail.mode==='raw' && detail.text!==null && isReadingOutput()){detail.pending=result;renderOutputNavigation();}
  else applyPreview(result,detail.text===null);
  renderAgentConversation();renderActivity();updateDetailMeta();
  $('history-note').hidden=!result.conversation_pending;
  $('history-note').textContent=result.conversation_pending?'Syncing conversation…':'';
  window.HubStartup?.check();
  return !result.conversation_pending;
}
async function detailPoll(force = false) {
  if (state.detailPolling) {
    if (!force) return state.detailPolling;
    await state.detailPolling;
  }
  if (state.refreshing && !force) return;
  if (state.view !== "agent" || !state.selected) return;
  if (!force && phoneLayout.matches && document.querySelector(".app-shell").dataset.mobileView !== "workspace") return;
  const selected = state.selected, generation = detail.generation, mode = detail.mode;
  state.detailPolling = (async () => {
    try {
      const path = `/api/preview?agent_id=${encodeURIComponent(selected)}&history=${detail.history ? 1 : 0}${mode==='raw'?'&terminal=1':''}`;
      const result = force ? await api(path+'&fresh=1') : await liveApi(path);
      if (state.selected === selected && detail.generation === generation && detail.mode === mode && state.view === "agent") {
        return receivePreview(selected,result,force);
      }
      return true;
    } catch (error) {
      if (state.selected === selected && detail.generation === generation && detail.mode === mode && state.view === "agent") { $("preview-error").textContent = `Couldn’t refresh output. ${error.message}`; $("preview-error").hidden = false; const observation = interaction.observations.get(selected); if (observation) observation.previewError = true; renderActivity(); window.HubStartup?.check(); }
      return false;
    } finally { state.detailPolling = false; if (detail.generation !== generation && state.view === "agent") detailPoll(); }
  })();
  return state.detailPolling;
}
function openAgent(id) {
  if(state.selected && state.selected!==id)state.previous={agent:state.selected};
  else if(state.view==='home')state.previous={supervisor:true};
  window.HubFocus?.saveReading();
  detail.unreadOutput = false;
  detail.history = true; detail.messages = []; detail.facts = null;
  if (state.view !== "agent") detail.order = [...document.querySelectorAll("[data-agent]")].map((row) => row.dataset.agent);
  if (state.selected) detail.drafts.set(state.selected, $("agent-message").value);
  state.selected = state.cursor = state.context = id;
  nativeQueue.select(id);
  nativeApprovals.select(id);
  AgentReader.reset($('agent-output'));
  detail.preview.reset();
  $('history-note').hidden=true;
  detail.generation++; detail.text = detail.pending = detail.signature = null;
  openBuffer("agent"); updateDetailMeta();
  const origin = bufferTrail.at(-1);
  $("agent-view").querySelector("[data-back]").innerHTML = `<svg class="icon"><use href="#i-back"/></svg>${origin?.view === "notifications" ? "Updates" : "Agents"}`;
  $("agent-view").querySelector("[data-back]").setAttribute("aria-label", origin?.view === "notifications" ? "Back to updates" : "Back to agents");
  $("agent-view").classList.remove("tools-open");
  $("agent-tools-toggle").setAttribute("aria-expanded", "false");
  terminalView.update("Loading…");
  $("agent-output").innerHTML = '<div class="reader-loading" role="status"><span class="loading-dots" aria-hidden="true"><i></i><i></i><i></i></span><span>Loading conversation…</span></div>';
  $("agent-previous-output").open = false;
  renderAgentConversation();
  $("agent-reader").scrollTop = $("agent-reader").scrollHeight;
  $("new-output").hidden = $("preview-error").hidden = true;
  $("agent-message").value = detail.drafts.get(id) || ""; sizeAgentInput();
  window.HubFocus?.opened(id);
  renderChat(); detailPoll();
  $("agent-reader").focus({ preventScroll: true });
}
async function doAction(value, button) {
  if (button) button.disabled = true;
  try { const result = await api("/api/action", value); toast(result.message); await poll(); return true; }
  catch (error) { toast(error.message, true); return false; }
  finally { if (button) button.disabled = false; updateDetailMeta(); }
}
async function submitSupervisor(message, input = $("supervisor-input"), voice = false) {
  const original = message;
  const imageBatch=window.HubImages?.capture(voice?null:input);
  message = HubRouting.supervisorRequest(message) ?? message;
  if(!message.trim()&&imageBatch?.items.length)message='Please look at the attached images.';
  if (!message.trim()) return false;
  if(window.HubFocus?.endCall(message)){
    if(input?.value===original){input.value='';if(input.id==='agent-message' && state.selected)detail.drafts.delete(state.selected);}
    sizeAgentInput();sizeSupervisorInput();window.HubFocus?.persistDrafts();
    return true;
  }
  if (!state.snapshot?.supervisor.available || state.snapshot.supervisor.busy || state.pendingJob || interaction.sending) {
    toast(state.snapshot?.supervisor.available ? "ARIA is finishing your previous request." : "ARIA is unavailable.", true);
    return false;
  }
  const entry = { kind:'supervisor', owner:state.snapshot.supervisor.owner || '', context_agent_id: state.context || "", request_id: requestId(), role: "user", text: message, created: nowSeconds(), status: "sending", confirmed: false,
    afterKey: $("chat").lastElementChild?.dataset.messageKey || null };
  entry.attachments=imageBatch?.items.length?HubImages.metadata(imageBatch):[];
  const selected = state.context || "", allowActions = $("allow-actions").checked;
  const clarificationId = window.HubFocus?.clarificationId() || "";
  entry.request={message,selected_agent_id:selected,allow_actions:allowActions,request_id:entry.request_id,clarification_id:clarificationId,...(voice?{voice:true}:{})};
  if(!imageBatch?.items.length)rememberDelivery(entry);
  interaction.outbox.push(entry); interaction.sending = entry; interaction.job = null; interaction.busySince = entry.created;
  window.HubFocus?.supervisorStarted(entry);
  if (input?.value === original) {
    input.value = "";
    if (input.id === "agent-message" && state.selected) detail.drafts.delete(state.selected);
  }
  sizeAgentInput();
  sizeSupervisorInput();
  window.HubFocus?.persistDrafts();
  renderChat(true);
  try {
    if(imageBatch?.items.length){
      entry.attachments=await HubImages.upload(imageBatch,'supervisor');
      entry.request.attachments=entry.attachments.map(image=>image.id);
      rememberDelivery(entry);renderChat();
    }
    const result = await api("/api/supervisor", entry.request);
    if(imageBatch?.items.length)HubImages.clear(imageBatch);
    state.pendingJob = result.job_id;
    entry.job_id=result.job_id;
    entry.status = "sent";
    renderChat();
    await poll();
    pollJob();
  } catch (error) {
    if (!entry.confirmed) { entry.status = error.status && error.status < 500 ? "failed" : "uncertain"; entry.error = error.message + (entry.status === "uncertain" ? " Check the conversation before resending." : ""); }
  } finally { entry.needsCheck=entry.status==='uncertain';rememberDelivery(entry);interaction.sending = null; renderChat(); }
  return true;
}
async function pollJob() {
  if (!state.pendingJob || interaction.jobPolling) return;
  const id = state.pendingJob;
  interaction.jobPolling = true;
  const started = performance.now();
  try {
    const job = await api(`/api/jobs/${id.split("/").map(encodeURIComponent).join("/")}?wait=1`);
    if (state.pendingJob !== id) return;
    interaction.job = job; interaction.lastJob = nowSeconds(); interaction.jobError = false;
    window.HubFocus?.supervisorProgress(job);
    if (job.status !== "running") {
      state.pendingJob = null;
      window.HubFocus?.supervisorFinished(job);
      await poll();
      if (job.status === "failed" && !state.snapshot.messages.some((message) => message.role === "error" && message.text === job.error)) toast(job.error, true);
    }
    renderChat();
  } catch {
    // Keep tracking the accepted job after a network failure; never resubmit it.
    interaction.jobError = true; renderActivity();
  } finally {
    interaction.jobPolling = false;
    if(state.pendingJob===id && !interaction.jobError)setTimeout(pollJob,performance.now()-started<500?1500:0);
  }
}

document.addEventListener("click", async (event) => {
  const provider = event.target.closest("[data-usage-provider]");
  if (provider) {
    usageProvider = provider.dataset.usageProvider;
    try { localStorage.setItem("hub-usage-provider", usageProvider); } catch {}
    if (state.snapshot) renderUsage();
  }
  const filter = event.target.closest("[data-filter]"); if (filter) setFilter(filter.dataset.filter);
  const agent = event.target.closest("[data-agent]"); if (agent) openAgent(agent.dataset.agent);
  const hostToggle=event.target.closest('[data-toggle-host]');if(hostToggle)foldHost(hostToggle.dataset.toggleHost,hostToggle.getAttribute('aria-expanded')==='true');
  const connection = event.target.closest("[data-connection]"); if (connection) revealHost(connection.dataset.connection);
  if (event.target.closest("[data-back]")) closeBuffer();
  const pane = event.target.closest("[data-mobile-pane]");
  if (pane) {
    bufferTrail.length = 0;
    if (pane.dataset.mobilePane === "workspace") showView("agents");
    else mobilePane("supervisor");
  }
  const page = event.target.closest("[data-mobile-page]");
  if (page) { bufferTrail.length = 0; showView(page.dataset.mobilePage); }
  const prompt = event.target.closest("[data-prompt]"); if (prompt) submitSupervisor(prompt.dataset.prompt);
  if (event.target.closest("#clear-context")) { state.context = null; renderChat(); }
  if (event.target.closest("#open-context") && agentById(state.context)) {
    if (state.selected === state.context && detail.text !== null) showView("agent"); else openAgent(state.context);
  }
  const restoreSupervisor = event.target.closest("[data-restore-supervisor]");
  const restoreAgent = event.target.closest("[data-restore-agent]");
  const dismiss=event.target.closest('[data-dismiss-delivery]');
  if(dismiss){const entry=deliveries.get(dismiss.dataset.dismissDelivery);if(entry)dismissDelivery(entry);}
  if (restoreSupervisor || restoreAgent) {
    const entry = restoreSupervisor ? interaction.outbox.find((item) => item.request_id === restoreSupervisor.dataset.restoreSupervisor) : interaction.receipts.get(state.selected);
    const input = $(restoreSupervisor ? "supervisor-input" : "agent-message");
    if (entry) await restoreDeliveryDraft(entry,input);
  }
  const remove = event.target.closest("[data-remove-connection]");
  if (remove) { try { await api("/api/connection/remove", { id: remove.dataset.removeConnection }); await poll(); } catch (error) { toast(error.message, true); } }
});
$("overview-button").onclick = () => setFilter(state.filter);
$("agent-tools-toggle").onclick = () => {
  const open = $("agent-tools-toggle").getAttribute("aria-expanded") !== "true";
  $("agent-tools-toggle").setAttribute("aria-expanded", String(open));
  $("agent-view").classList.toggle("tools-open", open);
};
$("agent-to-supervisor").onclick = () => { state.context = state.selected; mobilePane("supervisor"); renderChat(); };
$("activity-button").onclick = () => state.view === "activity" ? closeBuffer() : openBuffer("activity");
$("search").oninput = (event) => { state.query = event.target.value.toLowerCase().trim(); renderAgents(); };
$('search').placeholder='Find an agent…';
$('search').setAttribute('aria-label','Find an agent or action');
$('search').onkeydown=event=>{
  if(event.isComposing || !['ArrowDown','Enter'].includes(event.key))return;
  const first=$('agents').querySelector('[data-item-key]:not(:disabled)');
  if(first){event.preventDefault();if(event.key==='Enter')first.click();else first.focus();}
};
$("supervisor-form").onsubmit = (event) => { event.preventDefault(); submitSupervisor($("supervisor-input").value); };
$("supervisor-input").oninput = sizeSupervisorInput;
window.addEventListener("resize", sizeSupervisorInput);
document.fonts?.addEventListener("loadingdone", () => { supervisorInputSize=null; sizeSupervisorInput(); });
let appearanceReading=null;
window.addEventListener('hub-appearance-changing',event => {
  if (!['size','font','spacing'].some(key => event.detail.previous[key] !== event.detail.value[key])) return;
  const reader=$('agent-reader');
  if (!reader.getClientRects().length) return;
  const nodes=[...reader.querySelectorAll('[data-message-id],[data-message-key]')],id=node=>node.dataset.messageId || node.dataset.messageKey;
  appearanceReading={reader,nodes,id,position:HubReading.position(reader,nodes,id)};
});
window.addEventListener('hub-appearance-change',() => {
  supervisorInputSize=null;sizeSupervisorInput();
  if ($('agent-message').getClientRects().length) sizeAgentInput();
  if (appearanceReading) {
    const {reader,nodes,id,position}=appearanceReading;
    HubReading.restore(reader,position,nodes,id);appearanceReading=null;
  }
});
function deletePreviousWord(event) {
  if (!event.ctrlKey || event.altKey || event.metaKey || event.shiftKey || event.key.toLowerCase() !== "w" || event.isComposing) return;
  event.preventDefault();
  const input = event.currentTarget, end = input.selectionEnd;
  let start = input.selectionStart;
  if (start === end) {
    const before = input.value.slice(0, start);
    start = before.replace(/\S+\s*$|\s+$/u, "").length;
  }
  if (start === end) return;
  input.setRangeText("", start, end, "end");
  input.dispatchEvent(new Event("input", { bubbles: true }));
}
for (const id of ["supervisor-input", "agent-message"]) $(id).addEventListener("keydown", deletePreviousWord);
$("supervisor-input").onkeydown = (event) => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); submitSupervisor(event.target.value); } };
$("chat-new-output").onclick = () => { $("chat").scrollTop = $("chat").scrollHeight; $("chat-new-output").hidden = true; };
$("chat").onscroll = () => { if ($("chat").scrollHeight - $("chat").scrollTop - $("chat").clientHeight < 80) $("chat-new-output").hidden = true; };
function sizeAgentInput() {
  const input = $("agent-message");
  input.style.height = "44px";
  if (input.value) input.style.height = `${Math.min(120, Math.max(44, input.scrollHeight))}px`;
}
$("agent-message").oninput = () => { if (state.selected) detail.drafts.set(state.selected, $("agent-message").value); sizeAgentInput(); updateDetailMeta(); };
$("agent-message").onkeydown = (event) => {
  if(event.key==='Enter' && (event.ctrlKey || event.metaKey) && !event.shiftKey && !event.isComposing){event.preventDefault();window.HubFocus?.replyAndNext();return;}
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); if (!$("send-agent").disabled) $("agent-message-form").requestSubmit(); }
};
$("agent-message-form").onsubmit = (event) => {
  event.preventDefault();
  sendAgentMessage($("agent-message").value);
};
async function sendAgentMessage(message, input = $("agent-message"), options = {}) {
  if (HubRouting.supervisorRequest(message) !== null) return submitSupervisor(message, input);
  const original=message,imageBatch=window.HubImages?.capture(input);
  if(!message.trim()&&imageBatch?.items.length)message='Please look at the attached images.';
  const agentId = state.selected;
  const agent = agentById(agentId), prior = interaction.receipts.get(agentId);
  const awaitingStart = prior?.status === "sent" && !prior.responseSeen && !prior.wasRunning && nowSeconds() - prior.created < 30;
  if (!message.trim() || !agent || !agent.connection_online || !agent.live ||
      ["running", "waiting"].includes(agent.state) || detail.sending.has(agentId) || awaitingStart) return false;
  const baseline = detail.pending ?? detail.text ?? agentById(agentId)?.preview;
  const receipt = { kind:'agent',agent_id:agentId,request_id: requestId(), text: message, created: nowSeconds(), status: "sending", baseline: latestResponse(baseline), baselineInputs: matchingInputs(baseline, message), responseSeen: false, wasRunning: false };
  receipt.attachments=imageBatch?.items.length?HubImages.metadata(imageBatch):[];
  receipt.request={type:'send',agent_id:agentId,message,command_id:receipt.request_id,...(options.handoff?{expected_state:options.handoff.version}:{})};
  if(!imageBatch?.items.length)rememberDelivery(receipt);
  interaction.receipts.set(agentId, receipt);
  detail.sending.add(agentId);
  if (input?.value === original) { input.value = ""; detail.drafts.delete(agentId); }
  sizeAgentInput();
  window.HubFocus?.persistDrafts();
  if (detail.pending !== null) applyPreview(detail.pending, true);
  $("agent-previous-output").open = false;
  setOutputMode("reader"); renderAgentConversation(); $("agent-reader").scrollTop = $("agent-reader").scrollHeight;
  updateDetailMeta();
  try {
    if(imageBatch?.items.length){
      receipt.attachments=await HubImages.upload(imageBatch,agentId);
      receipt.request.attachments=receipt.attachments.map(image=>image.id);
      rememberDelivery(receipt);if(state.selected===agentId)renderAgentConversation();
    }
    const result=options.handoff?await api('/api/reply-next',{id:options.handoff.id,request:receipt.request}):await api('/api/action',receipt.request);
    if(imageBatch?.items.length)HubImages.clear(imageBatch);
    receipt.status = "sent";
    if(options.handoff && result.handled_handoff!==options.handoff.id)throw new Error('Sent, but review was not confirmed. Stay here to check the result.');
    if (state.selected === agentId) { renderAgentConversation(); renderActivity(); }
    await poll();
    if (state.selected === agentId) detailPoll();
  } catch (error) { receipt.status = error.status && error.status < 500 ? "failed" : "uncertain"; receipt.error = error.message + (receipt.status === "uncertain" ? " Check raw output before resending." : ""); }
  finally {
    receipt.needsCheck=receipt.status==='uncertain';rememberDelivery(receipt);
    if (["failed", "uncertain"].includes(receipt.status) && !detail.drafts.get(agentId)) detail.drafts.set(agentId,message);
    window.HubFocus?.persistDrafts();
    detail.sending.delete(agentId); if (state.selected === agentId) renderAgentConversation(); updateDetailMeta();
  }
  return receipt.status === "sent";
}
$('queue-agent').onclick=async()=>{
  const agent=agentById(state.selected),input=$('agent-message'),imageBatch=window.HubImages?.capture(input),message=input.value.trim()?input.value:(imageBatch?.items.length?'Please look at the attached images.':'');
  if($('queue-agent').disabled || $('queue-agent').hidden || !agent || !message.trim())return;
  const entries=interaction.queues.get(agent.id) || [];
  const entry={kind:'queue',agent_id:agent.id,request_id:requestId(),text:message,created:nowSeconds(),status:'sending',baselineCount:HubQueue.count(nativeQueue.texts(AgentReader.queued(detail.text || '')),message)+HubQueue.count(entries.filter(e=>!e.confirmed&&!e.dismissed&&e.status!=='failed').map(e=>e.text),message)};
  entry.attachments=imageBatch?.items.length?HubImages.metadata(imageBatch):[];
  entry.request={type:'queue',agent_id:agent.id,message,command_id:entry.request_id};if(!imageBatch?.items.length)rememberDelivery(entry);
  entries.push(entry);interaction.queues.set(agent.id,entries.slice(-20));detail.sending.add(agent.id);
  input.value='';detail.drafts.delete(agent.id);window.HubFocus?.persistDrafts();sizeAgentInput();renderAgentConversation();updateDetailMeta();
  $('agent-reader').scrollTop=$('agent-reader').scrollHeight;
  try{
    if(imageBatch?.items.length){
      entry.attachments=await HubImages.upload(imageBatch,agent.id);
      entry.request.attachments=entry.attachments.map(image=>image.id);
      rememberDelivery(entry);if(state.selected===agent.id)renderAgentConversation();
    }
    const result=await api('/api/action',entry.request);
    if(imageBatch?.items.length)HubImages.clear(imageBatch);
    entry.status=result.delivery==='sent'?'sent':'queued';
    if(entry.status==='sent'){entry.confirmed=true;interaction.receipts.set(agent.id,{...entry,confirmed:false,responseSeen:false,wasRunning:false,baseline:latestResponse(detail.text || ''),baselineInputs:matchingInputs(detail.text || '',message)});}
    await poll();if(state.selected===agent.id)await detailPoll(true);
  }catch(error){if(!entry.confirmed){entry.status=error.status && error.status<500?'failed':'uncertain';entry.error=error.message+(entry.status==='uncertain'?' Check the queue before trying again.':'');if(!detail.drafts.get(agent.id))detail.drafts.set(agent.id,message);}}
  finally{entry.needsCheck=entry.status==='uncertain';rememberDelivery(entry);detail.sending.delete(agent.id);window.HubFocus?.persistDrafts();if(state.selected===agent.id){renderAgentConversation();updateDetailMeta();}}
};
document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-restore-queue]');if(!button)return;
  const entry=(interaction.queues.get(state.selected)||[]).find(item=>item.request_id===button.dataset.restoreQueue);
  if(entry)await restoreDeliveryDraft(entry,$('agent-message'));
});
document.querySelectorAll("[data-output-mode]").forEach((button) => {
  button.onclick = () => setOutputMode(button.dataset.outputMode);
  button.onkeydown = (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault(); event.stopPropagation();
    const mode = event.key === "Home" ? "reader" : event.key === "End" ? "raw" : detail.mode === "reader" ? "raw" : "reader";
    setOutputMode(mode); $(mode === "reader" ? "reader-tab" : "raw-tab").focus();
  };
});
$("new-output").onclick = () => {
  window.HubFocus?.stopRestoring?.();
  if(detail.mode==='reader')AgentReader.latest($('agent-output'),true);
  if (detail.pending !== null) applyPreview(detail.pending, true);
  const output=$(detail.mode==='raw'?'terminal':'agent-reader');output.scrollTop=output.scrollHeight;
  detail.unreadOutput = false; renderOutputNavigation();
};
$('agent-reader').addEventListener('scroll', () => {
  const reader=$('agent-reader');
  if(reader.scrollHeight-reader.scrollTop-reader.clientHeight<80 && $('agent-output').dataset.historyPast!=='true'){detail.unreadOutput=false;renderOutputNavigation();}
});
$('agent-output').addEventListener('readerpage',()=>{
  window.HubFocus?.stopRestoring?.();
  renderOutputNavigation();
  window.HubFocus?.saveReading();
});
$('agent-output').addEventListener('readerwindowchange',renderOutputNavigation);
for (const [id, offset] of [["previous-agent", -1], ["next-agent", 1]]) $(id).onclick = () => {
  const next = detail.order[detail.order.indexOf(state.selected) + offset];
  if (next) { openAgent(next); $(id).focus({ preventScroll: true }); }
};
$("pause-agent").onclick = () => doAction({ type: "interrupt", agent_id: state.selected }, $("pause-agent"));
stopAgent.onclick=async()=>{
  if(stopAgent.disabled)return;
  stopAgent.dataset.pending='true';stopAgent.disabled=true;
  try{await doAction({type:'interrupt',agent_id:state.selected},stopAgent);}
  finally{delete stopAgent.dataset.pending;updateDetailMeta();}
};
$("acknowledge-agent").onclick = () => doAction({ type: "acknowledge", agent_id: state.selected }, $("acknowledge-agent"));
$("focus-agent").onclick = () => doAction({ type: "focus", agent_id: state.selected, focused: !agentById(state.selected)?.mark.focused }, $("focus-agent"));
const shellQuote = (value) => `'${value.replace(/'/g, "'\\''")}'`;
$("copy-attach").onclick = async () => {
  const agent = agentById(state.selected); if (!agent) return;
  const conn = state.snapshot.connections.find((c) => c.id === agent.connection_id);
  let command = `tmux${conn.socket ? ` -S ${shellQuote(conn.socket)}` : ""} attach-session -t ${shellQuote(agent.session)} \\; select-window -t ${shellQuote(agent.window)} \\; select-pane -t ${shellQuote(agent.pane)}`;
  if (conn.kind === "ssh" || conn.kind === "peer") command = `ssh -a -o ForwardAgent=no -t ${shellQuote(conn.target)} ${shellQuote(command)}`;
  try { await navigator.clipboard.writeText(command); toast("Tmux command copied"); } catch { toast(command); }
};
$("add-connection").onclick = () => { openBuffer("connection"); $("connection-form").elements.label.focus(); };
$("node-endpoint").onchange = (event) => {
  const node = state.snapshot.nodes.find((node) => node.id === event.target.value);
  if (node?.url && !node.local) window.location.assign(node.url);
};
$("supervisor-owner").onchange = () => renderChat();
$("handoff-supervisor").onclick = async () => {
  $("handoff-supervisor").disabled = true;
  try { const result = await api("/api/supervisor/handoff", { target: $("supervisor-owner").value }); toast(result.message); await poll(); }
  catch (error) { toast(error.message, true); }
  finally { renderChat(); }
};
$("connection-form").elements.kind.onchange = (event) => { $("ssh-target-label").hidden = event.target.value === "local"; };
$("connection-form").onsubmit = async (event) => {
  event.preventDefault(); const button = event.target.querySelector('[type="submit"]'); button.disabled = true;
  try { await api("/api/connections", Object.fromEntries(new FormData(event.target))); closeBuffer(); event.target.reset(); $("ssh-target-label").hidden = false; toast("Connection added; checking the host"); await poll(); }
  catch (error) { toast(error.message, true); } finally { button.disabled = false; }
};
$("launch-button").onclick = () => {
  const previous = $("launch-connection").value;
  $("launch-connection").innerHTML = state.snapshot.connections.filter((c) => c.status === "online").map((c) => `<option value="${esc(c.id)}">${esc(c.label)}</option>`).join("");
  if ([...$("launch-connection").options].some((option) => option.value === previous)) $("launch-connection").value = previous;
  const selected = agentById(state.selected); if (selected && !$("launch-form").elements.cwd.value) { $("launch-form").elements.cwd.value = selected.cwd; $("launch-connection").value = selected.connection_id; }
  openBuffer("launch"); $("launch-form").elements.cwd.focus();
};
$("launch-form").onsubmit = async (event) => { event.preventDefault(); if (await doAction({ type: "launch", ...Object.fromEntries(new FormData(event.target)) }, event.target.querySelector('[type="submit"]'))) { closeBuffer(); event.target.reset(); } };
$("voice-toggle").onclick = () => {
  if (!("speechSynthesis" in window)) return toast("Speech playback is unavailable in this browser.", true);
  state.voice = !state.voice; $("voice-label").textContent = state.voice ? "Voice on" : "Voice off"; $("voice-toggle").setAttribute("aria-pressed", String(state.voice));
  if (state.voice) speak(briefLocally()); else window.speechSynthesis.cancel();
};
$("brief-button").onclick = () => submitSupervisor("Brief me by project across all hosts. Explain each research goal, current studies, concrete progress or results, and next steps. Identify the agents behind each task. Include ongoing work, not only items needing attention. Keep each project concise. Do not take actions.");
function previousConversation(){
  const previous=state.previous;
  if(previous?.agent && agentById(previous.agent))openAgent(previous.agent);
  else if(previous?.supervisor)window.HubFocus?.home();
}
function paletteActions(){
  const agent=agentById(state.selected),previous=Boolean(state.previous?.supervisor || agentById(state.previous?.agent));
  return [
    {id:'refresh',label:'Refresh',search:'refresh update sync reload',icon:'refresh',disabled:state.refreshing,run:refreshWorkspace},
    ...(window.HubResearch?[{id:'research',label:'Research',search:'research learning papers archive notes progress',icon:'book',run:()=>HubResearch.open()}]:[]),
    ...(agent?[
      {id:'rename',label:'Rename '+agentTitle(agent),search:'rename name title window task',icon:'edit',run:()=>$('rename-agent').click()},
      {id:'pause',label:'Pause '+agentTitle(agent),search:'pause interrupt stop agent',icon:'pause',disabled:!agent.connection_online || !agent.live || !(agent.state==='running' || ['retrying','checking','waiting'].includes(agent.recovery?.status)),run:()=>$('pause-agent').click()}
    ]:[]),
    ...(previous?[{id:'previous',label:'Previous conversation',search:'previous back last conversation',icon:'back',run:previousConversation}]:[])
  ];
}
$('agents').addEventListener('click',event=>{
  const button=event.target.closest('[data-palette-action]');if(!button)return;
  const action=paletteActions().find(item=>item.id===button.dataset.paletteAction);
  if(action && !action.disabled){$('agent-drawer').close();action.run();}
});
function findAgent() { showView("agents"); $("search").focus();$('search').select(); }
$("find-button").onclick = findAgent;
document.addEventListener("focusin", (event) => {
  const row = event.target.closest?.("[data-agent]");
  if (row) { state.cursor = row.dataset.agent; document.querySelectorAll("[data-agent]").forEach((agent) => agent.classList.toggle("cursor", agent.dataset.agent === state.cursor)); }
});
document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); findAgent(); return; }
  if(state.view==='archive' && !document.querySelector('dialog[open]') && !event.isComposing && !event.ctrlKey && !event.metaKey &&
      event.target.closest?.('.workspace') && !event.target.closest?.('input,textarea,select,[contenteditable=true]') &&
      ['Escape','h','ArrowLeft'].includes(event.key) && (!event.altKey || event.key==='ArrowLeft')){
    event.preventDefault();window.HubArchive?.back();return;
  }
  if(event.altKey && event.key==='ArrowLeft' && !event.target.closest?.('input,textarea,select,[contenteditable=true]') && state.previous){event.preventDefault();previousConversation();return;}
  if (event.target.closest?.("input,textarea,select,[contenteditable=true]") || event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
  if (event.key === "/") { event.preventDefault(); findAgent(); return; }
  if (state.view !== "agents" && ["Escape", "h", "ArrowLeft"].includes(event.key) && event.target.closest?.(".workspace")) { event.preventDefault(); closeBuffer(); return; }
  const row = event.target.closest?.("[data-item-key]");
  if (!row || (state.view !== "agents" && !$("agent-drawer")?.open)) return;
  const rows = [...$('agents').querySelectorAll('[data-item-key]:not(:disabled)')].filter(node=>node.getClientRects().length);
  const index = rows.indexOf(row);
  if (["j", "k", "ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? rows.length - 1 : Math.min(rows.length - 1, Math.max(0, index + (["j", "ArrowDown"].includes(event.key) ? 1 : -1)));
    rows[next].focus({ preventScroll: true }); rows[next].scrollIntoView({ block: "nearest" });
  } else if(event.key==='ArrowLeft') {
    const group=row.closest('[data-host-group]');if(group){event.preventDefault();foldHost(group.dataset.hostGroup,true);[...$('agents').querySelectorAll('[data-toggle-host]')].find(node=>node.dataset.toggleHost===group.dataset.hostGroup)?.focus({preventScroll:true});}
  } else if (["l", "ArrowRight"].includes(event.key)) { event.preventDefault();if(row.dataset.toggleHost)foldHost(row.dataset.toggleHost,false);else row.click(); }
});
const phoneLayout = window.matchMedia("(max-width:800px)");
const touchInput = window.matchMedia("(pointer:coarse)");
// Settings have one home on every screen size. Move existing nodes once so
// their listeners, selected provider, and input state remain intact.
for (const [node, destination] of [
  [$("sync-state"), $("mobile-connections-slot")],
  [document.querySelector(".sidebar"), $("mobile-connections-slot")],
  [document.querySelector(".weekly-usage"), $("mobile-usage-slot")],
  [$("node-controls"), $("mobile-connections-slot")],
  [document.querySelector(".watch-bar"), $("mobile-settings-slot")],
  [$("voice-toggle"), $("mobile-settings-slot")],
]) destination.append(node);
function arrangePhone() {
  mobilePane(document.querySelector(".app-shell").dataset.mobileView);
  sizeSupervisorInput();
}
phoneLayout.addEventListener("change", arrangePhone);
window.addEventListener("popstate", event => {
  const route = event.state?.hubRoute;
  if (!phoneLayout.matches || !route || (!(route.view in viewIds) && route.view !== "home")) return;
  restoringPhoneRoute = true;
  try {
    if (state.selected) detail.drafts.set(state.selected, $("agent-message").value);
    bufferTrail.splice(0, bufferTrail.length, ...(route.trail || []));
    if (route.view === "agent" && agentById(route.agent)) {
      if (state.selected !== route.agent) openAgent(route.agent); else showView("agent");
    } else showView(route.view === "agent" ? "agents" : route.view);
    bufferTrail.splice(0, bufferTrail.length, ...(route.trail || []));
    mobilePane(route.pane);
  } finally { restoringPhoneRoute = false; }
});
let phoneViewportBaseline=0,phoneViewportWidth=0;
function updatePhoneViewport() {
  const viewport = window.visualViewport;
  // Foldables and tablets can use the wide layout with a software keyboard.
  // Visible geometry, not the navigation breakpoint, determines the space.
  if (!viewport || Math.abs(viewport.scale-1)>.01) {
    document.body.style.removeProperty("--phone-height");
    document.body.style.removeProperty("--phone-top");
    document.body.classList.remove("phone-keyboard");
    return;
  }
  if(Math.abs(innerWidth-phoneViewportWidth)>60){phoneViewportBaseline=0;phoneViewportWidth=innerWidth;}
  const focused=Boolean(document.activeElement?.matches('input,textarea,[contenteditable=true]'));
  if(!focused)phoneViewportBaseline=Math.max(phoneViewportBaseline,innerHeight);
  // Firefox can resize the layout viewport as well as the visual viewport.
  // Use their intersection during keyboard transitions, including browser pan.
  const top=Math.max(0,Math.min(viewport.offsetTop,innerHeight));
  const height=Math.max(0,Math.min(viewport.height,innerHeight-top));
  document.body.style.setProperty("--phone-top", `${top}px`);
  document.body.style.setProperty("--phone-height", `${height}px`);
  document.body.classList.toggle("phone-keyboard", focused && Math.max(phoneViewportBaseline,innerHeight)-height>120);
}
window.visualViewport?.addEventListener("resize", updatePhoneViewport);
window.visualViewport?.addEventListener("scroll", updatePhoneViewport);
window.addEventListener("resize", updatePhoneViewport);
document.addEventListener("focusin", updatePhoneViewport);
document.addEventListener("focusout", () => requestAnimationFrame(updatePhoneViewport));
arrangePhone();
updatePhoneViewport();
function updateClock() { const now = new Date(); $("clock-time").textContent = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }); $("clock-date").textContent = now.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" }); }
updateClock(); setInterval(updateClock, 30000);
const needsLiveUpdates=()=>!document.hidden || Boolean(window.HubFocus?.voiceActive());
const liveClient=window.HubLive?new (window.BroadcastChannel?HubLive.Tabs:HubLive.Client)({
  query:()=>({active:needsLiveUpdates() && Boolean(state.snapshot),agentId:state.view==='agent'?state.selected:'',history:detail.history,terminal:detail.mode==='raw'}),
  onState:value=>{if(!state.refreshing)receiveSnapshot(value);},
  onPreview:(id,history,value,terminal)=>{if(!state.refreshing&&history===detail.history&&terminal===(detail.mode==='raw'))receivePreview(id,value);},
  onError:(topic,error)=>{if(!state.refreshing && topic[0]==='preview' && topic[1]===state.selected && Boolean(topic[3])===(detail.mode==='raw')){$('preview-error').textContent=error;$('preview-error').hidden=false;const observation=interaction.observations.get(state.selected);if(observation)observation.previewError=true;nativeApprovals.setConnected(false);}}
}):null;
poll();
setInterval(()=>{liveClient?.sync();if(needsLiveUpdates() && !liveClient?.healthy){poll();detailPoll();}},1000);
setInterval(()=>{if(needsLiveUpdates())pollJob();},1500);
setInterval(()=>{if(needsLiveUpdates())renderActivity();},1000);
function resumeUpdates(){liveClient?.sync();if(!document.hidden){poll();detailPoll();pollJob();reconcileDeliveries(true);}}
document.addEventListener('visibilitychange',resumeUpdates);
window.addEventListener('pageshow',resumeUpdates);
window.addEventListener('online',resumeUpdates);

$("notification-button").onclick = () => openBuffer("notifications");
async function setWatching(enabled) {
  if (watchChanging || !state.snapshot?.watch?.eligible) return;
  watchChanging = true;
  $("toggle-watch").disabled = true;
  try {
    await api("/api/notifications", { enabled });
    await poll();
    toast(enabled ? "Watching resumed. New handoffs can notify you." : "Watching paused. New alerts are off.", false,
      enabled ? undefined : { label: "Resume", run: () => setWatching(true) });
  }
  catch (error) { toast(error.message, true); }
  finally { watchChanging = false; renderNotifications(); }
}
$("toggle-watch").onclick = () => setWatching(!state.snapshot.watch.enabled);
$("read-notifications").onclick = async () => {
  try { await api("/api/notifications", { read: (state.snapshot.watch.items || []).filter(item => !item.read_at).map(item => item.id) }); await poll(); }
  catch (error) { toast(error.message, true); }
};
let linkedNotice=null,updateNavigation=0;
async function openUpdate(identity,fallbackAgent=''){
  const ticket=++updateNavigation;
  const position=()=>JSON.stringify([state.view,state.selected,state.context,detail.generation]);
  // The push already carries the destination. Show it immediately, even when
  // loading the saved notice or its original reply takes longer than the view.
  if(agentById(fallbackAgent))openAgent(fallbackAgent);
  let origin=position();
  const unchanged=()=>ticket===updateNavigation && origin===position();
  let notice;
  try{notice=await api('/api/notification?id='+encodeURIComponent(identity));}
  catch(error){
    if(!unchanged())return;
    if(agentById(fallbackAgent)){toast('Opened the agent. This saved alert is no longer available.');return;}
    throw error;
  }
  if(!unchanged())return;
  const target=notice.target || {},agent=agentById(notice.agent_id);
  if(agent){
    if(state.view!=='agent' || state.selected!==agent.id)openAgent(agent.id);
    origin=position();
    let message=await HubAttention.source(target,detail.messages || []);
    if(!unchanged())return;
    if(target.message_id && !message){
      const signature=detail.signature;
      try{
        const preview=await api('/api/preview?'+new URLSearchParams({agent_id:agent.id,history:'1'}));
        if(!unchanged())return;
        // A newer live preview wins over a slower notification lookup.
        if(detail.signature===signature && !isReadingOutput())receivePreview(agent.id,preview,true);
        message=await HubAttention.source(target,detail.messages || []);
      }catch{ /* Keep the conversation open when its host is unavailable. */ }
      if(!unchanged())return;
    }
    if(message){
      const generation=detail.generation;
      requestAnimationFrame(()=>{
        if(state.view!=='agent' || state.selected!==agent.id || detail.generation!==generation || isReadingOutput())return;
        const node=AgentReader.reveal($('agent-output'),'assistant:'+message.id) || [...$('agent-output').querySelectorAll('[data-message-id]')].find(node=>node.dataset.messageId==='assistant:'+message.id);
        if(node)HubFocus.jumpTo(node);
      });
    }else if(target.message_id){
      toast('Opened the agent. The original reply is outside its available history.',false,
        {label:'Saved update',run:()=>{linkedNotice=notice;updateFilter=notice.handled_at?'handled':'pending';openBuffer('notifications');renderNotifications();}});
    }
  }else{
    linkedNotice=notice;updateFilter=notice.handled_at?'handled':'pending';openBuffer('notifications');renderNotifications();
    requestAnimationFrame(()=>{
      const node=[...$('notifications').querySelectorAll('[data-update-id]')].find(node=>node.dataset.updateId===notice.id);
      if(node)HubFocus.jumpTo(node);
    });
  }
  await api('/api/notifications',{read:[notice.id]});
  if(linkedNotice?.id===notice.id)linkedNotice={...linkedNotice,read_at:linkedNotice.read_at || nowSeconds()};
}
async function openNotification(event) {
  const button = event.target.closest("[data-notification-agent], [data-read-notification], [data-handle-notification], [data-reopen-notification]");
  if (!button) return;
  const action = button.dataset.handleNotification ? "handle" : button.dataset.reopenNotification ? "reopen" : "read";
  const id = button.dataset.handleNotification || button.dataset.reopenNotification || button.dataset.notificationId || button.dataset.readNotification;
  button.disabled = true;
  try {
    if(button.dataset.notificationAgent)await openUpdate(id,button.dataset.notificationAgent);else await api("/api/notifications", { [action]: [id] });
    if(linkedNotice?.id===id)linkedNotice=await api('/api/notification?id='+encodeURIComponent(id));
  }
  catch (error) { toast(error.message, true); }
  button.disabled = false;
  await poll();
}
$("handle-shown-updates").onclick = async () => {
  const ids = (state.snapshot.watch.items || []).filter(item => !item.handled_at).map(item => item.id);
  $("handle-shown-updates").disabled = true;
  try { await api("/api/notifications", {handle: ids}); await poll(); }
  catch (error) { toast(error.message, true); renderNotifications(); }
};
document.querySelectorAll("[data-update-filter]").forEach(button => {
  button.onclick = () => { updateFilter = button.dataset.updateFilter; renderNotifications(); };
});
$("notifications").onclick = openNotification;
$("chat").addEventListener("click", openNotification);
$("enable-push").onclick = async () => {
  // Open synchronously so browsers keep the user gesture for the new tab.
  const popup = window.open("about:blank", "_blank");
  if (popup) popup.opener = null;
  try {
    const result = await api("/api/notifications/pair", {});
    if (popup) popup.location.href = result.url;
    else window.location.href = result.url;
  } catch (error) { popup?.close(); toast(error.message, true); }
};
$('check-push').onclick=async()=>{
  if(pushChecking)return;
  pushChecking=true;renderNotifications();
  try { await api('/api/notifications/status',{});await poll(); }
  catch(error) { toast(error.message,true); }
  finally {pushChecking=false;renderNotifications();}
};
// Also handle an existing browser/PWA window receiving a new push URL.
let pushNavigation=0,pushRoute='';
function routeNotification(){
  const notificationRoute=HubPush.route(location.href);
  if(!notificationRoute || notificationRoute.key===pushRoute)return;
  pushRoute=notificationRoute.key;
  const ticket=++pushNavigation;
  const routeWhenReady = async () => {
    if(ticket!==pushNavigation)return;
    if (!state.snapshot || !window.HubFocus) return setTimeout(routeWhenReady, 200);
    // Complete first-paint restoration before starting a notification lookup.
    window.HubFocus.render();
    const {notice:identity,agent:id}=notificationRoute;
    try{
      if(identity)await openUpdate(identity,id);
      else if(id && agentById(id))openAgent(id);
      else openBuffer('notifications');
    }catch(error){pushRoute='';toast(error.message,true);return;}
    if(ticket!==pushNavigation)return;
    const url=new URL(location.href);
    if(HubPush.route(url.href)?.key===notificationRoute.key){
      for(const key of ['updates','notice','agent'])url.searchParams.delete(key);
      if(new URLSearchParams(url.hash.slice(1)).has('updates'))url.hash='';
      history.replaceState(history.state,'',url.pathname+url.search+url.hash);
    }
    pushRoute='';
  };
  routeWhenReady();
}
window.addEventListener('hashchange',routeNotification);
window.addEventListener('pageshow',routeNotification);
window.addEventListener('online',routeNotification);
window.addEventListener('popstate',routeNotification);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)routeNotification();});
routeNotification();

let editingMessage = null;
$("agent-output").addEventListener("click", event => {
  const button = event.target.closest("[data-edit-message]");
  if (!button) return;
  const message = detail.messages?.find(item => item.id === button.dataset.editMessage);
  if (!message?.fork) return;
  editingMessage = {agent_id: state.selected, message_id: message.id, command_id: requestId()};
  $("edit-message").value = AgentReader.userText(HubMessageContent.displayText(message)); $("edit-error").textContent = "";
  $("edit-dialog").showModal(); $("edit-message").focus();
});
$("cancel-edit").onclick = () => $("edit-dialog").close();
$("edit-message").addEventListener("keydown", deletePreviousWord);
$("edit-message").addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {event.preventDefault(); if (!$("fork-send").disabled) $("edit-form").requestSubmit();}
});
$("edit-form").onsubmit = async event => {
  event.preventDefault();
  if (!editingMessage || $("fork-send").disabled) return;
  $("fork-send").disabled = true; $("fork-send").textContent = "Creating branch…";
  $("edit-error").textContent = "";
  try {
    const result = await api("/api/action", {type: "fork", ...editingMessage, message: $("edit-message").value});
    $("edit-dialog").close(); toast(result.message);
    for (let attempt = 0; attempt < 8; attempt++) {
      await poll();
      const branch = state.snapshot.agents.find(agent => agent.session === result.branch_session);
      if (branch) { openAgent(branch.id); break; }
      await new Promise(resolve => setTimeout(resolve, 500));
    }
  } catch (error) { $("edit-error").textContent = error.message; }
  finally { $("fork-send").disabled = false; $("fork-send").textContent = "Fork & send"; }
};

let renamingAgent = null;
function updateRenameForm() {
  const name = $("window-name").value.trim(), saving = Boolean(renamingAgent?.saving);
  const label=$('task-name').value.trim();
  $("window-name").disabled = saving || !renamingAgent?.canRename;
  $('task-name').disabled=saving;
  $("save-window-name").disabled = !renamingAgent || saving || !name || (name === renamingAgent.original && label===renamingAgent.label);
  $("save-window-name").textContent = saving ? "Saving…" : "Save";
  $("rename-form").setAttribute("aria-busy", String(saving));
}
$("rename-agent").onclick = () => {
  const agent = agentById(state.selected); if (!agent) return;
  renamingAgent = {agent_id: agent.id, original: agent.title, label:agent.mark?.task_label || '', canRename:agent.connection_online && agent.live, saving: false};
  $('task-name').value=renamingAgent.label;$('task-name').placeholder=agentTitle(agent);
  $("window-name").value = agent.title; $("rename-error").textContent = "";
  $("rename-host").textContent = agent.connection_label;
  updateRenameForm();
  $("rename-dialog").showModal(); $('task-name').focus();$('task-name').select();
};
$("cancel-rename").onclick = () => $("rename-dialog").close();
$("rename-dialog").addEventListener("close", () => { renamingAgent = null; });
$("window-name").addEventListener("input", () => { $("rename-error").textContent = ""; updateRenameForm(); });
$("window-name").addEventListener("keydown", deletePreviousWord);
$('task-name').addEventListener('input',()=>{$('rename-error').textContent='';updateRenameForm();});
$('task-name').addEventListener('keydown',deletePreviousWord);
$("rename-form").onsubmit = async event => {
  event.preventDefault(); if (!renamingAgent || $("save-window-name").disabled) return;
  const target = renamingAgent, name = $("window-name").value.trim(), label=$('task-name').value.trim();
  target.saving = true; updateRenameForm(); $("rename-error").textContent = "";
  try {
    if(name!==target.original){await api('/api/action',{type:'rename',agent_id:target.agent_id,command_id:requestId(),name});target.original=name;}
    if(label!==target.label){await api('/api/action',{type:'label',agent_id:target.agent_id,label});target.label=label;}
    if (renamingAgent === target) $("rename-dialog").close();
    toast('Names saved.'); await poll();
  } catch (error) {
    if (renamingAgent === target) $("rename-error").textContent = error.message;
    else toast(error.message, true);
  } finally {
    target.saving = false;
    if (renamingAgent === target) updateRenameForm();
  }
};
