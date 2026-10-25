// Control requests are separate from audio requests; fixtures never send them to a real hub.
function mockVoiceFetch(handler) {
  global.fetch=(url,options)=>url==='/api/voice/cancel'?Promise.resolve({ok:true,json:async()=>({ok:true})}):handler(url,options);
}
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {wav,VoiceBuffer,LocalRecognition,LocalPlayback,speechChunks}=require('./static/voice.js');

test('phone sample rates produce bounded mono PCM WAV',()=>{
  for(const rate of [44100,48000]){
    const samples=Float32Array.from({length:rate},(_,i)=>.2*Math.sin(i*2*Math.PI*440/rate));
    const buffer=wav(samples,rate),view=new DataView(buffer);
    assert.equal(buffer.byteLength,32044);
    assert.equal(view.getUint16(22,true),1);
    assert.equal(view.getUint32(24,true),16000);
    assert.equal(view.getUint32(40,true),32000);
    assert.equal(new TextDecoder().decode(buffer.slice(0,4)),'RIFF');
    assert(view.getInt16(44+10*2,true)>0);
  }
  assert.equal(wav(new Float32Array(16000*31),16000).byteLength,44+30*32000);
});

test('silence and short noises do not submit; a pause ends an utterance',()=>{
  const buffer=new VoiceBuffer(16000),silence=new Float32Array(320);
  const sound=Float32Array.from({length:320},(_,i)=>.1*Math.sin(i*.12));
  for(let i=0;i<100;i++)assert.equal(buffer.push(silence),null);
  for(let i=0;i<4;i++)assert.equal(buffer.push(sound),null);
  for(let i=0;i<100;i++)assert.equal(buffer.push(silence),null);
  for(let i=0;i<50;i++)assert.equal(buffer.push(sound),null);
  for(let i=0;i<60;i++)assert.equal(buffer.push(silence),null,'a 1.2 second pause stays in this turn');
  for(let i=0;i<10;i++)assert.equal(buffer.push(sound),null);
  for(let i=0;i<74;i++)assert.equal(buffer.push(silence),null);
  const result=buffer.push(silence); // 1.5 seconds after the resumed words.
  assert(result?.length>16000);
  assert(result.length<16000*4);
  for(let i=0;i<100;i++)assert.equal(buffer.push(silence),null);
});

test('continuous speech ends at the upload duration limit',()=>{
  const buffer=new VoiceBuffer(48000),sound=new Float32Array(2048).fill(.1);
  let result;
  for(let i=0;i<710;i++)if((result=buffer.push(sound)))break;
  assert.equal(result.length,48000*30);
});

test('final silence reuses exactly the last caption audio, while resumed speech changes it',()=>{
  const buffer=new VoiceBuffer(16000),sound=new Float32Array(320).fill(.1),silence=new Float32Array(320);
  for(let i=0;i<50;i++)buffer.push(sound);
  for(let i=0;i<12;i++)buffer.push(silence);
  const caption=buffer.snapshot();
  for(let i=0;i<12;i++)buffer.push(silence);
  assert.deepEqual(buffer.snapshot(),caption,'silence does not force another model decode');
  buffer.push(sound);
  assert(buffer.snapshot().length>caption.length,'a resumed word must invalidate reuse');
  for(let i=0;i<12;i++)buffer.push(silence);
  const last=buffer.snapshot();let final;
  for(let i=0;i<70;i++)final ||= buffer.push(silence);
  assert.deepEqual(final,last);
});

test('end of speech requests one refined caption and still waits before submitting',()=>{
  const recognition=new LocalRecognition(()=>'token'),requests=[];
  recognition.buffer=new VoiceBuffer(16000);recognition.phase='listening';recognition.active=true;
  recognition.transcribe=(samples,generation,final,utterance)=>requests.push({samples,final,utterance});
  for(let i=0;i<50;i++)recognition.captureAudio(new Float32Array(320).fill(.1));
  for(let i=0;i<20;i++)recognition.captureAudio(new Float32Array(320));
  assert.equal(requests.length,2);assert(requests.every(request=>request.final===false));
  for(let i=0;i<54;i++)recognition.captureAudio(new Float32Array(320));
  assert.equal(requests.length,2,'a longer pause does not submit early or repeat captions');
  recognition.captureAudio(new Float32Array(320));
  assert.equal(requests.length,3);assert.equal(requests[2].final,true);
  assert.deepEqual(requests[2].samples,requests[1].samples);
  recognition.abort();
});

