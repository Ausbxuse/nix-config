/* Push readiness is verified by the relay, not inferred from a configured URL. */
((root) => {
  function status(snapshot,{connected=true,elapsed=0}={}) {
    const watch=snapshot?.watch || {},push=watch.push_status || {},now=(snapshot?.server_time || 0)+elapsed;
    const count=Number.isInteger(push.subscribers)?push.subscribers:null;
    const devices=count===null?'Device status is not verified.':`${count} ${count===1?'device':'devices'} enrolled.`;
    const attention=snapshot?.assistant?.attention?.mode || 'normal';
    let state='checking',label='Checking alerts',help='Checking watching and enrolled devices.';
    if (!connected) {state='error';label='Status unavailable';help='Hub connection lost. Push status is not verified.';}
    else if (!watch.eligible) {state='off';label='On controller';help='Open the controller hub for notifications across hosts.';}
    else if (!watch.enabled) {state='paused';label='Alerts paused';help='Resume watching to receive new alerts.';}
    else if (watch.error || (watch.checked_at && now-watch.checked_at>20)) {state='error';label='Watch reconnecting';help='Watching is interrupted. The hub is retrying.';}
    else if (!watch.push_available) {state='off';label='Push not set up';help='Updates stay in the hub. Push is not configured on this host.';}
    else if (attention==='quiet') {state='paused';label='Alerts quiet';help='Quiet mode holds phone alerts. Change Updates in ARIA settings to resume.';}
    else if (watch.push_error || push.error || (push.checked_at && now-push.checked_at>90)) {state='error';label='Push reconnecting';help='Push could not be verified. Updates stay in the hub; delivery retries.';}
    else if (count===0 && push.checked_at) {state='off';label='No devices';help='Set up this browser to receive alerts with the hub closed.';}
    else if (count>0 && push.checked_at && watch.checked_at) {
      state='on';label=attention==='focus'?'Focused alerts':'Alerts on';
      help=`${devices} ${attention==='focus'?'Only unseen handoffs that match your focus.':'Only unseen handoffs.'}`;
    }
    const latest=watch.push_latest;
    let outcome='';
    if (latest?.push_state==='observed') outcome='Latest update: already seen; push skipped.';
    else if (latest?.push_state==='handled') outcome='Latest update: handled; push skipped.';
    else if (latest?.push_state==='no_device') outcome='Latest update: no enrolled device at that time.';
    else if (latest?.push_state==='pending') outcome='Latest update: waiting to send.';
    else if (latest?.push_state==='sent') outcome='Latest push accepted by the push service.';
    return {state,label,help,outcome,accepted:push.last_accepted_at || 0,devices};
  }
  function route(address){
    const url=new URL(address),query=url.searchParams,fragment=new URLSearchParams(url.hash.slice(1));
    const params=query.has('updates')?query:fragment.has('updates')?fragment:null;
    return params?{notice:params.get('notice') || '',agent:params.get('agent') || '',key:params.toString()}:null;
  }
  const exports={status,route};
  if(typeof module==='object' && module.exports)module.exports=exports;else root.HubPush=exports;
})(globalThis);
