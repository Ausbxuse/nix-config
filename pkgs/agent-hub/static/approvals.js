/* Decisions refer to a verified request, never a terminal prompt or guessed key. */
((root)=>{
  const labels={command:'Run command',stdin:'Send command input',network:'Network access',files:'File changes',permissions:'Additional access'};
  function mount(node,{send,refresh}){
    let agent='',value=null,connected=true,busy=false,epoch=0;
    const rows=new Map();
    node.className='agent-approvals';node.hidden=true;node.setAttribute('aria-label','Pending approvals');
    function select(id){
      if(agent===id)return;
      agent=id;epoch++;value=null;busy=false;connected=true;rows.clear();node.replaceChildren();node.hidden=true;
    }
    function text(tag,content,className){
      const child=document.createElement(tag);child.textContent=content;if(className)child.className=className;return child;
    }
    function code(content,command=false){
      const pre=document.createElement('pre'),inner=document.createElement('code');pre.className='approval-code';
      if(command&&root.HubMarkdown?.highlightTerminal)inner.innerHTML=root.HubMarkdown.highlightTerminal(content,'command');
      else inner.textContent=content;
      pre.append(inner);return pre;
    }
    function make(item){
      const row=document.createElement('article');row.className='approval-request';row.dataset.approvalId=item.id;row.dataset.digest=item.digest;
      row.append(text('h3',labels[item.kind]||'Approval'));
      if(item.reason)row.append(text('p',item.reason));
      if(item.cwd)row.append(text('p',item.cwd,'approval-path'));
      if(item.environment)row.append(text('p','Environment: '+item.environment,'approval-path'));
      if(item.command)row.append(code(item.command,true));
      for(const change of item.changes||[]){
        const file=document.createElement('details');file.className='approval-file';
        file.append(text('summary',change.path+' · '+change.kind.type+(change.kind.move_path?' → '+change.kind.move_path:'')));
        const diff=document.createElement('pre');diff.className='approval-code approval-diff';
        for(const line of change.diff.split('\n'))diff.append(text('span',line+'\n',line.startsWith('+')?'diff-add':line.startsWith('-')?'diff-remove':''));
        file.append(diff);row.append(file);
      }
      for(const [title,details] of [['Network',item.network],['Requested access',item.access]]){
        if(details){row.append(text('h4',title),code(JSON.stringify(details,null,2)));}
      }
      if(item.note)row.append(text('p',item.note,'approval-note'));
      const controls=document.createElement('div');controls.className='approval-controls';
      for(const decision of item.decisions){
        const button=text('button',decision==='accept'?(item.scope==='turn'?'Allow for this turn':'Allow once'):decision==='cancel'?'Stop turn':'Decline');
        button.type='button';button.dataset.decision=decision;controls.append(button);
      }
      row.append(controls);const note=text('p','','approval-note');note.setAttribute('role','status');note.hidden=true;row.append(note);
      return row;
    }
    function render(){
      const items=value?.items||[],available=value?.status==='available'&&connected;
      const keep=new Set();
      for(const item of items){
        let row=rows.get(item.id);keep.add(item.id);
        if(!row||row.dataset.digest!==item.digest){
          const next=make(item);if(row)row.replaceWith(next);else node.append(next);rows.set(item.id,next);row=next;
        }
        for(const button of row.querySelectorAll('button'))button.disabled=busy||!available;
        const note=row.querySelector('[role=status]');
        if(!available&&!note.dataset.error){note.textContent='Reconnecting. Refresh before deciding.';note.hidden=false;note.dataset.connection='lost';}
        else if(note.dataset.connection){note.hidden=true;delete note.dataset.connection;}
      }
      for(const [id,row] of rows)if(!keep.has(id)){row.remove();rows.delete(id);}
      node.hidden=!rows.size;
    }
    async function decide(id,decision){
      const item=value?.items.find(item=>item.id===id);
      if(busy||!connected||value?.status!=='available'||!item?.decisions.includes(decision))return;
      const selected=agent,generation=epoch,thread=value.thread_id;busy=true;render();
      const note=rows.get(id).querySelector('[role=status]');note.textContent='Sending decision…';note.hidden=false;
      try{
        await send({type:'approval_respond',agent_id:selected,thread_id:thread,approval_id:id,approval_digest:item.digest,decision});
        if(generation===epoch)value={...value,items:value.items.filter(item=>item.id!==id)};
      }catch(error){
        if(generation===epoch){connected=false;note.textContent=error.message;note.hidden=false;note.dataset.error='true';}
      }finally{
        if(generation===epoch){busy=false;render();await refresh();}
      }
    }
    node.addEventListener('click',event=>{
      const button=event.target.closest('button[data-decision]');
      if(button&&!button.disabled)decide(button.closest('[data-approval-id]').dataset.approvalId,button.dataset.decision);
    });
    return {select,hasItems:()=>Boolean(value?.items?.length),
      update(id,next){select(id);value=next?.status==='available'?next:{...value,status:'unavailable'};
        if(next?.status==='available')for(const note of node.querySelectorAll('[data-error]')){delete note.dataset.error;note.hidden=true;}
        render();},
      setConnected(next){if(connected!==next){connected=next;render();}}};
  }
  root.HubApprovals={mount};
})(window);