test('cancel discards a pending transcript and releases capture resources',async()=>{
  const saved=global.fetch;
  let resolve,aborted=false,stopped=0,results=0,ended=0;
  mockVoiceFetch((_,options)=>new Promise(done=>{resolve=done;options.signal.addEventListener('abort',()=>{aborted=true;});}));
  try{
    const recognition=new LocalRecognition(()=>'token');
    recognition.active=true;recognition.phase='listening';recognition.context={sampleRate:16000};
    const track={enabled:true,stop(){stopped++;}};
    recognition.stream={getTracks:()=>[track],getAudioTracks:()=>[track]};
    recognition.onresult=()=>results++;recognition.onend=()=>ended++;
    const pending=recognition.transcribe(new Float32Array(16000).fill(.1),0);
    assert.equal(stopped,0,'transcription keeps the call microphone open');
    assert.equal(track.enabled,false,'the final transcript holds capture without reopening the device');
    recognition.abort();
    resolve({ok:true,json:async()=>({text:'do not send this late result'})});
    await pending;
    assert(aborted);assert.equal(results,0);assert.equal(ended,1);assert.equal(stopped,1);
  }finally{global.fetch=saved;}
});

test('live captions are provisional and the final turn wins over a late interim result',async()=>{
  const original=global.fetch,pending=[],results=[],starts=[];
  mockVoiceFetch((_,options)=>new Promise(resolve=>pending.push({resolve,options})));
  const recognition=new LocalRecognition(()=>'token');
  recognition.context={sampleRate:16000};recognition.buffer=new VoiceBuffer(16000);recognition.phase='listening';recognition.active=true;
  recognition.onspeechstart=event=>starts.push(event.utterance);recognition.onresult=event=>results.push(event);
  const sound=new Float32Array(320).fill(.1),silence=new Float32Array(320);
  const flush=()=>new Promise(resolve=>setImmediate(resolve));
  try{
    for(let i=0;i<50;i++)recognition.captureAudio(sound);
    assert.equal(pending.length,1);assert.equal(starts.length,1);
    pending[0].resolve({ok:true,json:async()=>({text:'supervisor, show'})});await flush();
    assert.equal(results.length,1);assert.equal(results[0].results[0].isFinal,false);
    for(let i=0;i<65;i++)recognition.captureAudio(sound);
    assert.equal(pending.length,2);
    for(let i=0;i<75;i++)recognition.captureAudio(silence);
    assert(recognition.processing);assert(recognition.active);
    assert.equal(pending.length,3,'final audio is submitted before the slow caption returns');
    assert(pending[1].options.signal.aborted);
    const partialWork=JSON.parse(pending[1].options.headers['X-Hub-Speech-Work']);
    const finalWork=JSON.parse(pending[2].options.headers['X-Hub-Speech-Work']);
    assert.equal(finalWork.mode,'final');assert.equal(partialWork.mode,'interim');
    assert.equal(finalWork.turn,partialWork.turn);assert(finalWork.sequence>partialWork.sequence);
    pending[1].resolve({ok:true,json:async()=>({text:'stale partial'})});await flush();
    assert.equal(results.length,1,'a late partial cannot replace a finished utterance');
    assert.equal(pending.length,3);
    pending[2].resolve({ok:true,json:async()=>({text:'supervisor, show the LAP agent'})});await flush();
    assert.equal(results.length,2);assert.equal(results[1].results[0].isFinal,true);assert.equal(results[1].utterance,starts[0]);
    assert(recognition.active);assert.equal(recognition.processing,false);
  }finally{recognition.abort();global.fetch=original;}
});

test('reply playback disables capture and resumes with a fresh speech buffer',()=>{
  const recognition=new LocalRecognition(()=>'token');
  recognition.buffer=new VoiceBuffer(16000);recognition.phase='listening';recognition.active=true;
  const track={enabled:true,stop(){}};
  recognition.stream={getAudioTracks:()=>[track],getTracks:()=>[track]};
  let starts=0;recognition.onspeechstart=()=>starts++;
  const sound=new Float32Array(320).fill(.1);
  for(let i=0;i<4;i++)recognition.captureAudio(sound);
  recognition.setPlayback(true);
  for(let i=0;i<100;i++)recognition.captureAudio(sound);
  assert.equal(track.enabled,false);assert.equal(starts,0);assert(!recognition.recording);
  assert.equal(recognition.buffer.pre.length,0);assert.equal(recognition.jobs.length,0);assert(!recognition.inflight);
  recognition.setPlayback(false);
  assert(track.enabled);assert(recognition.active,'resuming does not reopen the device');
  for(let i=0;i<4;i++)recognition.captureAudio(sound);
  assert.equal(starts,0,'pre-playback audio cannot trigger the next utterance');
  for(let i=0;i<3;i++)recognition.captureAudio(sound);
  assert.equal(starts,1);assert(recognition.recording);assert.equal(recognition.buffer.snapshot().length,320*7);
  recognition.abort();
});

