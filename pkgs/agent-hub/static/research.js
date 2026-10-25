/* A durable, editable research notebook, with every collected note tied to its source. */
(()=>{
  const icon=name=>`<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
  const view=document.createElement('section');view.id='research-view';view.className='research-view';view.hidden=true;
  view.innerHTML=`<div class="research-tools"><form id="research-search" role="search"><label class="search-box">${icon('search')}<input id="research-query" type="search" maxlength="240" placeholder="Find a lesson, paper, or experiment…" aria-label="Search research"></label></form><button id="research-hidden" class="text-button" aria-pressed="false">Hidden</button></div><nav id="research-projects" aria-label="Research projects"></nav><p id="research-status" role="status"></p><div id="research-scroll" tabindex="-1"><div id="research-notes"></div><button id="research-more" class="text-button" hidden>Older notes ${icon('arrow')}</button></div>`;
  document.querySelector('.workspace').append(view);viewIds.research=view.id;
  const menu=document.createElement('button');menu.id='focus-research';menu.innerHTML=icon('book')+'Research';menu.onclick=()=>open();$('focus-history').before(menu);
  const drawerLink=document.createElement('button');drawerLink.id='drawer-research';drawerLink.innerHTML=icon('book')+'<span>Research</span>'+icon('arrow');drawerLink.onclick=()=>open();$('agent-list-view').querySelector('.agent-list-controls').append(drawerLink);
  const editor=document.createElement('dialog');editor.id='research-editor';editor.setAttribute('aria-labelledby','research-edit-title');
  editor.innerHTML=`<form id="research-edit-form"><div class="edit-heading"><span id="research-edit-title">Edit research note</span><button type="button" id="research-edit-close" class="icon-button" aria-label="Close editor">${icon('close')}</button></div><label>Title<input name="title" maxlength="160" required></label><label>TLDR<textarea name="tldr" maxlength="1200" rows="3" required></textarea></label><label>Lesson<textarea name="lesson" maxlength="1200" rows="3"></textarea></label><div id="research-edit-examples"></div><label>My notes<textarea name="notes" maxlength="4000" rows="3" placeholder="What I want to remember…"></textarea></label><p id="research-edit-error" role="status"></p><div class="research-edit-actions"><button type="button" id="research-edit-cancel" class="text-button">Cancel</button><button type="submit" class="primary-button">Save</button></div></form>`;
  document.body.append(editor);
  const ui={query:'',project:'',hidden:false,items:[],result:null,epoch:0,controller:null,timer:null,editing:null,busy:false};
  const reader=$('research-scroll'),notes=$('research-notes'),status=$('research-status');
  const date=value=>{const d=new Date(value);return Number.isNaN(d.getTime())?'':d.toLocaleDateString([],{month:'short',day:'numeric',...(d.getFullYear()!==new Date().getFullYear()?{year:'numeric'}:{})});};
  const url=value=>{try{const u=new URL(value);return ['https:','http:'].includes(u.protocol)&&!u.username&&!u.password?u.href:'';}catch{return '';}};
  const markdown=value=>HubMarkdown.render(value || '');
  function card(item){
    const sources=item.sources || [],details=Boolean(item.lesson || item.examples?.length || item.notes || sources.length);
    const papers=(item.papers || []).filter(paper=>url(paper.url));
    return `<article class="research-note" data-research-id="${esc(item.id)}"><div class="research-meta"><span class="research-project">${esc(item.project?.label || 'Research')}</span><time datetime="${esc(item.created)}">${esc(date(item.created))}</time><details class="research-menu"><summary class="icon-button" aria-label="Actions for ${esc(item.title)}">${icon('more')}</summary><div><button data-research-edit="${esc(item.id)}">${icon('edit')}Edit</button><button data-research-hide="${esc(item.id)}">${item.hidden?'Restore':'Hide'}</button></div></details></div><h2>${esc(item.title)}</h2><div class="research-tldr rich-message">${markdown(item.tldr)}</div>${papers.length?`<ul class="research-papers">${papers.map(paper=>`<li><a href="${esc(url(paper.url))}" target="_blank" rel="noopener noreferrer">${icon('book')}<span>${esc(paper.title || new URL(paper.url).hostname)}</span>${icon('arrow')}</a>${paper.tldr?`<p>${esc(paper.tldr)}</p>`:''}</li>`).join('')}</ul>`:''}${details?`<details class="research-details"><summary>${item.lesson?'Lesson':''}${item.lesson && item.examples?.length?' & ':''}${item.examples?.length?'example'+(item.examples.length===1?'':'s'):''}${!item.lesson && !item.examples?.length?'Notes & sources':''}<span class="research-chevron" aria-hidden="true"></span></summary><div class="research-detail-body">${item.lesson?`<div class="rich-message">${markdown(item.lesson)}</div>`:''}${(item.examples || []).map((example,i)=>`<section class="research-example"><h3>Example${item.examples.length>1?' '+(i+1):''}</h3><div class="rich-message">${markdown(example)}</div></section>`).join('')}${item.notes?`<section><h3>My notes</h3><div class="rich-message">${markdown(item.notes)}</div></section>`:''}${sources.map(source=>`<blockquote class="research-excerpt"><p>${esc(source.excerpt || '')}</p><footer>${esc(source.title || 'Agent conversation')}${source.missing?' · Saved copy':''}${source.stale?' · Source changed':''}</footer></blockquote>`).join('')}</div></details>`:''}<div class="research-footer"><span>${esc(sources[0]?.host_label || sources[0]?.host || '')}${item.edited?' · Edited':item.summary_state==='excerpt'?' · Source excerpt':''}</span>${sources.map((source,index)=>`<button class="text-button" data-research-source="${esc(item.id)}" data-source-index="${index}">Conversation${sources.length>1?' '+(index+1):''} ${icon('arrow')}</button>`).join('')}</div></article>`;
  }
  function render(append=false){
    const existing=new Map([...notes.children].map(node=>[node.dataset.researchId,node]));
    for(const item of ui.items){
      const html=card(item),old=existing.get(item.id);
      if(old?._html===html){existing.delete(item.id);continue;}
      // Keep an expanded/selected note stable during background refresh.
      if(old && !append && (old.querySelector('details[open]') || old.contains(document.activeElement))){existing.delete(item.id);continue;}
      const template=document.createElement('template');template.innerHTML=html;const node=template.content.firstElementChild;node._html=html;
      if(old?.querySelector('.research-details[open]'))node.querySelector('.research-details')?.setAttribute('open','');
      if(old)old.replaceWith(node);else notes.append(node);existing.delete(item.id);HubMarkdown.enhance(node);
    }
    for(const node of existing.values())node.remove();
    for(const [index,item] of ui.items.entries()){
      const node=[...notes.children].find(node=>node.dataset.researchId===item.id);
      if(node && notes.children[index]!==node)notes.insertBefore(node,notes.children[index] || null);
    }
    if(!ui.items.length){notes.innerHTML=`<p class="research-empty">${ui.hidden?'No hidden notes.':ui.query || ui.project?'No matching notes.':'Research notes will appear here as your conversations are collected. Papers, lessons, and examples stay linked to their sources.'}</p>`;}
    $('research-more').hidden=!ui.result?.next;
    const projects=ui.result?.projects || [];
    const markup=[{id:'',label:'All projects'},...projects].map(project=>`<button type="button" data-research-project="${esc(project.id)}" aria-pressed="${project.id===ui.project}">${esc(project.label)}</button>`).join('');
    replaceMarkup($('research-projects'),markup,'data-research-project');
    report();
  }
  function report(){
    if(ui.result?.available===false){status.textContent='Open the controller hub to view your research archive.';status.dataset.collecting='false';notes.replaceChildren();return;}
    const value=ui.result?.status || {},offline=(value.unavailable || []).map(host=>host.label || host.host);
    status.textContent=offline.length?'Saved notes · '+offline.join(', ')+' unavailable':value.indexing?'Collecting earlier research…':value.collecting?'Collecting research…':value.pending?'Summaries will update in the background.':'Collected from your conversations · edit or hide any note';
    status.dataset.collecting=String(Boolean(value.collecting));
  }
  async function load({more=false,quiet=false}={}){
    const epoch=++ui.epoch;ui.controller?.abort();const controller=ui.controller=new AbortController();
    if(!quiet){status.textContent=more?'Loading older notes…':'Loading research…';status.dataset.collecting='true';}
    $('research-more').disabled=true;
    try{
      const params=new URLSearchParams({q:ui.query,project:ui.project,hidden:ui.hidden?'1':'0'});
      if(more && ui.result?.next)params.set('before',JSON.stringify(ui.result.next));
      const response=await fetch('/api/research?'+params,{cache:'no-store',signal:controller.signal});
      const value=await response.json();if(!response.ok)throw Error(value.error || 'Research is unavailable');
      if(epoch!==ui.epoch || state.view!=='research')return;
      ui.result=value;ui.items=more?[...ui.items,...value.items.filter(item=>!ui.items.some(old=>old.id===item.id))]:value.items;
      render(more);
    }catch(error){if(error.name!=='AbortError' && epoch===ui.epoch){status.textContent=error.message+' · Tap refresh to retry.';status.dataset.collecting='false';}}
    finally{if(epoch===ui.epoch)$('research-more').disabled=false;}
  }
  function open(){
    $('agent-drawer').close();document.querySelector('.workspace-menu').open=false;
    if(state.view!=='research'){HubFocus.leaveConversation();openBuffer('research');}
    HubFocus.decorate();load({quiet:Boolean(ui.result)});
  }
  function filter(){
    ui.query=$('research-query').value.trim();ui.items=[];ui.result=null;notes.replaceChildren();reader.scrollTop=0;load();
  }
  async function change(value){return api('/api/research',value);}
  async function refresh(){
    try{await change({action:'refresh'});await load();}catch(error){status.textContent=error.message;}
  }
  function edit(item){
    ui.editing=item;const fields=$('research-edit-form').elements;
    for(const key of ['title','tldr','lesson','notes'])fields.namedItem(key).value=item[key] || '';
    $('research-edit-examples').innerHTML=(item.examples?.length?item.examples:['']).map((example,index)=>`<label>Example${index?' '+(index+1):''}<textarea data-example maxlength="1500" rows="3">${esc(example)}</textarea></label>`).join('');
    $('research-edit-error').textContent='';editor.showModal();fields.namedItem('title').focus();
  }
  notes.addEventListener('click',async event=>{
    const button=event.target.closest('button');if(!button)return;
    const id=button.dataset.researchEdit || button.dataset.researchHide || button.dataset.researchSource;
    const item=ui.items.find(item=>item.id===id);if(!item)return;
    if(button.dataset.researchSource){
      const source=item.sources[Number(button.dataset.sourceIndex)];
      HubArchive.openSource({...source,title:source.title || item.title});return;
    }
    button.closest('.research-menu')?.removeAttribute('open');
    if(button.dataset.researchEdit){edit(item);return;}
    button.disabled=true;
    try{
      await change({action:item.hidden?'restore':'hide',id});
      ui.items=ui.items.filter(note=>note.id!==id);render();
      toast(item.hidden?'Restored.':'Hidden.',false,{label:'Undo',run:async()=>{await change({action:item.hidden?'hide':'restore',id});await load();}});
    }catch(error){toast(error.message,true);button.disabled=false;}
  });
  notes.addEventListener('toggle',event=>{if(event.target.matches('.research-details') && event.target.open)HubMarkdown.enhance(event.target);},true);
  $('research-projects').onclick=event=>{const button=event.target.closest('[data-research-project]');if(button){ui.project=button.dataset.researchProject;filter();}};
  $('research-search').onsubmit=event=>{event.preventDefault();clearTimeout(ui.timer);filter();};
  $('research-query').oninput=()=>{clearTimeout(ui.timer);ui.epoch++;ui.controller?.abort();ui.timer=setTimeout(filter,250);};
  $('research-hidden').onclick=()=>{ui.hidden=!ui.hidden;$('research-hidden').setAttribute('aria-pressed',String(ui.hidden));filter();};
  $('research-more').onclick=()=>load({more:true});
  for(const id of ['research-edit-close','research-edit-cancel'])$(id).onclick=()=>editor.close();
  $('research-edit-form').onsubmit=async event=>{
    event.preventDefault();if(ui.busy || !ui.editing)return;
    ui.busy=true;const button=event.submitter;button.disabled=true;
    const fields=event.target.elements,changes=Object.fromEntries(['title','tldr','lesson','notes'].map(key=>[key,fields.namedItem(key).value]));
    changes.examples=[...$('research-edit-examples').querySelectorAll('textarea')].map(node=>node.value.trim()).filter(Boolean);
    try{
      await change({action:'edit',id:ui.editing.id,changes});
      Object.assign(ui.editing,changes,{edited:true});render(true);editor.close();
    }catch(error){$('research-edit-error').textContent=error.message;}
    finally{ui.busy=false;button.disabled=false;}
  };
  editor.addEventListener('cancel',event=>{if(ui.busy)event.preventDefault();});
  const originalRefresh=$('refresh-workspace').onclick;
  $('refresh-workspace').onclick=event=>state.view==='research'?refresh():originalRefresh(event);
  // Reuse cached cards while collecting; do not reorder a page being read.
  setInterval(()=>{const selection=window.getSelection();if(state.view==='research' && !document.hidden && !editor.open && reader.scrollTop<40 && !notes.querySelector('details[open]') && !(selection && !selection.isCollapsed && reader.contains(selection.anchorNode)))load({quiet:true});},20000);
  window.HubResearch={open,refresh};
})();
