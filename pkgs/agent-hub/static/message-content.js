/* Complete Codex context envelopes are metadata, even when stored as user input. */
((root)=>{
  const opening=/^<(codex_internal_context|environment_context|permissions instructions|collaboration_mode|turn_aborted|local-command-caveat|system-reminder)(?:\s+[\w:-]+=(?:"[^"]*"|'[^']*'))*\s*>/;
  const agents=/^# AGENTS\.md instructions for [^\r\n]+\r?\n\s*<INSTRUCTIONS>/;
  const blankLines=text=>text.replace(/^(?:[ \t]*\r?\n)+/,'');
  function userText(text){
    for(;;){
      const candidate=blankLines(text),header=agents.exec(candidate),start=header||opening.exec(candidate);
      if(!start)return text;
      const close=header?'</INSTRUCTIONS>':`</${start[1]}>`,end=candidate.indexOf(close,start[0].length);
      if(end<0)return text;
      text=blankLines(candidate.slice(end+close.length));
      if(!text.trim())return '';
    }
  }
  const visible=message=>![message.phase,message.channel,message.type].some(value=>['analysis','reasoning','thinking','reasoning_summary'].includes(value))&&
    (message.role!=='user'||Boolean(userText(message.text||'')));
  const clean=message=>message.role==='user'?{...message,text:userText(message.text||'')}:message;
  const messages=items=>(items||[]).map(clean).filter(visible);
  // The image-only transport instruction is not a caption written by the user.
  // Keep the original text for receipt matching, history IDs and native forks.
  const displayText=message=>(!message.role||message.role==='user')&&(message.attachments?.length||message.has_attachments)&&
    message.text?.trim()==='Please look at the attached images.'?'':message.text||'';
  const api={userText,visible,clean,messages,displayText};
  if(typeof module==='object'&&module.exports)module.exports=api;else root.HubMessageContent=api;
})(globalThis);