test('sustained speech interrupts playback once and preserves its opening words at phone sample rates',()=>{
  for(const rate of [16000,44100,48000]){
    const recognition=new LocalRecognition(()=>'token'),track={enabled:true,stop(){}},requests=[];
    recognition.buffer=new VoiceBuffer(rate);recognition.phase='listening';recognition.active=true;
    recognition.stream={getAudioTracks:()=>[track],getTracks:()=>[track]};
    recognition.transcribe=(samples,generation,final,utterance)=>requests.push({samples,final,utterance});
    const sound=Float32Array.from({length:Math.round(rate*.02)},(_,i)=>.1*Math.sin(i*.12)),silence=new Float32Array(sound.length);
    let interruptions=0,starts=0;
    recognition.onspeechstart=()=>starts++;
    recognition.oninterrupt=()=>{
      interruptions++;recognition.setInterruptible(false);recognition.setBlocked(false);recognition.setPlayback(false);
    };
    // A streamed answer may still be generating. Monitoring can stop its audio
    // while normal transcription remains gated until the interruption callback.
    recognition.setBlocked(true);recognition.setPlayback(true);recognition.setInterruptible(true);
    assert(track.enabled);assert(!recognition.accepting);assert(recognition.monitoring);
    for(let i=0;i<11;i++)recognition.captureAudio(sound);
    assert.equal(interruptions,0);assert.equal(starts,0);assert.equal(requests.length,0);
    recognition.captureAudio(sound);
    assert.equal(interruptions,1);assert.equal(starts,1);assert(recognition.recording);
    assert.equal(recognition.buffer.count,sound.length*12,'the opening 240 ms remains in the utterance');
    for(let i=0;i<30;i++)recognition.captureAudio(sound);
    for(let i=0;i<75;i++)recognition.captureAudio(silence);
    const finals=requests.filter(request=>request.final);
    assert.equal(finals.length,1);assert.equal(interruptions,1);
    assert.equal(finals[0].samples.length,Math.round(rate*1.04));
    assert.deepEqual(finals[0].samples.subarray(0,sound.length),sound,'the first packet is not clipped');
    recognition.abort();
  }
});

test('quiet residual audio and short noises neither interrupt nor reach transcription',()=>{
  const recognition=new LocalRecognition(()=>'token');
  recognition.buffer=new VoiceBuffer(16000);recognition.phase='listening';recognition.active=true;
  let interruptions=0,uploads=0;
  recognition.oninterrupt=()=>interruptions++;recognition.transcribe=()=>uploads++;
  recognition.setPlayback(true);recognition.setInterruptible(true);
  const quiet=new Float32Array(320).fill(.006),noise=new Float32Array(320).fill(.2),silence=new Float32Array(320);
  for(let i=0;i<500;i++)recognition.captureAudio(quiet);
  for(let i=0;i<10;i++)recognition.captureAudio(noise);
  for(let i=0;i<50;i++)recognition.captureAudio(silence);
  assert.equal(interruptions,0);assert.equal(uploads,0);assert.equal(recognition.jobs.length,0);
  assert(recognition.interruptionBuffer.lead<=16000*.47,'monitoring memory is bounded');
  recognition.setInterruptible(false);
  assert.equal(recognition.interruptionBuffer,null);
  recognition.setPlayback(false);
  for(let i=0;i<4;i++)recognition.captureAudio(noise);
  assert(!recognition.recording,'monitoring audio never leaks into the next turn');
  recognition.abort();
});

test('muting or ending during the interruption callback cannot revive buffered speech',()=>{
  for(const end of [false,true]){
    const recognition=new LocalRecognition(()=>'token'),track={enabled:true,stop(){this.enabled=false;}};
    recognition.buffer=new VoiceBuffer(16000);recognition.phase='listening';recognition.active=true;
    recognition.stream={getAudioTracks:()=>[track],getTracks:()=>[track]};
    let starts=0;recognition.onspeechstart=()=>starts++;
    recognition.oninterrupt=()=>{recognition.setInterruptible(false);end?recognition.abort():recognition.setBlocked(true);};
    recognition.setPlayback(true);recognition.setInterruptible(true);
    const generation=recognition.generation;
    for(let i=0;i<50;i++)recognition.captureAudio(new Float32Array(320).fill(.1),generation);
    assert.equal(starts,0);assert(!track.enabled);assert.equal(recognition.jobs.length,0);
    assert.equal(recognition.interruptionBuffer,null);assert(!recognition.recording);
    recognition.abort();
  }
});

