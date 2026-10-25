import {localFilePath} from './links.mjs';
import {localAsset} from './assets.mjs';

const formats=new Set('png jpg jpeg webp gif svg mp4 webm ogv mov pdf csv tsv json jsonl ndjson html txt md log ipynb'.split(' '));
export function candidate(href,label=''){
  if(typeof href!=='string')return null;
  const path=localFilePath(href),name=path?.split('/').at(-1),extension=name?.split('.').at(-1).toLowerCase();
  return path&&formats.has(extension)?{href,path,name,label:label.trim()||name,visual:/^(png|jpg|jpeg|webp|gif|svg|mp4|webm|ogv|mov)$/.test(extension)}:null;
}
function inlineAssets(children){
  const items=[];
  for(let i=0;i<children.length;i++){
    const token=children[i];
    if(['softbreak','hardbreak'].includes(token.type)||token.type==='text'&&!token.content.trim())continue;
    let item;
    if(token.type==='image')item=candidate(token.attrGet('src')||'',token.content);
    else if(token.type==='link_open'){
      const text=[];let closed=false;
      for(i++;i<children.length;i++){
        if(children[i].type==='link_close'){closed=true;break;}
        if(!['text','code_inline','image','em_open','em_close','strong_open','strong_close'].includes(children[i].type))return null;
        if(children[i].content)text.push(children[i].content);
      }
      if(!closed)return null;
      item=candidate(token.attrGet('href')||'',text.join(''));
    }else return null;
    if(!item)return null;items.push(item);
  }
  return items.length?items:null;
}
function block(tokens,index){
  const token=tokens[index];if(!token||token.level!==0)return null;
  const paragraph=i=>tokens[i]?.type==='paragraph_open'&&tokens[i+1]?.type==='inline'&&tokens[i+2]?.type==='paragraph_close'?inlineAssets(tokens[i+1].children||[]):null;
  if(token.type==='paragraph_open'){
    const items=paragraph(index);return items?{items,end:index+3}:null;
  }
  if(token.type==='bullet_list_open'){
    const items=[];let i=index+1;
    while(tokens[i]?.type==='list_item_open'){
      const found=paragraph(i+1);
      if(!found||tokens[i+4]?.type!=='list_item_close')return null;
      items.push(...found);i+=5;
    }
    if(tokens[i]?.type==='bullet_list_close'&&items.length)return {items,end:i+1};
  }
  return null;
}
export function artifactMarkdown(md){
  md.core.ruler.after('local_files','artifact_groups',state=>{
    if(state.env.filePreview)return;
    const output=[];
    for(let index=0;index<state.tokens.length;){
      const items=[];let end=index,next;
      while((next=block(state.tokens,end))&&items.length+next.items.length<=24){items.push(...next.items);end=next.end;}
      if(items.length<2){output.push(state.tokens[index++]);continue;}
      const token=new state.Token('artifact_group','section',0);token.block=true;
      const unique=new Map();for(const item of items)if(!unique.has(item.path))unique.set(item.path,item);
      token.meta=[...unique.values()];output.push(token);index=end;
    }
    state.tokens=output;
  });
  const escape=md.utils.escapeHtml;
  md.renderer.rules.artifact_group=(tokens,index)=>{
    const items=tokens[index].meta,visual=items.filter(item=>item.visual).length;
    return '<section class="artifact-group" data-artifact-group><div class="artifact-heading"><span>'+items.length+' '+(items.length===1?'file':'files')+'</span>'+
      (visual>1?'<button type="button" data-artifact-compare>Compare</button>':'')+'</div><div class="artifact-grid">'+items.map((item,index)=>
        `<a class="artifact-card" href="${escape(item.href)}" data-artifact-href="${escape(item.href)}" data-artifact-index="${index}" target="_blank" rel="noopener noreferrer"${index>=4?' hidden':''}><span class="artifact-thumb" aria-hidden="true"></span><span class="artifact-label">${escape(item.label)}</span><span class="artifact-name"${item.label===item.name?' hidden':''}>${escape(item.name)}</span></a>`).join('')+'</div>'+
      (items.length>4?`<button type="button" class="artifact-more" data-artifact-more>Show ${items.length-4} more</button>`:'')+'</section>\n';
  };
}

