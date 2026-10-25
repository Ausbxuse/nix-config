/* Image drafts stay with their conversation. Only opaque upload IDs are sent. */
((root)=>{
  const drafts=new Map(),busy=new Set();
  const closeIcon='<svg class="icon" aria-hidden="true"><use href="#i-close"/></svg>';
  let tray,button,picker,dialog,activeKey='',dbPromise;
  const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const key=input=>input?.id==='agent-message'?'agent:'+state.selected:'supervisor:'+(state.context||'');
  const current=()=>document.getElementById('focus-composers')?.dataset.mode==='agent'?$('agent-message'):$('supervisor-input');
  const items=target=>drafts.get(target)||[];
  const url=image=>image.url?.startsWith('blob:')?image.url:'/api/image?'+new URLSearchParams({host:image.host,id:image.id});
  const valid=image=>image&&(/^[a-f0-9]{32}$/.test(image.id)&&typeof image.host==='string'||image.url?.startsWith('blob:'));
  function html(images){
    const values=(images||[]).filter(valid).slice(0,4);
    return values.length?'<div class="message-images">'+values.map(image=>`<button type="button" class="message-image" data-image-preview="${escape(url(image))}" aria-label="View ${escape(image.name||'image')}"><img src="${escape(url(image))}" alt="${escape(image.name||'Attached image')}" loading="lazy" decoding="async" width="${Number(image.width)||96}" height="${Number(image.height)||72}"></button>`).join('')+'</div>':'';
  }
  function database(){
    return dbPromise??=new Promise((resolve,reject)=>{
      const request=indexedDB.open('hub-image-drafts',1);
      request.onupgradeneeded=()=>request.result.createObjectStore('images',{keyPath:'id'});
      request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);
    });
  }
  async function persist(item,remove=false){
    try{
      const db=await database(),store=db.transaction('images','readwrite').objectStore('images');
      if(remove)store.delete(item.id);else store.put({id:item.id,key:item.key,blob:item.blob,name:item.name,width:item.width,height:item.height,at:item.at});
    }catch{/* Private browsing can deny storage; keep the in-memory draft. */}
  }
  async function restore(){
    try{
      const db=await database(),request=db.transaction('images').objectStore('images').getAll();
      request.onsuccess=()=>{
        for(const item of request.result){
          if(!item.blob||Date.now()-item.at>7*86400000||items(item.key).length>=4){persist(item,true);continue;}
          if(items(item.key).some(i=>i.id===item.id))continue;
          item.url=URL.createObjectURL(item.blob);item.uploads=new Map();item.state='ready';
          drafts.set(item.key,[...items(item.key),item]);
        }
        render();updateDetailMeta();
      };
    }catch{}
  }
  async function resize(file){
    if(!['image/png','image/jpeg','image/webp','image/gif'].includes(file.type))throw new Error('Choose a PNG, JPEG, WebP, or GIF image.');
    if(file.size>25*1024*1024)throw new Error('Choose an image smaller than 25 MB.');
    const bitmap=await createImageBitmap(file);
    try{
      if(bitmap.width*bitmap.height>60_000_000)throw new Error('Resize this image before attaching it.');
      const scale=Math.min(1,2048/Math.max(bitmap.width,bitmap.height)),canvas=document.createElement('canvas');
      canvas.width=Math.max(1,Math.round(bitmap.width*scale));canvas.height=Math.max(1,Math.round(bitmap.height*scale));
      const format=file.type==='image/png'?'image/png':'image/jpeg';
      for(let attempt=0;attempt<8;attempt++){
        canvas.getContext('2d').drawImage(bitmap,0,0,canvas.width,canvas.height);
        const blob=await new Promise(resolve=>canvas.toBlob(resolve,format,.9));
        if(!blob)throw new Error('Could not prepare this image.');
        if(blob.size<=3*1024*1024)return {blob,width:canvas.width,height:canvas.height};
        canvas.width=Math.max(1,Math.floor(canvas.width*.8));canvas.height=Math.max(1,Math.floor(canvas.height*.8));
      }
      throw new Error('This image is too large. Resize it before attaching.');
    }finally{bitmap.close();}
  }
  async function add(files,target=key(current())){
    for(const file of files){
      if(items(target).length>=4){toast('Attach up to four images per message.',true);break;}
      if([...drafts.values()].flat().length>=32){toast('Send or remove some image drafts before adding more.',true);break;}
      const item={id:crypto.randomUUID().replaceAll('-',''),key:target,name:file.name||'Pasted image',at:Date.now(),state:'preparing',uploads:new Map()};
      drafts.set(target,[...items(target),item]);render();
      try{
        Object.assign(item,await resize(file));item.url=URL.createObjectURL(item.blob);item.state='ready';
        if(items(target).includes(item))await persist(item);else URL.revokeObjectURL(item.url);
      }catch(error){item.state='error';item.error=error.message||'Could not open this image.';toast(item.error,true);}
      render();updateDetailMeta();
    }
  }
  function capture(input){
    if(!input)return {key:'',items:[]};
    const target=key(input);
    return {key:target,items:[...items(target)]};
  }
  async function restoreAttachments(images,input,unchanged=()=>true){
    const target=key(input),destination=input.id==='agent-message'?state.selected:'supervisor';
    if(!images.length)return;
    if(busy.has(target))throw new Error('Wait for the current image operation.');
    const existing=items(target);
    if(existing.length===images.length&&existing.every((item,index)=>{
      const uploaded=item.uploads.get(destination);return item.id===images[index].id||uploaded?.id===images[index].id&&uploaded.host===images[index].host;
    }))return;
    if(existing.length)throw new Error('Your draft already has images. Remove them before restoring this message.');
    if(images.length>4||images.some(image=>!valid(image)||!image.host))throw new Error('These attachments cannot be restored. Attach them again.');
    if([...drafts.values()].flat().length+images.length>32)throw new Error('Send or remove some image drafts before restoring more.');
    busy.add(target);render();
    try{
      const blobs=await Promise.all(images.map(async image=>{
        const response=await fetch(url(image),{signal:AbortSignal.timeout(15000)});
        if(!response.ok)throw new Error('Could not restore this image. The original message is kept.');
        const blob=await response.blob();
        if(!blob.type.startsWith('image/')||blob.size>3*1024*1024)throw new Error('Could not restore this image. The original message is kept.');
        return blob;
      }));
      if(key(input)!==target||!unchanged())throw new Error('Your conversation or draft changed. The original message is kept.');
      if(items(target).length)throw new Error('Your new images are kept. The original message is still available.');
      const restored=images.map((image,index)=>({id:crypto.randomUUID().replaceAll('-',''),key:target,
        name:image.name||'Attached image',width:image.width,height:image.height,at:Date.now(),state:'ready',
        blob:blobs[index],url:URL.createObjectURL(blobs[index]),uploads:new Map([[destination,image]])}));
      drafts.set(target,restored);for(const item of restored)persist(item);
    }finally{busy.delete(target);render();updateDetailMeta();}
  }
  async function upload(batch,target){
    if(!batch.items.length)return [];
    const failure=message=>Object.assign(new Error(message),{status:400});
    if(busy.has(batch.key))throw failure('These images are already being sent.');
    if(batch.items.some(item=>item.state!=='ready'))throw failure('Wait for the images to load, or remove the failed image.');
    busy.add(batch.key);render();
    try{
      const result=[];
      for(const item of batch.items){
        let uploaded=item.uploads.get(target);
        if(!uploaded){
          const response=await fetch('/api/images?'+new URLSearchParams({target,name:item.name}),{method:'POST',headers:{'Content-Type':item.blob.type,'X-Hub-Token':state.snapshot?.csrf_token||''},body:item.blob,signal:AbortSignal.timeout(45000)});
          const value=await response.json();
          if(!response.ok)throw new Error(value.error||'Image upload failed.');
          if(!valid(value))throw new Error('Image upload was not confirmed.');
          item.uploads.set(target,value);uploaded=value;
        }
        result.push(uploaded);
      }
      return result;
    }catch(error){error.status ||= 400;throw error;}
    finally{busy.delete(batch.key);render();}
  }
  function clear(batch){
    const ids=new Set(batch.items.map(item=>item.id));
    drafts.set(batch.key,items(batch.key).filter(item=>!ids.has(item.id)));
    for(const item of batch.items){persist(item,true);if(item.url)URL.revokeObjectURL(item.url);}
    render();
  }
  function render(){
    if(!tray)return;
    activeKey=key(current());
    const list=items(activeKey);
    tray.hidden=!list.length;
    button.disabled=busy.has(activeKey);
    button.classList.toggle('has-images',Boolean(list.length));
    const markup=list.map(item=>`<div class="image-draft${item.state==='error'?' image-error':''}">${item.url?`<button type="button" data-image-preview="${escape(item.url)}" aria-label="Preview ${escape(item.name)}"><img src="${escape(item.url)}" alt="${escape(item.name)}"></button>`:'<span class="image-loading" aria-label="Preparing image"></span>'}<button type="button" class="image-remove" data-remove-image="${item.id}" aria-label="Remove ${escape(item.name)}"${busy.has(activeKey)?' disabled':''}>${closeIcon}</button><span class="image-draft-status">${busy.has(activeKey)?'Uploading…':item.state==='error'?'Try another image':item.state==='preparing'?'Preparing…':''}</span></div>`).join('');
    if(tray._markup!==markup){tray.innerHTML=markup;tray._markup=markup;}
  }
  function mount(composer){
    button=document.createElement('button');button.id='attach-image';button.type='button';button.className='icon-button';button.title='Attach images';button.setAttribute('aria-label','Attach images');
    button.innerHTML='<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m8 12 6.2-6.2a3.5 3.5 0 0 1 5 5l-8.5 8.5a5 5 0 0 1-7.1-7.1L12 3.8M7 14l7.5-7.5a1.5 1.5 0 0 1 2.1 2.1l-8 8"/></svg>';
    composer.querySelector('.composer-caption').append(button);
    tray=document.createElement('div');tray.id='image-drafts';tray.hidden=true;tray.setAttribute('aria-label','Attached image drafts');
    composer.querySelector('.composer-caption').after(tray);
    picker=document.createElement('input');picker.id='image-picker';picker.type='file';picker.accept='image/png,image/jpeg,image/webp,image/gif';picker.multiple=true;picker.hidden=true;composer.append(picker);
    let pickedKey='';
    button.onclick=()=>{pickedKey=key(current());picker.click();};
    picker.onchange=()=>{const files=[...picker.files];picker.value='';add(files,pickedKey);};
    tray.onclick=event=>{
      const remove=event.target.closest('[data-remove-image]');if(!remove)return;
      const item=items(activeKey).find(i=>i.id===remove.dataset.removeImage);
      if(item&&!busy.has(activeKey))clear({key:activeKey,items:[item]});
      updateDetailMeta();
    };
    composer.addEventListener('paste',event=>{
      const files=[...(event.clipboardData?.items||[])].filter(item=>item.kind==='file'&&item.type.startsWith('image/')).map(item=>item.getAsFile()).filter(Boolean);
      if(files.length){event.preventDefault();add(files);}
    });
    composer.addEventListener('dragover',event=>{if(event.dataTransfer.types.includes('Files')){event.preventDefault();composer.classList.add('image-drop');}});
    composer.addEventListener('dragleave',event=>{if(!composer.contains(event.relatedTarget))composer.classList.remove('image-drop');});
    composer.addEventListener('drop',event=>{composer.classList.remove('image-drop');if(event.dataTransfer.files.length){event.preventDefault();add([...event.dataTransfer.files]);}});
    document.addEventListener('click',event=>{
      const preview=event.target.closest('[data-image-preview]');if(!preview)return;
      const src=preview.dataset.imagePreview;
      if(!src.startsWith('blob:')&&!/^\/api\/image\?/.test(src))return;
      if(!dialog){
        dialog=document.createElement('dialog');dialog.id='image-lightbox';dialog.setAttribute('aria-label','Image preview');
        dialog.innerHTML=`<button type="button" class="icon-button" aria-label="Close image preview" autofocus>${closeIcon}</button><div class="image-stage"><img alt="Attached image"><span class="image-loading" role="status" aria-label="Loading image"></span><p class="image-view-error" role="status" hidden>Image unavailable.</p></div>`;
        document.body.append(dialog);dialog.firstElementChild.onclick=()=>dialog.close();
        const img=dialog.querySelector('img'),loading=dialog.querySelector('.image-loading'),error=dialog.querySelector('.image-view-error');
        img.onload=()=>{loading.hidden=true;img.hidden=false;};
        img.onerror=()=>{loading.hidden=true;img.hidden=true;error.hidden=false;};
        dialog.addEventListener('click',event=>{if(event.target===dialog||event.target.classList.contains('image-stage'))dialog.close();});
        dialog.addEventListener('close',()=>{img.removeAttribute('src');img.hidden=true;loading.hidden=true;error.hidden=true;});
      }
      const img=dialog.querySelector('img');img.hidden=true;img.alt=preview.querySelector('img')?.alt||'Attached image';
      dialog.querySelector('.image-loading').hidden=false;dialog.querySelector('.image-view-error').hidden=true;
      img.src=src;dialog.showModal();
    });
    restore();render();
  }
  root.HubImages={mount,render,html,capture,upload,clear,restoreAttachments,has:input=>Boolean(input&&items(key(input)).length),metadata:batch=>batch.items.map(({id,name,url,width,height})=>({id,name,url,width,height}))};
})(window);