test('pausing capture drops delayed transcripts without reporting an error or sending work',async()=>{
  const original=global.fetch,results=[],errors=[];let resolve,aborted=false;
  mockVoiceFetch((_,options)=>new Promise(done=>{resolve=done;options.signal.addEventListener('abort',()=>aborted=true);}));
  const recognition=new LocalRecognition(()=>'token');
  recognition.active=true;recognition.phase='listening';recognition.context={sampleRate:16000};
  recognition.onresult=event=>results.push(event);recognition.onerror=event=>errors.push(event);
  try{
    const pending=recognition.transcribe(new Float32Array(16000).fill(.1),0,true,1);
    recognition.setPlayback(true);
    assert(aborted);assert.equal(recognition.processing,false);
    resolve({ok:true,json:async()=>({text:'do not send this late result'})});await pending;
    assert.deepEqual(results,[]);assert.deepEqual(errors,[]);
    recognition.setPlayback(false);
    const next=recognition.transcribe(new Float32Array(16000).fill(.1),0,true,2);
    resolve({ok:true,json:async()=>({text:'this is a new instruction'})});await next;
    assert.equal(results.length,1);assert.equal(results[0].utterance,2);
  }finally{recognition.abort();global.fetch=original;}
});

test('one final transcript holds the mic through thinking and speech without buffering noise',async()=>{
  const original=global.fetch,requests=[],results=[];
  mockVoiceFetch((_,options)=>new Promise(resolve=>requests.push({resolve,options})));
  const recognition=new LocalRecognition(()=>'token'),track={enabled:true,stop(){}};
  recognition.stream={getAudioTracks:()=>[track],getTracks:()=>[track]};
  recognition.context={sampleRate:16000};recognition.buffer=new VoiceBuffer(16000);recognition.phase='listening';recognition.active=true;
  let starts=0;recognition.onspeechstart=()=>starts++;
  recognition.onresult=event=>{results.push(event);recognition.setBlocked(true);};
  const sound=new Float32Array(320).fill(.1);
  try{
    const pending=recognition.transcribe(new Float32Array(16000).fill(.1),0,true,1);
    assert(recognition.processing);assert(!track.enabled);
    for(let i=0;i<200;i++)recognition.captureAudio(sound);
    assert.equal(starts,0);assert.equal(requests.length,1);assert.equal(recognition.jobs.length,0);
    requests[0].resolve({ok:true,json:async()=>({text:'Supervisor, summarize this experiment.'})});await pending;
    assert.equal(results.length,1);assert(!recognition.processing);assert(recognition.blocked);assert(!track.enabled);
    for(let i=0;i<200;i++)recognition.captureAudio(sound);
    assert.equal(starts,0);assert.equal(requests.length,1);assert.equal(recognition.buffer.count,0);
    recognition.setPlayback(true);recognition.setBlocked(false);
    assert(!track.enabled,'finishing the model response cannot open the mic during playback');
    recognition.setBlocked(true);recognition.setPlayback(false);
    assert(!track.enabled,'interrupting audio cannot open the mic while an agent is still responding');
    recognition.setBlocked(false);assert(track.enabled);
    for(let i=0;i<4;i++)recognition.captureAudio(sound);
    assert.equal(starts,0,'held audio never supplies pre-roll for a fresh turn');
    for(let i=0;i<3;i++)recognition.captureAudio(sound);
    assert.equal(starts,1);assert.equal(recognition.buffer.snapshot().length,7*320);
  }finally{recognition.abort();global.fetch=original;}
});

test('a reply beginning during transcription discards late text, including after listening resumes',async()=>{
  const original=global.fetch,results=[],errors=[];let resolve,aborted=false;
  mockVoiceFetch((_,options)=>new Promise(done=>{resolve=done;options.signal.addEventListener('abort',()=>aborted=true);}));
  const recognition=new LocalRecognition(()=>'token');
  recognition.context={sampleRate:16000};recognition.buffer=new VoiceBuffer(16000);recognition.phase='listening';recognition.active=true;
  recognition.onresult=event=>results.push(event);recognition.onerror=event=>errors.push(event);
  try{
    const pending=recognition.transcribe(new Float32Array(16000).fill(.1),0,true,1);
    recognition.setBlocked(true);assert(aborted);assert(!recognition.processing);
    recognition.setBlocked(false);
    resolve({ok:true,json:async()=>({text:'unwanted background speech'})});await pending;
    assert.deepEqual(results,[]);assert.deepEqual(errors,[]);assert.equal(recognition.jobs.length,0);
  }finally{recognition.abort();global.fetch=original;}
});

