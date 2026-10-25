/* Recipient routing is local to the browser that made the request. */
((root) => {
  function supervisorRequest(text) {
    const match = String(text).match(/^\s*(?:aria|supervisor)(?:\s*[,，:]\s*|\s+|$)([\s\S]*)$/i);
    return match ? match[1].trim() : null;
  }
  function endCallRequested(text) {
    const request = (supervisorRequest(text) ?? String(text)).trim().toLowerCase().replace(/\s+/g, ' ');
    // Match a whole request, never a quoted instruction, negation or mention
    // inside an agent task. These controls need no model or agent command.
    return /^(?:(?:can|could|would|will) you )?(?:please )?(?:hang ?up|hang off|(?:end|close|stop) (?:(?:the|this|our) )?(?:call|voice (?:call|chat))|(?:stop|disable|turn off|switch off) (?:(?:the|my|your) )?(?:mic|microphone)|turn (?:(?:the|my|your) )?(?:mic|microphone) off)(?:[, ]+(?:for me|now|please|thanks|thank you))*[.!?]*$/.test(request);
  }
  function ownsReply(job, request, context, revision) {
    return Boolean(request && job.status === 'complete' && job.request_id === request.id &&
      request.context === (context || '') && request.revision === revision);
  }
  function withReply(job,messages){
    if(!job || job.status!=='complete' || typeof job.reply==='string' || !job.request_id)return job;
    const message=[...messages].reverse().find(m=>m.role==='assistant' && m.request_id===job.request_id &&
      (m.context_agent_id || '')===(job.context_agent_id || ''));
    return message && typeof message.text==='string'?{...job,reply:message.text}:job;
  }
  function selectionTarget(job, request, context, revision, agents) {
    if (!ownsReply(job, request, context, revision) || job.agent_choices?.length) return null;
    return agents.find(agent => agent.id === job.select_agent_id && agent.connection_online && agent.live) || null;
  }
  function clarificationFor(job, request, context, revision, agents, now = Date.now() / 1000) {
    if (!ownsReply(job, request, context, revision) || !job.clarification_id || !(job.clarification_expires > now)) return null;
    const seen = new Set();
    const choices = (Array.isArray(job.agent_choices) ? job.agent_choices : []).slice(0, 6).filter(choice => {
      if (!choice || typeof choice.reason !== 'string' || seen.has(choice.agent_id) || !agents.some(a => a.id === choice.agent_id)) return false;
      seen.add(choice.agent_id); return true;
    });
    return choices.length < 2 ? null : { id: job.clarification_id, expires: job.clarification_expires,
      requestId: request.id, context: request.context, revision, choices,
      purpose:job.clarification_purpose==='coordinate'?'coordinate':'navigate' };
  }
  function nextHandoff(items,agents,currentId,passed=[]){
    const skip=new Set(passed);
    let candidates=items.filter(item=>item.agent_id!==currentId && !skip.has(item.id));
    if(!candidates.length)candidates=items.filter(item=>item.agent_id!==currentId);
    if(!candidates.length)return items[0] || null;
    const first=candidates[0],current=agents.find(agent=>agent.id===currentId);
    const cwd=String(current?.cwd || '').replace(/\/+$/,'');
    if(!cwd || /^(?:\/tmp|\/home\/[^/]+|\/Users\/[^/]+)$/.test(cwd))return first;
    const group=candidates.filter(item=>Boolean(item.preferred)===Boolean(first.preferred) && item.kind===first.kind);
    return group.find(item=>agents.some(agent=>agent.id===item.agent_id && (current.project_key && agent.project_key?
      agent.project_key===current.project_key:agent.connection_id===current.connection_id && String(agent.cwd || '').replace(/\/+$/,'')===cwd))) || first;
  }
  const routing = { supervisorRequest, endCallRequested, selectionTarget, clarificationFor, nextHandoff, withReply };
  if (typeof module !== 'undefined' && module.exports) module.exports = routing;
  root.HubRouting = routing;
})(typeof window === 'undefined' ? globalThis : window);
