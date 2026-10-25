/* Search and read native session history without resuming or instructing it. */
(()=>{
  const text=(tag,value,className)=>{const node=document.createElement(tag);node.textContent=value;if(className)node.className=className;return node;};
  const button=(label,run,className='text-button')=>{const node=text('button',label,className);node.type='button';node.onclick=run;return node;};
  const view=document.createElement('section');view.id='archive-view';view.className='archive-view';view.hidden=true;
  document.querySelector('.workspace').append(view);viewIds.archive='archive-view';
  const drawer=$('agent-drawer'),heading=drawer.querySelector('.drawer-heading strong');
  const searchPanel=document.createElement('section');searchPanel.id='archive-search';searchPanel.hidden=true;searchPanel.setAttribute('aria-label','Search conversation history');drawer.append(searchPanel);
  const drawerBack=button('',()=>{HubFocus.navigate('agents');$('search').focus();},'icon-button');drawerBack.id='drawer-history-back';drawerBack.hidden=true;drawerBack.setAttribute('aria-label','Back to agents');drawerBack.innerHTML='<svg class="icon" aria-hidden="true"><use href="#i-back"/></svg>';heading.before(drawerBack);
  const toolbar=document.createElement('form');toolbar.className='archive-toolbar';toolbar.setAttribute('role','search');
  const input=document.createElement('input');input.type='search';input.id='archive-query';input.placeholder='Search messages or files…';input.setAttribute('aria-label','Search conversation history');input.maxLength=240;
  const field=document.createElement('div');field.className='search-box';field.innerHTML='<svg class="icon" aria-hidden="true"><use href="#i-search"/></svg>';field.append(input);
  const host=document.createElement('select');host.id='archive-host';host.setAttribute('aria-label','History host');
  const files=button('Files',()=>{ui.files=!ui.files;files.setAttribute('aria-pressed',String(ui.files));search();});files.id='archive-files';files.setAttribute('aria-pressed','false');
  const filters=document.createElement('div');filters.className='archive-filters';filters.append(host,files);toolbar.append(field,filters);
  const status=text('p','','archive-status');status.setAttribute('role','status');
  const pageStatus=text('p','','archive-status');pageStatus.setAttribute('role','status');pageStatus.hidden=true;
  const results=document.createElement('div');results.id='archive-results';results.className='archive-results';
  const reader=document.createElement('div');reader.id='archive-reader';reader.className='agent-reader archive-reader';reader.hidden=true;reader.tabIndex=-1;reader.setAttribute('role','region');reader.setAttribute('aria-label','Conversation history');
  const top=document.createElement('div'),output=document.createElement('div'),bottom=document.createElement('div');output.id='archive-output';top.className=bottom.className='archive-pages';reader.append(top,output,bottom);
  searchPanel.append(toolbar,status,results);view.append(pageStatus,reader);
  const ui={files:false,query:'',host:'',result:null,items:[],page:null,match:null,searchEpoch:0,pageEpoch:0,searchController:null,pageController:null,timer:null,complete:false};
  const menu=button('Search history',()=>open());menu.id='focus-archive';$('workspace-menu-items').append(menu);
  const drawerLink=button('',()=>open($('search').value),'icon-button');drawerLink.id='drawer-archive';drawerLink.title='Search conversation history';drawerLink.setAttribute('aria-label','Search conversation history');
  drawerLink.innerHTML='<svg class="icon" aria-hidden="true"><use href="#i-clock"/></svg>';document.querySelector('#agent-drawer .search-box').append(drawerLink);
  function hosts(){
    const previous=host.value;
    const nodes=state.snapshot?.nodes?.length?state.snapshot.nodes:(state.snapshot?.connections || []).map(c=>({id:c.id,local:c.kind==='local'}));
    host.replaceChildren(new Option('All hosts',''));
    for(const node of nodes){const connection=state.snapshot?.connections.find(c=>c.id===node.id);host.append(new Option(connection?.label || node.id,node.id));}
    host.value=previous;
  }
  function stopMedia(){for(const media of reader.querySelectorAll('audio,video'))media.pause();}
  function drawerMode(history){
    drawer.dataset.mode=history?'history':'agents';searchPanel.hidden=drawerBack.hidden=!history;heading.textContent=history?'History':'Workspace';
    drawer.setAttribute('aria-label',history?'Conversation history':'Agents and handoffs');
  }
  function agents(){cancelSearch();drawerMode(false);}
  function enter(){
    cancelSearch();drawer.close();document.querySelector('.workspace-menu').open=false;
    if(state.view!=='archive'){HubFocus.leaveConversation();openBuffer('archive');}
  }
  function open(query,focus=true){
    ui.fromResearch=false;
    // Searching does not change the active conversation, draft or voice call.
    document.querySelector('.workspace-menu').open=false;hosts();drawerMode(true);
    if(!drawer.open)drawer.showModal();
    const changed=query!==undefined&&query!==input.value;if(query!==undefined)input.value=query;
    if(changed||!ui.complete)search();
    if(focus)input.focus();
    else (Array.from(results.children).find(node=>node.dataset.archiveKey===ui.match?.key)||input).focus({preventScroll:true});
  }
  function cancelSearch(){ui.searchEpoch++;ui.searchController?.abort();clearTimeout(ui.timer);results.setAttribute('aria-busy','false');}
  function cancelPage(){ui.pageEpoch++;ui.pageController?.abort();}
  async function request(path,parameters,kind){
    const controller=new AbortController();ui[kind+'Controller']?.abort();ui[kind+'Controller']=controller;
    const response=await fetch(path+'?'+new URLSearchParams(Object.entries(parameters).filter(([,value])=>value!==undefined&&value!=='')),{cache:'no-store',signal:controller.signal});
    const value=await response.json();if(!response.ok)throw Error(value.error || 'History unavailable');return value;
  }
  function resultStatus(result){
    const unavailable=result.unavailable?.map(item=>item.label)||[];
    const pending=result.hosts.filter(item=>item.discovering||item.indexing||item.known_files>item.sessions);
    const skipped=result.hosts.reduce((sum,item)=>sum+(item.skipped || 0),0);
    return [pending.length?'Indexing '+pending.map(item=>item.label).join(', ')+' · Refresh for newer results':'',
      unavailable.length?'Unavailable: '+unavailable.join(', '):'',skipped?skipped+' records could not be indexed':''].filter(Boolean).join(' · ');
  }
  function renderResults(){
    results.replaceChildren();
    for(const item of ui.items){
      const row=button('',()=>conversation(item),'archive-result');row.dataset.archiveKey=item.key;
      const meta=text('span','','archive-result-meta'),source=text('span',item.host_label,'archive-host');source.dataset.hostTone=HubFocus.hostTone(item.host);
      meta.append(source,document.createTextNode(' · '+item.runtime+' · '+(item.role==='user'?'You':item.phase==='commentary'?'Progress':'Agent')));
      const excerpt=document.createElement('template');excerpt.innerHTML=HubMarkdown.render(item.excerpt);excerpt.content.querySelectorAll('.code-toolbar,.artifact-heading,button,[hidden]').forEach(node=>node.remove());
      row.append(meta,text('strong',item.title),text('span',excerpt.content.textContent.replace(/\s+/g,' ').trim(),'archive-excerpt'));
      if(item.paths.length)row.append(text('span',item.paths.map(path=>path.split('/').at(-1)).join(' · '),'archive-result-files'));
      const date=new Date(item.created),when=Number.isNaN(date.getTime())?'':date.toLocaleString([],{month:'short',day:'numeric',...(date.getFullYear()!==new Date().getFullYear()?{year:'numeric'}:{}),hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
      row.append(text('small',when+' · '+shortPath(item.cwd)));
      results.append(row);
    }
    if(!ui.items.length)results.append(text('p','No matches in the indexed history.','archive-empty'));
    if(ui.result.next)results.append(button('Older results',()=>search(true),'archive-more'));
  }
  async function search(more=false){
    cancelSearch();const epoch=ui.searchEpoch;ui.complete=false;status.hidden=false;
    if(!more){ui.query=input.value.trim();ui.host=host.value;ui.items=[];ui.result=null;results.replaceChildren();results.scrollTop=0;}
    if(!ui.query){ui.complete=true;status.textContent='Find a message or file, including closed sessions.';return;}
    status.textContent='Searching…';results.setAttribute('aria-busy','true');
    try{
      const result=await request('/api/archive/search',{q:ui.query,host:ui.host,files:ui.files?'1':'0',before:more?JSON.stringify(ui.result.next):undefined},'search');
      if(epoch!==ui.searchEpoch||searchPanel.hidden||!drawer.open)return;
      ui.complete=true;ui.result=result;ui.items.push(...result.items.filter(item=>!ui.items.some(old=>old.key===item.key)));renderResults();
      status.textContent=resultStatus(result);status.hidden=!status.textContent;
    }catch(error){if(error.name!=='AbortError'&&epoch===ui.searchEpoch){status.textContent=error.message;status.hidden=false;}}
    finally{if(epoch===ui.searchEpoch)results.setAttribute('aria-busy','false');}
  }
  const protectedReading=()=>{const selection=getSelection();return selection&&!selection.isCollapsed&&(reader.contains(selection.anchorNode)||reader.contains(selection.focusNode))||[...reader.querySelectorAll('audio,video')].some(media=>!media.paused)||HubMarkdown.isPreviewing?.(reader);};
  function parts(message){
    if(message.part.start===0&&message.part.end===message.part.total)return;
    const node=[...output.querySelectorAll('[data-message-id]')].find(node=>node.dataset.messageId===message.role+':'+message.id);
    if(!node||node.querySelector('.archive-parts'))return;
    const controls=document.createElement('div');controls.className='archive-parts';
    controls.append(text('span','Long message · '+(message.part.start+1).toLocaleString()+'–'+message.part.end.toLocaleString()+' / '+message.part.total.toLocaleString()+' characters'));
    if(message.part.start)controls.append(button('Earlier part',()=>page('',message.id,Math.max(0,message.part.start-180000)+80)));
    if(message.part.end<message.part.total)controls.append(button('Later part',()=>page('',message.id,message.part.end+80)));
    node.append(controls);
  }
  function highlightMatch(node){
    const words=[...new Set(ui.query.slice(0,240).match(/[\p{L}\p{N}]+/gu)||[])];
    if(!node||!words.length)return node;
    const walker=document.createTreeWalker(node.querySelector('.reader-prose'),NodeFilter.SHOW_TEXT);let part;
    while((part=walker.nextNode())){
      if(part.parentElement.closest('summary,button,.reply-outline,.katex,math,svg'))continue;
      const match=part.textContent.match(new RegExp(words.join('|'),'iu'));
      if(!match)continue;
      const range=document.createRange();range.setStart(part,match.index);range.setEnd(part,match.index+match[0].length);
      const mark=document.createElement('mark');mark.className='archive-hit';range.surroundContents(mark);
      for(let parent=mark.parentElement;parent&&parent!==node;parent=parent.parentElement)if(parent.tagName==='DETAILS')parent.open=true;
      return mark;
    }
    return node;
  }
  function renderPage(value,anchor){
    const epoch=ui.pageEpoch;ui.page=value;top.replaceChildren();bottom.replaceChildren();
    const focused=value.messages.find(message=>message.id===anchor)||value.messages[0];
    ui.anchor=focused?.id||'';ui.offset=focused?.part.start?focused.part.start+80:0;
    reader.setAttribute('aria-label','History: '+value.title);
    if(value.earlier)top.append(button('Earlier messages ↑',()=>page('before',value.messages[0].id)));
    top.append(button('Copy link',async()=>{const url=new URL(location.origin);url.searchParams.set('archive',value.session);url.searchParams.set('host',value.host);url.searchParams.set('message',focused?.id||'');url.searchParams.set('generation',value.generation);if(focused?.part.start)url.searchParams.set('offset',focused.part.start+80);if(ui.query)url.searchParams.set('q',ui.query);try{await navigator.clipboard.writeText(url.href);toast('Link copied.');}catch{toast('Copy is unavailable in this browser.',true);}}));
    for(const mark of output.querySelectorAll('.archive-hit')){const parent=mark.parentNode;mark.replaceWith(...mark.childNodes);parent.normalize();}
    AgentReader.reset(output);AgentReader.update(output,'',value.messages,true,true,{follow:false});
    let target=anchor&&value.messages.find(message=>message.id===anchor);
    if(target)AgentReader.reveal(output,target.role+':'+target.id);
    HubMarkdown.enhance(output);
    for(const message of value.messages)parts(message);
    if(value.later)bottom.append(button('Newer messages ↓',()=>page('after',value.messages.at(-1).id)));
    pageStatus.textContent=[value.missing?'Saved copy · source log removed':'',value.skipped?value.skipped+' records could not be indexed':''].filter(Boolean).join(' · ');pageStatus.hidden=!pageStatus.textContent;
    if(!value.messages.length)output.replaceChildren(text('p','No visible messages in this page.','archive-empty'));
    HubFocus.decorate();
    requestAnimationFrame(()=>{
      if(epoch!==ui.pageEpoch||state.view!=='archive')return;
      const node=target?[...output.querySelectorAll('[data-message-id]')].find(node=>node.dataset.messageId===target.role+':'+target.id):null;
      if(node){const match=highlightMatch(node);match.scrollIntoView({block:'start',behavior:'instant'});node.classList.add('archive-match');setTimeout(()=>node.classList.remove('archive-match'),1800);}else reader.scrollTop=0;
      if(!drawer.open)reader.focus({preventScroll:true});
    });
  }
  async function conversation(item,direction='',anchor=item.message_id,offset=item.offset || 0){
    enter();
    if(ui.page && (ui.page.session!==item.session || ui.page.host!==item.host)){stopMedia();ui.page=null;AgentReader.reset(output);output.replaceChildren();top.replaceChildren();bottom.replaceChildren();}
    cancelPage();const epoch=ui.pageEpoch;ui.match=item;reader.hidden=false;pageStatus.hidden=false;pageStatus.textContent='Opening message…';HubFocus.decorate();
    try{
      const value=await request('/api/archive/conversation',{host:item.host,session:item.session,message:anchor,direction,generation:item.generation,offset},'page');
      if(epoch!==ui.pageEpoch||state.view!=='archive')return;
      renderPage(value,anchor);
    }catch(error){if(error.name!=='AbortError'&&epoch===ui.pageEpoch){pageStatus.hidden=false;pageStatus.textContent=error.message;}}
  }
  function page(direction,anchor,offset=0){
    if(protectedReading()){toast('Pause playback or clear the selection to change pages.');return;}
    if(ui.match)conversation({...ui.match,generation:ui.page?.generation || ui.match.generation},direction,anchor,offset);
  }
  function back(){
    cancelPage();stopMedia();if(state.view==='archive')closeBuffer();
    if(ui.fromResearch){ui.fromResearch=false;HubFocus.decorate();return;}
    open(undefined,false);
  }
  toolbar.onsubmit=event=>{event.preventDefault();input.blur();search();};
  input.oninput=()=>{cancelSearch();ui.complete=false;ui.items=[];ui.result=null;results.replaceChildren();status.hidden=false;status.textContent=input.value.trim()?'Searching…':'Search messages or filenames.';ui.timer=setTimeout(()=>search(),300);};host.onchange=()=>search();
  drawer.addEventListener('close',()=>{if(!drawer.open)cancelSearch();});
  drawer.addEventListener('keydown',event=>{
    if(searchPanel.hidden||event.isComposing||event.ctrlKey||event.metaKey)return;
    if(event.key==='Escape'){event.preventDefault();event.stopPropagation();drawer.close();return;}
    const editing=event.target.closest('input,select,textarea');
    if(event.key==='ArrowLeft'&&(!editing||event.altKey)){event.preventDefault();event.stopPropagation();drawerBack.click();return;}
    if(event.altKey)return;
    if(event.key==='/'&&!editing){event.preventDefault();event.stopPropagation();input.focus();return;}
    const rows=[...results.querySelectorAll('button')],index=rows.indexOf(event.target.closest('button'));
    if((event.target===input&&event.key==='ArrowDown')||(index>=0&&['ArrowDown','ArrowUp','Home','End'].includes(event.key))){
      if(!rows.length)return;event.preventDefault();event.stopPropagation();
      const next=event.key==='Home'?0:event.key==='End'?rows.length-1:Math.max(0,Math.min(rows.length-1,index+(event.key==='ArrowUp'?-1:1)));
      rows[next].focus({preventScroll:true});rows[next].scrollIntoView({block:'nearest'});
    }
  });
  const updateParts=()=>requestAnimationFrame(()=>{if(state.view==='archive')for(const message of ui.page?.messages || [])parts(message);});
  output.addEventListener('toggle',event=>{if(event.target.matches('.reader-progress'))updateParts();},true);
  output.addEventListener('click',event=>{if(event.target.closest('[data-progress-page],[data-history-page]'))updateParts();});
  const originalRefresh=$('refresh-workspace').onclick;
  $('refresh-workspace').onclick=event=>state.view==='archive'?page('',ui.anchor,ui.offset):originalRefresh(event);
  window.HubArchive={open,back,agents,openSource:item=>{ui.fromResearch=state.view==='research';ui.query='';return conversation(item);},backLabel:()=>ui.fromResearch?'Back to research':'Back to history search',title:()=>ui.page?.title || ui.match?.title || 'History',host:()=>ui.match?.host,
    subtitle:()=>[ui.page?.host_label || ui.match?.host_label || ui.match?.host,ui.page?.runtime || ui.match?.runtime,'History'].filter(Boolean).join(' · ')};
  const link=new URL(location.href);
  if(link.searchParams.has('archive')){
    const pending=()=>{if(!state.snapshot)return setTimeout(pending,100);ui.query=link.searchParams.get('q')||'';input.value=ui.query;conversation({session:link.searchParams.get('archive'),host:link.searchParams.get('host') || '',message_id:link.searchParams.get('message') || '',generation:link.searchParams.get('generation') || '',offset:link.searchParams.get('offset') || 0});};pending();
  }
})();