test('worklet packets preserve audio and missing Firefox input at each device rate',()=>{
  for(const sampleRate of [16000,44100,48000,96000]){
    let Processor;const chunks=[],expected=[];
    require('node:vm').runInNewContext(require('node:fs').readFileSync(__dirname+'/static/voice-capture.js','utf8'),{
      sampleRate,
      AudioWorkletProcessor:class{constructor(){this.port={postMessage:(chunk,transfer)=>chunks.push(structuredClone(chunk,{transfer}))};}},
      registerProcessor(name,type){Processor=type;}
    });
    const capture=new Processor(),size=Math.round(sampleRate*.02);
    for(let i=0;i<256;i++){
      const samples=Float32Array.from({length:128},(_,j)=>i%3?Math.sin((i*128+j)*.12)*.1:0);
      expected.push(...samples);assert(capture.process(i%3?[[samples]]:[[]]));
    }
    assert(chunks.every(chunk=>chunk.length===size),'each packet is 20 ms');
    const actual=chunks.flatMap(chunk=>Array.from(chunk));
    assert.deepEqual(actual,expected.slice(0,actual.length),'no lost, repeated or reordered samples');
    assert(expected.length-actual.length<size,'less than one packet remains buffered');
  }
});

test('device-rate packets keep natural pauses in one instruction and reject short noises',()=>{
  for(const rate of [16000,44100,48000])for(const alignment of [0,.005,.017]){
    const size=Math.round(rate*.02);
    const collect=pieces=>{
      const buffer=new VoiceBuffer(rate),audio=Float32Array.from(pieces.flatMap(([s,v])=>Array(Math.round(s*rate)).fill(v))),finals=[];
      for(let offset=0;offset+size<=audio.length;offset+=size){
        const final=buffer.push(audio.subarray(offset,offset+size));
        if(final)finals.push({audio:final,at:offset+size});
      }
      return finals;
    };
    for(const pause of [.45,.8,1.2]){
      const finals=collect([[.3+alignment,0],[.9,.1],[pause,0],[.7,.1],[2,0]]);
      assert.equal(finals.length,1,'the whole instruction stays in one turn');
      assert.equal(finals[0].audio.filter(sample=>sample>.05).length,Math.round(rate*1.6));
      const delay=finals[0].at/rate-(.3+alignment+.9+pause+.7);
      assert(delay>=1.49&&delay<=1.53,'finish after the full 1.5 second silence allowance');
    }
    for(const noise of [.05,.11,.16])assert.equal(collect([[.3+alignment,0],[noise,.1],[2,0]]).length,0);
  }
});

test('brief noise clears the draft without asking the model to transcribe it',()=>{
  const recognition=new LocalRecognition(()=>'token'),discarded=[];
  recognition.buffer=new VoiceBuffer(16000);recognition.phase='listening';recognition.active=true;
  recognition.onnomatch=event=>discarded.push(event);
  for(let i=0;i<8;i++)recognition.captureAudio(new Float32Array(320).fill(.1));
  for(let i=0;i<75;i++)recognition.captureAudio(new Float32Array(320));
  assert.deepEqual(discarded,[{utterance:1,silent:true}]);assert.equal(recognition.jobs.length,0);assert(!recognition.inflight);
  recognition.abort();
});