export function createArtifactReview(attachPreview){
  const bound=new WeakMap();let active=null;
  const close=()=>{if(active)active.dialog.close();};
  const text=(tag,value,className)=>{const node=document.createElement(tag);node.textContent=value;if(className)node.className=className;return node;};
  function compare(group,button){
    const choices=[...group.querySelectorAll('[data-artifact-href]')].map(link=>bound.get(link)).filter(asset=>asset&&['image','video'].includes(asset.kind));
    if(choices.length<2)return;
    close();const dialog=document.createElement('dialog');dialog.className='artifact-compare';dialog.setAttribute('aria-label','Compare files');
    const header=document.createElement('header'),done=text('button','Close');done.type='button';done.onclick=()=>dialog.close();
    header.append(text('strong','Compare'),done);
    const tabs=document.createElement('div');tabs.className='compare-tabs';tabs.setAttribute('role','group');tabs.setAttribute('aria-label','Visible file');
    const panels=document.createElement('div');panels.className='compare-panels';const selected=[0,1];let visible=0;
    const clear=panel=>{for(const video of panel.querySelectorAll('video')){video.pause();video.removeAttribute('src');video.load();}};
    const tabButtons=[],slots=[];
    function choose(index){
      for(let i=0;i<2;i++){slots[i].dataset.active=String(i===index);tabButtons[i].setAttribute('aria-pressed',String(i===index));}
      if(matchMedia('(max-width:650px)').matches)slots[visible].querySelector('video')?.pause();
      visible=index;
    }
    function fill(index){
      const panel=slots[index],asset=choices[selected[index]];clear(panel);panel.replaceChildren();
      const heading=document.createElement('div');heading.className='compare-file-heading';
      const select=document.createElement('select');select.setAttribute('aria-label',index?'Second file':'First file');
      choices.forEach((choice,i)=>{const option=text('option',choice.label===choice.name?choice.name:choice.label+' · '+choice.name);option.value=String(i);select.append(option);});select.value=String(selected[index]);
      select.onchange=()=>{selected[index]=Number(select.value);fill(index);};
      const original=text('a','Open');original.href=asset.file;original.target='_blank';original.rel='noopener noreferrer';original.setAttribute('aria-label','Open original '+asset.name);
      heading.append(select,original);panel.append(heading);
      const media=document.createElement(asset.kind==='video'?'video':'img');
      if(asset.kind==='video'){media.controls=true;media.playsInline=true;media.preload='none';media.muted=true;media.src=asset.file;}
      else{media.alt=asset.label;media.decoding='async';media.src=asset.file+'&preview=1';}
      media.onerror=()=>{media.hidden=true;panel.append(text('p','Preview unavailable. Open the original file.','compare-error'));};
      panel.append(media);tabButtons[index].textContent=(index?'B · ':'A · ')+asset.name;
    }
    for(let i=0;i<2;i++){
      const tab=text('button','');tab.type='button';tab.onclick=()=>choose(i);tabButtons.push(tab);tabs.append(tab);
      const panel=document.createElement('section');panel.className='compare-panel';slots.push(panel);panels.append(panel);
    }
    dialog.append(header,tabs,panels);document.body.append(dialog);
    active={group,dialog};fill(0);fill(1);choose(0);
    const observer=new MutationObserver(()=>{if(!group.isConnected)dialog.close();});
    observer.observe(document.body,{childList:true,subtree:true});
    dialog.onclose=()=>{observer.disconnect();slots.forEach(clear);dialog.remove();if(active?.dialog===dialog)active=null;if(button.isConnected)button.focus({preventScroll:true});};
    dialog.addEventListener('keydown',event=>{if(!event.target.matches('select')&&['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();choose(event.key==='ArrowLeft'?0:1);}});
    dialog.showModal();
  }
  function enhance(root){
    if(active&&!active.group.isConnected)close();
    for(const group of root.querySelectorAll('[data-artifact-group]')){
      const agentId=group.closest('[data-agent-id]')?.dataset.agentId;
      if(!agentId){
        const fragment=document.createDocumentFragment();
        for(const link of group.querySelectorAll('[data-artifact-href]')){const p=document.createElement('p'),a=text('a',link.querySelector('.artifact-label').textContent);a.href=link.dataset.artifactHref;p.append(a);fragment.append(p);}
        group.replaceWith(fragment);
        continue;
      }
      for(const link of group.querySelectorAll('[data-artifact-href]')){
        const asset=localAsset(link.dataset.artifactHref,agentId);if(!asset)continue;
        link.dataset.kind=asset.kind;
        asset.label=link.querySelector('.artifact-label').textContent;
        bound.set(link,asset);link.dataset.linkReady='true';attachPreview(link,link.dataset.artifactHref,agentId);
        const thumb=link.querySelector('.artifact-thumb');
        if(['image','video','document'].includes(asset.kind)){
          if(!thumb.firstChild){const img=document.createElement('img');img.loading='lazy';img.decoding='async';img.alt='';img.width=640;img.height=400;
            img.src=asset.thumbnail||asset.preview;img.onerror=()=>{thumb.textContent=asset.kind==='video'?'Video':'Image';};thumb.append(img);
            if(asset.kind==='video')thumb.append(text('span','▶','artifact-play'));
          }
        }else if(!thumb.textContent)thumb.textContent=asset.extension.toUpperCase();
      }
      if(!group._artifactBound){
        group._artifactBound=true;
        group.addEventListener('click',event=>{
          const button=event.target.closest('button');if(!button)return;
          if(button.hasAttribute('data-artifact-compare'))compare(group,button);
          if(button.hasAttribute('data-artifact-more')){
            const expanding=button.getAttribute('aria-expanded')!=='true';button.setAttribute('aria-expanded',String(expanding));
            for(const link of group.querySelectorAll('[data-artifact-index]'))if(Number(link.dataset.artifactIndex)>=4)link.hidden=!expanding;
            button.textContent=expanding?'Show fewer':'Show '+(group.querySelectorAll('[data-artifact-index]').length-4)+' more';
          }
        });
      }
    }
  }
  return {enhance,isOpenFor:root=>Boolean(active&&root.contains(active.group))};
}
