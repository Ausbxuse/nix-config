const {test}=require('node:test'),assert=require('node:assert/strict');
const {readings,resetTime,context,weekly,detailSources,headerSources}=require('./static/usage.js');
const connection=(used=61,extra={})=>({status:'online',usage:{status:'available',observed_at:1000,stale:false,weekly:[{id:'codex',label:'Codex',used_percent:used,resets_at:2000}],...extra}});
test('weekly display uses remaining percentage, including zero and exhaustion',()=>{
 assert.equal(readings(connection(),1000).rows[0].value,'39% left');
 assert.equal(readings(connection(0),1000).rows[0].value,'100% left');
 assert.equal(readings(connection(100),1000).rows[0].value,'0% left');
 assert.equal(readings(connection(100),1000).rows[0].level,'low');
 assert.equal(readings(connection(73),1000).rows[0].remaining,27);
});
test('expired reports never pretend that the weekly quota has replenished',()=>{
 const report=readings(connection(100),2001).rows[0];
 assert.equal(report.expired,true);assert.equal(report.value,'Awaiting update');
 assert.equal(report.remaining,0);
});
test('offline and old reports keep their last observation visibly stale',()=>{
 assert.equal(readings({...connection(),status:'offline'},1000).note,'Host offline');
 assert.equal(readings(connection(),1181).rows[0].stale,true);
 assert.equal(readings(connection(61,{stale:true}),1000).note,'Last known usage');
 assert.equal(readings({status:'online'},1000).status,'unavailable');
 assert.equal(readings({status:'online'},1000).rows.length,0);
});
test('reset time is explicit and missing reset timestamps are not made up',()=>{
 assert.match(resetTime(2000),/^Resets /);
 assert.equal(resetTime(null),'Reset time not reported');
});
test('Claude session and weekly windows stay separate from Codex',()=>{
 const conn=connection(61);
 conn.claude_usage={status:'available',observed_at:1000,stale:false,limits:[
  {id:'five_hour',label:'Claude',window_minutes:300,used_percent:26,resets_at:2000},
  {id:'seven_day',label:'Claude',window_minutes:10080,used_percent:17,resets_at:3000},
  {id:'seven_day_sonnet',label:'Claude Sonnet',window_minutes:10080,used_percent:0,resets_at:null}
 ]};
 assert.equal(readings(conn,1000).rows[0].remaining,39);
 const rows=readings(conn,1000,'claude').rows;
 assert.deepEqual(rows.map(row=>row.period),['5h','weekly','weekly']);
 assert.deepEqual(rows.map(row=>row.remaining),[74,83,100]);
 assert.equal(rows[2].label,'Claude Sonnet');
 assert.equal(readings(conn,2001,'claude').rows[0].expired,true);
 assert.equal(readings(conn,2001,'claude').rows[1].expired,false);
 conn.claude_usage.stale=true;
 assert.equal(readings(conn,1000,'claude').note,'Last known usage');
 assert.equal(readings(conn,1000).stale,false);
});
test('Claude authentication failures are unavailable, never full quota',()=>{
 const conn=connection(61);
 conn.claude_usage={status:'unavailable',reason:'auth_required',stale:true,limits:[]};
 assert.equal(readings(conn,1000,'claude').reason,'auth_required');
 assert.equal(readings(conn,1000,'claude').rows.length,0);
 assert.equal(readings(conn,1000).rows[0].remaining,39);
});
test('the header follows the recipient account, and supervisor uses its own host',()=>{
 const local={...connection(20),id:'razy',kind:'local'},remote={...connection(90),id:'gpu',kind:'peer'};
 const snapshot={connections:[local,remote],supervisor:{owner:'razy',provider:'codex'}};
 assert.deepEqual(context(snapshot,{connection_id:'gpu',runtime:'claude'}),{connection:remote,provider:'claude'});
 assert.equal(weekly(context(snapshot,{connection_id:'gpu',runtime:'codex'}).connection,1000).value,'10%');
 assert.deepEqual(context(snapshot),{connection:local,provider:'codex'});
 assert.equal(weekly(context(snapshot).connection,1000).value,'80%');
 assert.equal(context({...snapshot,connections:[remote]}).connection,undefined,'Missing controller usage cannot borrow a worker account');
 assert.equal(context(snapshot,{connection_id:'missing',runtime:'codex'}).connection,undefined);
 assert.equal(context({...snapshot,supervisor:{provider:'codex'}}).connection,local);
});
test('weekly indicator selects the shared pool, not a session or unrelated model limit',()=>{
 const conn=connection(61);
 conn.claude_usage={stale:false,observed_at:1000,limits:[
  {id:'five_hour',label:'Claude',window_minutes:300,used_percent:99,resets_at:2000},
  {id:'seven_day_sonnet',label:'Claude Sonnet',window_minutes:10080,used_percent:100,resets_at:2000},
  {id:'seven_day',label:'Claude',window_minutes:10080,used_percent:17,resets_at:2000}
 ]};
 assert.equal(weekly(conn,1000,'claude').value,'83%');
 conn.claude_usage.limits=conn.claude_usage.limits.slice(0,1);
 assert.equal(weekly(conn,1000,'claude').value,'—','A five-hour value is not weekly');
 conn.usage.weekly.unshift({id:'model:primary',label:'Other model',used_percent:98,resets_at:2000});
 assert.equal(weekly(conn,1000).value,'39%');
});
test('compact values stay honest near exhaustion and across a reset',()=>{
 assert.equal(weekly(connection(99.8),1000).value,'<1%');
 assert.equal(weekly(connection(0.2),1000).value,'99%');
 assert.equal(weekly(connection(0),1000).value,'100%');
 assert.equal(weekly(connection(100),1000).value,'0%');
 assert.equal(weekly(connection(100),2001).value,'—');
 assert.equal(weekly({...connection(3),status:'offline'},1000).level,'stale');
 assert.equal(weekly(connection(3),1181).level,'stale');
 assert.equal(weekly(undefined,1000).value,'—');
});
test('details show the host actually running Claude without relabeling another account',()=>{
 const local={...connection(6),id:'razy',claude_usage:{status:'unavailable',reason:'auth_required',limits:[]}};
 const worker={...connection(20),id:'gpu',claude_usage:{stale:false,observed_at:1000,limits:[{id:'seven_day',label:'Claude',window_minutes:10080,used_percent:21,resets_at:2000}]}};
 const snapshot={connections:[local,worker],agents:[{connection_id:'gpu',runtime:'claude',live:true}]};
 assert.deepEqual(detailSources(snapshot,local,'codex',1000).map(s=>[s.connection.id,s.provider]),[['razy','codex'],['gpu','claude']]);
 assert.equal(weekly(local,1000,'claude').value,'—','The local Claude account still has no report');
 assert.equal(weekly(worker,1000,'claude').value,'79%');
 assert.deepEqual(detailSources(snapshot,worker,'claude',1000).map(s=>[s.connection.id,s.provider]),[['gpu','claude'],['razy','codex'],['gpu','codex']]);
});
const claudeReport=(used=21,stale=false)=>({stale,observed_at:1000,limits:[{id:'seven_day',label:'Claude',window_minutes:10080,used_percent:used,resets_at:2000}]});
test('both header batteries stay visible in provider order and use separate source accounts',()=>{
 const local={...connection(6),id:'razy',kind:'local',claude_usage:{status:'unavailable',limits:[]}};
 const worker={...connection(79),id:'gpu',claude_usage:claudeReport(93)};
 const snapshot={connections:[local,worker],supervisor:{owner:'razy',provider:'codex'},agents:[{connection_id:'gpu',runtime:'claude',live:true}]};
 const values=agent=>headerSources(snapshot,agent,1000).map(s=>[s.provider,s.connection?.id,weekly(s.connection,1000,s.provider).value]);
 assert.deepEqual(values(),[['codex','razy','94%'],['claude','gpu','7%']]);
 assert.deepEqual(values({connection_id:'gpu',runtime:'claude'}),[['codex','gpu','21%'],['claude','gpu','7%']]);
 assert.deepEqual(values({connection_id:'gpu',runtime:'codex'}),[['codex','gpu','21%'],['claude','gpu','7%']]);
});
test('a missing recipient allowance stays unavailable instead of borrowing another host account',()=>{
 const local={...connection(6),id:'razy',kind:'local',claude_usage:{status:'unavailable',reason:'auth_required',limits:[]}};
 const worker={...connection(79),id:'gpu',claude_usage:claudeReport()};
 const snapshot={connections:[local,worker],supervisor:{owner:'missing',provider:'codex'}};
 let sources=headerSources(snapshot,null,1000);
 assert.equal(sources[0].connection,undefined);assert.equal(weekly(sources[0].connection,1000,'codex').value,'—');
 assert.equal(sources[1].connection,worker);
 sources=headerSources(snapshot,{connection_id:'razy',runtime:'claude'},1000);
 assert.equal(sources[1].connection,local);assert.equal(weekly(sources[1].connection,1000,'claude').value,'—');
 assert.deepEqual(headerSources({connections:[worker],supervisor:{owner:'gpu'}},null,1000).map(s=>s.connection.id),['gpu','gpu']);
});
test('the other provider favors fresh local or active-host reports and does not invent a full battery',()=>{
 const local={...connection(6),id:'razy',kind:'local',claude_usage:claudeReport(50,true)};
 const unused={...connection(20),id:'unused',claude_usage:claudeReport(10)};
 const worker={...connection(79),id:'gpu',claude_usage:claudeReport(93)};
 const snapshot={connections:[local,unused,worker],supervisor:{owner:'razy',provider:'codex'},agents:[{connection_id:'gpu',runtime:'claude',live:true}]};
 assert.equal(headerSources(snapshot,null,1000)[1].connection,worker);
 local.claude_usage.stale=false;
 assert.equal(headerSources(snapshot,null,1000)[1].connection,local);
 for(const c of snapshot.connections)c.claude_usage={status:'unavailable',limits:[]};
 const source=headerSources(snapshot,null,1000)[1];
 assert.equal(source.connection,worker);assert.equal(weekly(source.connection,1000,'claude').value,'—');
});