test('spoken sentences are bounded and omit Markdown syntax, code blocks and link URLs',()=>{
  const chunks=speechChunks('The [motion trace](https://example.com/a.png) is ready. **Compare** `stdmean` and quantile.\n```python\nprint("not spoken")\n```\n'+ 'checkpoint '.repeat(65));
  assert(chunks.every(text=>text.length<=220));
  assert.match(chunks.join(' '),/The motion trace is ready/);
  assert.doesNotMatch(chunks.join(' '),/https:|print\(|```|\*\*/);
});

test('short spoken replies retain sentence context for natural phrasing',()=>{
  const text="I've queued that for LAP. The current run will keep going.";
  assert.deepEqual(speechChunks(text),[text]);
});

test('longer replies start with a complete sentence instead of waiting for the rest',()=>{
  const first="I've queued that for the LAP agent.";
  const rest="She'll pick it up when the current turn finishes.";
  assert.deepEqual(speechChunks(first+' '+rest),[first,rest]);
});

test('a long opening starts at a natural clause without dropping words or splitting numbers',()=>{
  const first='The quantile checkpoints are ready on GPU four,';
  const rest='and the mean comparison is still running in the other agent.';
  assert.deepEqual(speechChunks(first+' '+rest),[first,rest]);
  const unsplit='On gpu-004, the checkpoint comparison has reached 3.25 hours and is still running with the same parameters';
  assert.deepEqual(speechChunks(unsplit),[unsplit]);
});

test('silent preparation and a matching reply share one request and one decode',async()=>{
  const original=global.fetch;let complete,requests=0,decoded=0,started=0;
  const audio={length:24000,numberOfChannels:1};
  mockVoiceFetch(()=>{requests++;return new Promise(resolve=>{complete=resolve;});});
  const playback=new LocalPlayback(()=>'token',()=>({decodeAudioData:async()=>{decoded++;return audio;}}));
  playback.onstart=()=>started++;
  try{
    playback.preload("I've sent that.");playback.preload("I've sent that.");
    playback.stop("I've sent that.");
    const ready=playback.prepare("I've sent that.",playback.generation);
    complete({ok:true,arrayBuffer:async()=>new ArrayBuffer(44)});
    assert.equal(await ready,audio);assert.equal(requests,1);assert.equal(decoded,1);assert.equal(started,0);
    playback.stop();assert.equal(await playback.prepare("I've sent that.",playback.generation),audio);
    assert.equal(requests,1);assert.equal(decoded,1);
    playback.clear();assert.equal(playback.cached("I've sent that."),null);
  }finally{playback.clear();global.fetch=original;}
});

test('closing a call discards pending preparation and a new answer cancels unrelated warmup',async()=>{
  const original=global.fetch,requests=[];let decoded=0;
  mockVoiceFetch((_,options)=>new Promise(resolve=>requests.push({options,resolve})));
  const playback=new LocalPlayback(()=>'token',()=>({decodeAudioData:async()=>{decoded++;return {length:24000,numberOfChannels:1};}}));
  const response={ok:true,arrayBuffer:async()=>new ArrayBuffer(44)};
  try{
    playback.preload('A prepared reply.');const warm=playback.warming.promise;
    const actual=playback.prepare('A different reply.',playback.generation);
    assert(requests[0].options.signal.aborted);
    requests[0].resolve(response);await warm;assert.equal(decoded,0);
    requests[1].resolve(response);assert(await actual);assert.equal(decoded,1);
    playback.preload('A final reply.');const late=playback.warming.promise;
    playback.clear();requests[2].resolve(response);await late;
    assert.equal(decoded,1);assert.equal(playback.cache.size,0);
  }finally{playback.clear();global.fetch=original;}
});

test('decoded audio cache expires, bounds memory and clears when the hub restarts',()=>{
  let token='first';const playback=new LocalPlayback(()=>token,()=>null);
  try{
    const audio={length:24000,numberOfChannels:1};
    for(let i=0;i<17;i++)playback.remember(String(i),audio);
    assert.equal(playback.cache.size,16);assert.equal(playback.cached('0'),null);
    playback.cache.get('1').until=Date.now()-1;assert.equal(playback.cached('1'),null);
    playback.remember('large',{length:1024*1024,numberOfChannels:1});
    playback.remember('larger',{length:1024*1024+1,numberOfChannels:1});
    assert.equal(playback.cache.size,1);assert.equal(playback.cached('large'),null);
    token='restarted';assert.equal(playback.cached('larger'),null);assert.equal(playback.cache.size,0);
  }finally{playback.clear();}
});

test('interrupting local playback discards audio that arrives after cancellation',async()=>{
  const original=global.fetch;let resolve,aborted=false,decoded=0;
  mockVoiceFetch((_,options)=>new Promise(done=>{resolve=done;options.signal.addEventListener('abort',()=>aborted=true);}));
  const playback=new LocalPlayback(()=>'token',()=>({decodeAudioData(){decoded++;}}));
  try{
    const speaking=playback.speak('An old reply.');await new Promise(resolve=>setImmediate(resolve));playback.stop();
    resolve({ok:true,arrayBuffer:async()=>new ArrayBuffer(44)});
    assert.equal(await speaking,false);assert(aborted);assert.equal(decoded,0);
  }finally{playback.stop();global.fetch=original;}
});

test('streamed speech starts before completion, prefetches one clip and stops without replay',async()=>{
  const original=global.fetch;mockVoiceFetch(()=>Promise.resolve({ok:true}));
  const playback=new LocalPlayback(()=>'token',()=>null),prepared=[],playing=[];
  let release;
  playback.prepare=async text=>{prepared.push(text);return {};};
  playback.play=async (_,text)=>{playing.push(text);await new Promise(resolve=>{release=resolve;playback.finish=resolve;});};
  const flush=()=>new Promise(resolve=>setImmediate(resolve));
  try{
    const stream=playback.begin();stream.append('The first comparison is ready.');await flush();
    assert.deepEqual(playing,['The first comparison is ready.']);
    stream.append(' The second run is still working.');stream.append(' The third run needs review.');await flush();
    assert.deepEqual(prepared,['The first comparison is ready.','The second run is still working.']);
    release();await flush();assert.equal(playing.length,2);assert.equal(prepared.length,3);
    playback.stop();assert.equal(await stream.done,false);assert.equal(stream.append('Late reply.'),false);
    assert.equal(playing.length,2,'the queued old clip never plays');
    const next=playback.begin('A new reply.');next.finish();await flush();
    release();assert.equal(await next.done,true);
    assert.equal(playing.at(-1),'A new reply.');
    const before=prepared.length,immediate=playback.speak('Canceled before synthesis.');playback.stop();
    assert.equal(await immediate,false);assert.equal(prepared.length,before);
  }finally{playback.stop();global.fetch=original;}
});

test('ending a call cancels host jobs without invalidating a later reply',async()=>{
  const original=global.fetch,cancels=[],audio=[];
  global.fetch=(url,options)=>{
    if(url==='/api/voice/cancel'){
      cancels.push({value:JSON.parse(options.body),token:options.headers['X-Hub-Token']});
      return Promise.resolve({ok:true});
    }
    return new Promise(resolve=>audio.push({resolve,options}));
  };
  const playback=new LocalPlayback(()=>'private-token',()=>({decodeAudioData:async()=>({length:1,numberOfChannels:1})}));
  const response={ok:true,arrayBuffer:async()=>new ArrayBuffer(44)};
  try{
    const first=playback.prepare('First reply.',playback.generation);
    const oldWork=JSON.parse(audio[0].options.body).work;
    playback.stop();
    const next=playback.prepare('Next reply.',playback.generation);
    const newWork=JSON.parse(audio[1].options.body).work;
    assert.equal(cancels.length,1);assert.equal(cancels[0].token,'private-token');
    assert.equal(cancels[0].value.kind,'tts');assert.equal(cancels[0].value.client,oldWork.client);
    assert.equal(cancels[0].value.sequence,oldWork.sequence);
    assert.equal(newWork.client,oldWork.client);assert(newWork.sequence>oldWork.sequence);
    audio[0].resolve(response);audio[1].resolve(response);
    assert.equal(await first,null);assert(await next);
  }finally{playback.stop();global.fetch=original;}
});

test('denied permission reports immediately even if the audio device never resumes',async()=>{
  const original=Object.getOwnPropertyDescriptor(global,'navigator');
  Object.defineProperty(global,'navigator',{configurable:true,value:{mediaDevices:{getUserMedia:async()=>{throw new DOMException('Denied','NotAllowedError');}}}});
  let timer;
  try{
    const recognition=new LocalRecognition(()=>'token'),errors=[];
    recognition.active=true;recognition.onerror=event=>errors.push(event.error);
    await Promise.race([recognition.startCapture(0,new Promise(()=>{})),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('Permission error was held behind audio initialization')),100);})]);
    assert.deepEqual(errors,['not-allowed']);assert.equal(recognition.active,false);
  }finally{clearTimeout(timer);if(original)Object.defineProperty(global,'navigator',original);else delete global.navigator;}
});

test('audio suspension discards pending capture and cannot resume from a stale event',async()=>{
  const saved=global.fetch;
  try{
    for(const state of ['suspended','interrupted','closed']){
      let resolve,stopped=0,ended=0,aborted=false;const errors=[],results=[];
      mockVoiceFetch((_,options)=>new Promise(done=>{resolve=done;options.signal.addEventListener('abort',()=>{aborted=true;});}));
      const recognition=new LocalRecognition(()=>'token'),context=new EventTarget();
      Object.assign(context,{state:'running',sampleRate:16000});
      recognition.context=context;recognition.buffer=new VoiceBuffer(16000);recognition.active=true;recognition.phase='listening';
      const track={enabled:true,stop(){stopped++;}};
      recognition.stream={getTracks:()=>[track],getAudioTracks:()=>[track]};
      recognition.onend=()=>ended++;recognition.onerror=event=>errors.push(event.error);recognition.onresult=event=>results.push(event);
      recognition.watchContext(0);const oldEvent=recognition.contextChanged;
      const pending=recognition.transcribe(new Float32Array(16000).fill(.1),0,true,1);
      context.state=state;context.dispatchEvent(new Event('statechange'));
      assert(aborted);assert.equal(stopped,1);assert.equal(ended,1);assert.equal(recognition.active,false);
      assert.deepEqual(errors,['audio-interrupted']);assert.equal(recognition.contextChanged,null);
      resolve({ok:true,json:async()=>({text:'Do not deliver this stale result'})});await pending;
      assert.deepEqual(results,[]);assert.equal(recognition.jobs.length,0);
      context.state='running';context.dispatchEvent(new Event('statechange'));
      assert.equal(recognition.active,false,'hardware recovery alone does not start another capture');
      recognition.active=true;context.state='closed';oldEvent();
      assert.equal(recognition.active,true,'the previous call cannot interrupt a new generation');
      recognition.abort();
    }
  }finally{global.fetch=saved;}
});

test('explicit resume recreates a closed audio context and its worklet module',()=>{
  const saved=global.AudioContext;let contexts=0,resumes=0;
  global.AudioContext=class{constructor(){contexts++;this.state='suspended';}resume(){resumes++;this.state='running';return Promise.resolve();}};
  try{
    const recognition=new LocalRecognition(()=>'token');
    recognition.context={state:'closed'};recognition.module=Promise.resolve();
    recognition.startCapture=()=>{};
    recognition.start();assert.equal(contexts,1);assert.equal(resumes,1);assert.equal(recognition.module,null);
    assert.equal(recognition.context.state,'running');recognition.abort();
  }finally{if(saved)global.AudioContext=saved;else delete global.AudioContext;}
});

test('late worklet failure from a closed context cannot invalidate the resumed call',async()=>{
  const navigator=Object.getOwnPropertyDescriptor(global,'navigator'),saved={AudioContext:global.AudioContext,AudioWorkletNode:global.AudioWorkletNode,fetch:global.fetch};
  let rejectOld;const contexts=[],errors=[];
  const node=()=>({connect(){},disconnect(){}});
  global.fetch=async()=>({ok:true,json:async()=>({ok:true})});
  global.AudioContext=class extends EventTarget{
    constructor(){super();this.state='suspended';this.sampleRate=16000;this.loads=0;this.destination={};const first=contexts.length===0;contexts.push(this);this.audioWorklet={addModule:()=>{this.loads++;return first?new Promise((_,reject)=>{rejectOld=reject;}):Promise.resolve();}};}
    resume(){this.state='running';return Promise.resolve();}
    createMediaStreamSource(){return node();}
    createGain(){return {...node(),gain:{value:1}};}
  };
  global.AudioWorkletNode=class{constructor(){Object.assign(this,node());this.port={close(){}};}};
  Object.defineProperty(global,'navigator',{configurable:true,value:{mediaDevices:{getUserMedia:async()=>{const track={enabled:true,stop(){}};return {getTracks:()=>[track],getAudioTracks:()=>[track]};}}}});
  const recognition=new LocalRecognition(()=>'token');recognition.onerror=event=>errors.push(event);
  const flush=()=>new Promise(resolve=>setImmediate(resolve));
  try{
    recognition.start();await flush();assert(rejectOld);
    recognition.abort();contexts[0].state='closed';
    recognition.start();await flush();assert(recognition.accepting);
    const resumedModule=recognition.module;
    rejectOld(new Error('Old context closed while its module was loading'));await flush();
    assert.equal(recognition.module,resumedModule);assert(recognition.accepting);assert.deepEqual(errors,[]);
    recognition.abort();recognition.start();await flush();
    assert(recognition.accepting);assert.equal(contexts.length,2);assert.equal(contexts[1].loads,1,'resume reuses the registered worklet');
  }finally{
    recognition.abort();
    for(const [key,value] of Object.entries(saved)){if(value===undefined)delete global[key];else global[key]=value;}
    if(navigator)Object.defineProperty(global,'navigator',navigator);else delete global.navigator;
  }
});
