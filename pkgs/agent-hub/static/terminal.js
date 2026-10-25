/* Read-only tmux SGR rendering. Other terminal controls never become browser actions. */
((root)=>{
  const escape=text=>String(text).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const controls=/\x1b(?:\[[0-?]*[ -/]*(?:[@-~]|$)|[\]PX^_][\s\S]*?(?:\x07|\x1b\\|$)|[ -/]*[@-~])|\x9b[0-?]*[ -/]*(?:[@-~]|$)|[\x00-\x08\x0b-\x1f\x7f-\x9a\x9c-\x9f]/g;
  const byte=value=>Number.isInteger(value)&&value>=0&&value<=255;
  function indexed(value){
    if(!byte(value))return null;
    if(value<16)return `var(--ansi-${value})`;
    if(value>=232){const grey=8+10*(value-232);return `rgb(${grey},${grey},${grey})`;}
    const n=value-16,levels=[0,95,135,175,215,255];
    return `rgb(${levels[Math.floor(n/36)]},${levels[Math.floor(n/6)%6]},${levels[n%6]})`;
  }
  function color(values){
    if(values[0]===5)return indexed(values[1]);
    if(values[0]===2&&values.length===4&&values.slice(1).every(byte))return `rgb(${values.slice(1).join(',')})`;
    return null;
  }
  function sgr(state,parameters){
    const parts=parameters.split(';');
    for(let i=0;i<parts.length;i++){
      const sub=parts[i].split(':'),code=Number(sub[0]);
      if(code===0){for(const key of Object.keys(state))delete state[key];}
      else if(code===1)state.bold=true;
      else if(code===2)state.dim=true;
      else if(code===3)state.italic=true;
      else if(code===4)state.underline=sub.length>1?Number(sub[1]):1;
      else if(code===7)state.inverse=true;
      else if(code===8)state.hidden=true;
      else if(code===9)state.strike=true;
      else if(code===21)state.underline=2;
      else if(code===22){state.bold=false;state.dim=false;}
      else if(code===23)state.italic=false;
      else if(code===24)state.underline=0;
      else if(code===27)state.inverse=false;
      else if(code===28)state.hidden=false;
      else if(code===29)state.strike=false;
      else if(code>=30&&code<=37)state.fg=indexed(code-30);
      else if(code>=40&&code<=47)state.bg=indexed(code-40);
      else if(code>=90&&code<=97)state.fg=indexed(code-90+8);
      else if(code>=100&&code<=107)state.bg=indexed(code-100+8);
      else if([39,49,59].includes(code))delete state[{39:'fg',49:'bg',59:'underlineColor'}[code]];
      else if([38,48,58].includes(code)){
        let values;
        if(sub.length>1){
          values=sub.slice(1).map(Number);
          // ISO colon form can include the colour-space slot (empty or zero).
          if(values[0]===2&&values.length===5)values.splice(1,1);
        }else{
          const mode=Number(parts[++i]),count=mode===2?3:mode===5?1:0;
          values=[mode,...parts.slice(i+1,i+1+count).map(Number)];i+=count;
        }
        const value=color(values);if(value)state[{38:'fg',48:'bg',58:'underlineColor'}[code]]=value;
      }
    }
  }
  function style(state){
    let fg=state.fg,bg=state.bg;
    if(state.inverse)[fg,bg]=[bg||'var(--terminal-bg)',fg||'var(--terminal-text)'];
    if(state.dim)fg=`color-mix(in srgb,${fg||'var(--terminal-text)'} 50%,transparent)`;
    if(state.hidden)fg='transparent';
    const styles=[];
    if(fg)styles.push('color:'+fg);
    if(bg)styles.push('background-color:'+bg);
    if(state.bold)styles.push('font-weight:600');
    if(state.italic)styles.push('font-style:italic');
    if(state.underline||state.strike){
      styles.push('text-decoration-line:'+[(state.underline?'underline':''),(state.strike?'line-through':'')].filter(Boolean).join(' '));
      if(state.underline)styles.push('text-decoration-style:'+({2:'double',3:'wavy',4:'dotted',5:'dashed'}[state.underline]||'solid'));
      if(state.underlineColor)styles.push('text-decoration-color:'+state.underlineColor);
    }
    return styles.join(';');
  }
  function parse(input){
    const text=String(input),state={},rows=[];
    let row={text:'',html:''},offset=0,css='';
    function append(value){
      const parts=value.split('\n');
      for(let i=0;i<parts.length;i++){
        const part=parts[i];row.text+=part;
        if(part)row.html+=css?'<span style="'+css+'">'+escape(part)+'</span>':escape(part);
        if(i<parts.length-1){row.text+='\n';row.html+='\n';rows.push(row);row={text:'',html:''};}
      }
    }
    for(const match of text.matchAll(controls)){
      append(text.slice(offset,match.index));
      const sequence=match[0].match(/^(?:\x1b\[|\x9b)([0-9;:]*)m$/);
      if(sequence){sgr(state,sequence[1]);css=style(state);}
      offset=match.index+match[0].length;
    }
    append(text.slice(offset));rows.push(row);return rows;
  }
  const render=text=>parse(text).map(row=>row.html).join('');
  const plain=text=>parse(text).map(row=>row.text).join('');
  const capture=value=>typeof value==='object'&&value!==null?{ansi:String(value.ansi??''),theme:value.theme==='light'?'light':'dark'}:{ansi:String(value??''),theme:'dark'};
  function mount(node,{back,copy}){
    const wrapButton=document.getElementById('terminal-wrap'),copyButton=document.getElementById('terminal-copy');
    const phone=matchMedia('(max-width:800px)');
    let explicit=null,last=null,painted=null,rows=[],copyTimer;
    try{const value=localStorage.getItem('hub-terminal-wrap');if(value==='true'||value==='false')explicit=value==='true';}catch{}
    function wrap(){
      const atBottom=node.scrollHeight-node.scrollTop-node.clientHeight<60;
      const enabled=explicit??phone.matches;
      node.dataset.wrap=String(enabled);wrapButton.setAttribute('aria-pressed',String(enabled));
      wrapButton.title=enabled?'Keep original line widths':'Wrap long lines';
      if(enabled)node.scrollLeft=0;
      if(atBottom)node.scrollTop=node.scrollHeight;
    }
    document.getElementById('terminal-back').onclick=()=>{back();document.getElementById('composer-recipient')?.focus({preventScroll:true});};
    wrapButton.onclick=()=>{explicit=node.dataset.wrap!=='true';try{localStorage.setItem('hub-terminal-wrap',String(explicit));}catch{}wrap();};
    phone.addEventListener('change',()=>{if(explicit===null)wrap();});wrap();
    copyButton.onclick=async()=>{
      if(last===null)return;
      if(await copy(plain(last.ansi))){
        clearTimeout(copyTimer);copyButton.querySelector('use').setAttribute('href','#i-check');
        copyButton.setAttribute('aria-label','Copied terminal output');
        copyTimer=setTimeout(()=>{copyButton.querySelector('use').setAttribute('href','#i-copy');copyButton.setAttribute('aria-label','Copy terminal output');},1500);
      }
    };
    function update(value){
      last=capture(value);
      // Conversation polling never parses an invisible terminal capture.
      if(node.hidden)return;
      node.parentElement.dataset.terminalTheme=last.theme;
      if(last.ansi===painted)return;
      const lines=parse(last.ansi);
      let start=0,end=0;
      while(start<rows.length&&start<lines.length&&rows[start].html===lines[start].html)start++;
      while(end<rows.length-start&&end<lines.length-start&&rows[rows.length-1-end].html===lines[lines.length-1-end].html)end++;
      // Timer ticks and appended output replace only their changed lines.
      // Keep the rest of the DOM, horizontal position and text selection intact.
      if(painted===null)node.replaceChildren();
      const fragment=document.createDocumentFragment(),changed=[];
      for(let index=start;index<lines.length-end;index++){
        const value=lines[index];
        const element=document.createElement('span');element.className='terminal-row';element.innerHTML=value.html;
        fragment.append(element);changed.push({...value,element});
      }
      const anchor=end?rows[rows.length-end].element:null;
      for(let index=start;index<rows.length-end;index++)rows[index].element.remove();
      node.insertBefore(fragment,anchor);
      rows=[...rows.slice(0,start),...changed,...(end?rows.slice(-end):[])];painted=last.ansi;
    }
    return {update,show:()=>update(last??''),matches:value=>{const next=capture(value);return next.ansi===last?.ansi&&next.theme===last?.theme;}};
  }
  const api={render,plain,mount};
  if(typeof module==='object'&&module.exports)module.exports=api;else root.HubTerminal=api;
})(globalThis);
