/* Local transcription adapter: microphone → PCM WAV → this hub. */
((root)=>{
  const speechClient = () => root.crypto?.randomUUID?.() || 'voice-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
  function cancelSpeech(token, kind, client, sequence) {
    if (!sequence) return;
    fetch('/api/voice/cancel', {method:'POST', headers:{'Content-Type':'application/json','X-Hub-Token':token()},
      body:JSON.stringify({kind,client,sequence}), keepalive:true, signal:root.AbortSignal?.timeout?.(5000)}).catch(()=>{});
  }
  function wav(samples,rate) {
    const length=Math.min(30*16000,Math.floor(samples.length*16000/rate));
    const buffer=new ArrayBuffer(44+length*2), view=new DataView(buffer);
    const text=(offset,value)=>{for(let i=0;i<value.length;i++)view.setUint8(offset+i,value.charCodeAt(i));};
    text(0,'RIFF');view.setUint32(4,36+length*2,true);text(8,'WAVE');text(12,'fmt ');
    view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);
    view.setUint32(24,16000,true);view.setUint32(28,32000,true);view.setUint16(32,2,true);view.setUint16(34,16,true);
    text(36,'data');view.setUint32(40,length*2,true);
    // Average the source interval when downsampling to reduce aliasing.
    for(let i=0;i<length;i++){
      const start=i*rate/16000,end=(i+1)*rate/16000;
      let total=0,weight=0;
      for(let j=Math.floor(start);j<Math.ceil(end);j++){
        const w=Math.min(j+1,end)-Math.max(j,start);total+=(samples[j] || 0)*w;weight+=w;
      }
      const sample=Math.max(-1,Math.min(1,total/weight));
      view.setInt16(44+i*2,Math.round(sample*(sample<0?32768:32767)),true);
    }
    return buffer;
  }
  class VoiceBuffer {
    constructor(rate,{onset=.12,threshold=.01,multiplier=3,silence=1.5}={}){this.rate=rate;this.onset=onset;this.threshold=threshold;this.multiplier=multiplier;this.silence=silence;this.floor=.003;this.reset();}
    reset(){this.pre=[];this.chunks=[];this.count=0;this.lastLoud=0;this.lead=0;this.voiced=0;this.quiet=0;this.started=false;}
    push(chunk){
      const seconds=chunk.length/this.rate;
      const rms=Math.sqrt(chunk.reduce((total,value)=>total+value*value,0)/chunk.length);
      this.level=Math.min(1,rms*12);
      const loud=rms>Math.max(this.threshold,this.floor*this.multiplier);
      if(!this.started){
        this.pre.push(chunk);this.lead+=chunk.length;
        while(this.lead-this.pre[0].length>this.rate*.45)this.lead-=this.pre.shift().length;
        this.voiced=loud?this.voiced+seconds:0;
        if(!loud)this.floor=this.floor*.95+Math.min(rms,.012)*.05;
        if(this.voiced+1e-6<this.onset)return null;
        this.started=true;this.chunks=this.pre;this.pre=[];this.count=this.lead;
      }else{this.chunks.push(chunk);this.count+=chunk.length;if(loud)this.voiced+=seconds;}
      if(loud)this.lastLoud=this.count;
      this.quiet=loud?0:this.quiet+seconds;
      if(this.quiet>=this.silence || this.count>=30*this.rate)return this.finish();
      return null;
    }
    snapshot(){
      // Keep 200 ms after speech. The last caption and final use identical audio
      // once silence starts, so the hub can reuse that transcript exactly.
      const length=Math.min(this.count,this.lastLoud+Math.round(.2*this.rate),30*this.rate),samples=new Float32Array(length);
      let offset=0;
      for(const chunk of this.chunks){const part=chunk.subarray(0,length-offset);samples.set(part,offset);offset+=part.length;if(offset===length)break;}
      return samples;
    }
    finish(){const samples=this.voiced>=.25?this.snapshot():null;this.reset();return samples;}
  }
  class LocalRecognition {
    constructor(token){this.token=token;this.client=speechClient();this.workSequence=0;this.generation=0;this.active=false;this.playing=false;this.blocked=false;this.interruptible=false;this.local=true;this.sequence=0;this.jobs=[];this.finalTurns=new Set();}
    get accepting(){return this.active && this.phase==='listening' && !this.playing && !this.blocked && !this.processing;}
    get monitoring(){return this.active && this.phase==='listening' && this.playing && this.interruptible && !this.processing;}
    get recording(){return this.accepting && Boolean(this.buffer?.started);}
    get processing(){return Boolean(this.inflight?.final || this.jobs.some(job=>job.final));}
    syncCapture(){
      const enabled=this.accepting || this.monitoring;
      for(const track of this.stream?.getAudioTracks() || [])track.enabled=enabled;
      if(!this.accepting)this.buffer?.reset();
      if(!this.monitoring)this.interruptionBuffer=null;
    }
    discardTranscripts(){
      cancelSpeech(this.token,'asr',this.client,this.workSequence);
      if(this.inflight){this.inflight.cancelled=true;this.inflight.controller.abort();this.inflight=null;}
      this.jobs=[];this.finalTurns.clear();this.buffer?.reset();
    }
    setBlocked(blocked){
      blocked=Boolean(blocked);
      if(blocked && !this.blocked)this.discardTranscripts();
      this.blocked=blocked;this.syncCapture();
    }
    setPlayback(playing){
      playing=Boolean(playing);
      if(playing && !this.playing)this.discardTranscripts();
      if(this.playing!==playing)this.buffer?.reset();
      this.playing=playing;this.syncCapture();
    }
    setInterruptible(enabled){this.interruptible=Boolean(enabled);this.syncCapture();}
    start(){
      if(this.active)throw new DOMException('Microphone is already active.','InvalidStateError');
      this.active=true;this.phase='starting';const generation=++this.generation;
      try{
        const AudioContext=root.AudioContext || root.webkitAudioContext;
        if(this.context?.state==='closed')this.context=this.module=null;
        this.context ||= new AudioContext();
        this.startCapture(generation,this.context.resume());
      }catch(error){this.fail(error,generation);}
    }
    async startCapture(generation,resume){
      let stream;
      try{
        const resumed=Promise.resolve(resume);resumed.catch(()=>{});
        const permission=root.navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false}).then(stream=>{
          if(generation!==this.generation)stream.getTracks().forEach(track=>track.stop());
          else this.stream=stream;
          return stream;
        });
        stream=await permission;
        if(generation!==this.generation){stream.getTracks().forEach(track=>track.stop());return;}
        this.stream=stream;
        this.setPlayback(this.playing);
        // Loading runs beside capture and never holds microphone startup.
        fetch('/api/voice/warmup',{method:'POST',headers:{'Content-Type':'application/json','X-Hub-Token':this.token()},body:'{}',signal:root.AbortSignal?.timeout?.(10000)}).catch(()=>{});
        await resumed;
        if(generation!==this.generation)return;
        if(!this.module){
          const module=this.context.audioWorklet.addModule('/voice-capture.js').catch(error=>{if(this.module===module)this.module=null;throw error;});
          this.module=module;
        }
        await this.module;
        if(generation!==this.generation)return;
        this.buffer=new VoiceBuffer(this.context.sampleRate);
        this.source=this.context.createMediaStreamSource(stream);
        this.capture=new root.AudioWorkletNode(this.context,'hub-voice-capture');
        this.mute=this.context.createGain();this.mute.gain.value=0;
        this.capture.port.onmessage=event=>this.captureAudio(event.data,generation);
        this.source.connect(this.capture);this.capture.connect(this.mute);this.mute.connect(this.context.destination);
        stream.getAudioTracks()[0].onended=()=>{if(generation===this.generation && this.active)this.fail({name:'NotReadableError'},generation);};
        this.phase='listening';this.watchContext(generation);
        if(generation!==this.generation)return;
        this.syncCapture();this.onstart?.();
      }catch(error){stream?.getTracks().forEach(track=>track.stop());this.fail(error,generation);}
    }
    watchContext(generation){
      const context=this.context;
      this.contextChanged=()=>{
        if(generation!==this.generation || !this.active || this.context!==context)return;
        if(['suspended','interrupted','closed'].includes(context.state))this.fail({name:'AudioInterruptedError'},generation);
      };
      context.addEventListener('statechange',this.contextChanged);
      this.contextChanged();
    }
    captureAudio(chunk,generation=this.generation){
      if(generation!==this.generation)return;
      if(this.monitoring){
        // Use browser echo cancellation for speaker output. Require sustained,
        // louder input here than during normal listening to ignore brief noises
        // and quiet residual echo. Nothing is transcribed before interruption.
        const buffer=this.interruptionBuffer ||= new VoiceBuffer(this.buffer.rate,{onset:.24,threshold:.011,multiplier:3.5});
        buffer.push(chunk);
        if(buffer.started){
          const onset=buffer.snapshot();this.interruptionBuffer=null;
          this.oninterrupt?.();
          if(generation!==this.generation || !this.accepting)return;
          // Preserve the first words across stopping playback and resetting the
          // capture gates. Replay only this bounded onset, in normal packets.
          const size=Math.round(buffer.rate*.02);
          for(let offset=0;offset<onset.length;offset+=size)this.captureAudio(onset.subarray(offset,offset+size),generation);
        }
        return;
      }
      if(!this.accepting)return;
      const started=this.buffer.started,samples=this.buffer.push(chunk);
      this.onlevel?.(this.buffer.level || 0);
      if(!started && this.buffer.started){
        this.turn=++this.sequence;this.nextPartial=this.buffer.rate*.9;this.lastPartialSize=0;
        this.jobs=this.jobs.filter(job=>job.final);
        this.onspeechstart?.({utterance:this.turn});
        if(generation!==this.generation || !this.accepting)return;
      }
      if(samples){
        this.transcribe(samples,generation,true,this.turn);
      }else if(started && !this.buffer.started){
        this.onnomatch?.({utterance:this.turn,silent:true});
      }else if(this.buffer.started && this.buffer.voiced>=.35 && (this.buffer.count>=this.nextPartial || this.buffer.quiet>=.2)){
        this.nextPartial=this.buffer.count+this.buffer.rate*1.2;
        const samples=this.buffer.snapshot();
        if(samples.length!==this.lastPartialSize){this.lastPartialSize=samples.length;this.transcribe(samples,generation,false,this.turn);}
      }
    }
    release(){
      if(this.contextChanged){this.context?.removeEventListener('statechange',this.contextChanged);this.contextChanged=null;}
      if(this.capture){this.capture.port.onmessage=null;this.capture.port.close();this.capture.disconnect();}
      this.source?.disconnect();this.mute?.disconnect();
      this.stream?.getTracks().forEach(track=>track.stop());
      this.source=this.capture=this.mute=this.stream=null;
    }
    transcribe(samples,generation=this.generation,final=true,utterance=this.turn){
      if(generation!==this.generation || !this.active || this.playing || this.blocked)return;
      this.jobs=this.jobs.filter(job=>job.final || job.utterance!==utterance);
      if(final)this.finalTurns.add(utterance);
      if(this.jobs.filter(job=>job.final).length>=3){this.onerror?.({error:'transcription',message:'Transcription is falling behind. Pause briefly, then try again.'});return;}
      const job={samples,generation,final,utterance,work:{client:this.client,sequence:++this.workSequence,turn:utterance || 0,mode:final?'final':'interim'}};
      // Submit the final audio immediately. The host gives it priority over
      // waiting captions; an already running caption can still seed its cache.
      if(final && this.inflight && !this.inflight.final){this.inflight.cancelled=true;this.inflight.controller.abort();this.inflight=null;}
      if(final)this.jobs.unshift(job);else this.jobs.push(job);
      this.syncCapture();
      if(final)this.onprocessing?.({utterance});
      return this.drain();
    }
    async drain(){
      if(this.inflight || !this.jobs.length)return;
      const job=this.inflight=this.jobs.shift(),{generation,final,utterance}=job;
      const controller=job.controller=new AbortController();
      const timeout=setTimeout(()=>controller.abort(),25000);
      try{
        let response;
        for(let attempt=0;attempt<4;attempt++){
          response=await fetch('/api/voice/transcribe',{method:'POST',headers:{'Content-Type':'audio/wav','X-Hub-Token':this.token(),'X-Hub-Speech-Work':JSON.stringify(job.work)},body:wav(job.samples,this.context.sampleRate),signal:controller.signal});
          if(response.status!==429 || !final || attempt===3)break;
          await response.arrayBuffer();await new Promise(resolve=>setTimeout(resolve,350));
          if(generation!==this.generation || job.cancelled)return;
        }
        const result=await response.json();
        if(!response.ok)throw new Error(result.error || 'Local transcription failed.');
        if(generation!==this.generation || job.cancelled || (!final && (this.finalTurns.has(utterance) || utterance!==this.turn)))return;
        if(result.text){
          const alternative=[{transcript:result.text,confidence:1}];alternative.isFinal=final;
          this.onresult?.({results:[alternative],resultIndex:0,utterance});
        }else if(final)this.onnomatch?.({utterance});
      }catch(error){
        if(generation===this.generation && !job.cancelled && final)this.onerror?.({error:'transcription',message:error.name==='AbortError'?'Transcription timed out. Try a shorter message.':error.message});
      }finally{
        clearTimeout(timeout);
        if(this.inflight===job){
          this.inflight=null;
          this.syncCapture();
          if(final){this.finalTurns.delete(utterance);this.onsettled?.({utterance});}
          this.drain();
        }
      }
    }
    fail(error,generation){
      if(generation!==this.generation)return;
      const code=error.name==='AudioInterruptedError'?'audio-interrupted':['NotAllowedError','SecurityError'].includes(error.name)?'not-allowed':['NotFoundError','NotReadableError'].includes(error.name)?'audio-capture':'capture-start';
      this.abort();this.onerror?.({error:code,message:error.message || 'Microphone capture failed. Tap to retry.'});
    }
    stop(){if(this.recording){const samples=this.buffer.finish();if(samples)return this.transcribe(samples);}}
    abort(){
      cancelSpeech(this.token,'asr',this.client,this.workSequence);
      const active=this.active;this.generation++;this.inflight?.controller.abort();this.inflight=null;this.jobs=[];this.finalTurns.clear();
      this.release();this.buffer?.reset();this.interruptionBuffer=null;this.interruptible=false;this.active=false;this.phase='idle';if(active)this.onend?.();
    }
    close(){this.abort();this.context?.close().catch(()=>{});this.context=this.module=null;}
  }
  function readyCue(context){
    let finish;
    const finished=new Promise(resolve=>finish=resolve);
    if(!context || context.state!=='running'){finish();return {finished,stop(){}};}
    const oscillator=context.createOscillator(),gain=context.createGain(),at=context.currentTime;
    oscillator.type='sine';oscillator.frequency.setValueAtTime(740,at);oscillator.frequency.exponentialRampToValueAtTime(920,at+.07);
    gain.gain.setValueAtTime(0,at);gain.gain.linearRampToValueAtTime(.025,at+.012);gain.gain.exponentialRampToValueAtTime(.0001,at+.085);
    oscillator.connect(gain);gain.connect(context.destination);
    oscillator.onended=()=>{oscillator.disconnect();gain.disconnect();finish();};
    oscillator.start(at);oscillator.stop(at+.09);
    return {finished,stop(){try{oscillator.stop();}catch{}}};
  }
  function speechChunks(text){
    const clean=String(text).replace(/```[\s\S]*?```/g,' See the code in the chat. ').replace(/\[([^\]]+)\]\([^)]*\)/g,'$1').replace(/[*#`]/g,'').replace(/https?:\/\/\S+/g,'the linked page').replace(/\s+/g,' ').trim().slice(0,2400);
    const sentences=root.Intl?.Segmenter?[...new Intl.Segmenter('en',{granularity:'sentence'}).segment(clean)].map(item=>item.segment):clean.match(/[^.!?]+[.!?]*\s*/g)||[];
    const chunks=[];
    for(let sentence of sentences){
      sentence=sentence.trim();
      while(sentence.length>220){let end=sentence.lastIndexOf(' ',220);if(end<60)end=220;chunks.push(sentence.slice(0,end));sentence=sentence.slice(end).trim();}
      // Keep short related sentences together so the voice has enough context
      // for natural phrasing, while bounding time to the first spoken phrase.
      // Start after the first short sentence; prepare the rest during playback.
      // Very short related sentences still stay together for natural prosody.
      const limit=chunks.length===1?80:160;
      if(sentence && chunks.length && chunks.at(-1).length + sentence.length < limit) chunks[chunks.length-1]+=' '+sentence;
      else if(sentence)chunks.push(sentence);
    }
    // Start a long opening at an existing clause boundary. Never split names,
    // decimals or an unpunctuated sentence just to make its first clip tiny.
    if(chunks[0]?.length>100){
      const opening=chunks[0],pauses=[...opening.matchAll(/[,;:]\s+|[—–]\s+/g)];
      const pause=pauses.find(match=>match.index>=35 && match.index<=110 && opening.slice(0,match.index).split(/\s+/).length>=6);
      if(pause){const end=pause.index+pause[0].trimEnd().length;chunks.splice(0,1,opening.slice(0,end),opening.slice(end).trim());}
    }
    return chunks;
  }
  class LocalPlayback {
    constructor(token,context){this.token=token;this.client=speechClient();this.workSequence=0;this.context=context;this.generation=0;this.controllers=new Set();this.cache=new Map();this.warming=null;this.cacheToken=token();}
    cached(text){
      if(this.cacheToken!==this.token()){this.warming?.controller.abort();this.warming=null;this.cache.clear();this.cacheToken=this.token();}
      for(const [key,item] of this.cache)if(item.until<=Date.now())this.cache.delete(key);
      const item=this.cache.get(text);
      if(item){this.cache.delete(text);this.cache.set(text,item);return item.audio;}
      return null;
    }
    remember(text,audio){
      const bytes=audio.length*audio.numberOfChannels*4;
      this.cache.delete(text);this.cache.set(text,{audio,bytes,until:Date.now()+300000});
      let size=[...this.cache.values()].reduce((sum,item)=>sum+item.bytes,0);
      while(this.cache.size>16 || size>8*1024*1024){const key=this.cache.keys().next().value;size-=this.cache.get(key).bytes;this.cache.delete(key);}
    }
    clear(){this.stop();this.warming?.controller.abort();this.warming=null;this.cache.clear();}
    preload(text){
      const chunk=speechChunks(text)[0];
      if(!chunk || this.cached(chunk) || this.warming || this.controllers.size || this.source)return;
      // One small, silent preparation per call. It can finish while the user
      // talks, but never plays or acknowledges an action by itself.
      const job={text:chunk,controller:new AbortController(),work:{client:this.client,sequence:++this.workSequence,mode:'preload'}};this.warming=job;
      job.promise=this.fetchAudio(chunk,job.controller,job.work).then(audio=>{if(audio&&!job.controller.signal.aborted)this.remember(chunk,audio);return audio;}).catch(()=>null).finally(()=>{if(this.warming===job)this.warming=null;});
    }
    stop(keepWarm=''){
      const keep=this.warming?.text===keepWarm && Boolean(keepWarm);
      cancelSpeech(this.token,'tts',this.client,keep?this.warming.work.sequence-1:this.workSequence);
      if(this.warming && !keep){this.warming.controller.abort();this.warming=null;}
      this.generation++;for(const controller of this.controllers)controller.abort();this.controllers.clear();
      this.cancelStream?.();this.cancelStream=null;
      this.source?.stop();this.source=null;this.finish?.();this.finish=null;
      if(this.frame)root.cancelAnimationFrame(this.frame);this.frame=null;
    }
    async prepare(text,generation){
      const cached=this.cached(text);if(cached)return cached;
      if(this.warming?.text===text){const audio=await this.warming.promise;if(generation!==this.generation)return null;if(audio)return audio;}
      // A real answer takes priority over an unused warmup waiting on the host.
      else if(this.warming){cancelSpeech(this.token,'tts',this.client,this.warming.work.sequence);this.warming.controller.abort();this.warming=null;}
      const controller=new AbortController();this.controllers.add(controller);
      try{
        const audio=await this.fetchAudio(text,controller);
        if(generation!==this.generation || !audio)return null;
        this.remember(text,audio);return audio;
      }finally{this.controllers.delete(controller);}
    }
    async fetchAudio(text,controller,work={client:this.client,sequence:++this.workSequence,mode:'speech'}){
      const timer=setTimeout(()=>controller.abort(),25000);
      try{
        let response;
        for(let attempt=0;attempt<16;attempt++){
          response=await fetch('/api/voice/speak',{method:'POST',headers:{'Content-Type':'application/json','X-Hub-Token':this.token()},body:JSON.stringify({text,work}),signal:controller.signal});
          if(response.status!==429 || attempt===15)break;
          await response.arrayBuffer();await new Promise(resolve=>setTimeout(resolve,200));
          if(controller.signal.aborted)return null;
        }
        if(!response.ok){const result=await response.json();throw new Error(result.error || 'Local voice playback failed.');}
        const bytes=await response.arrayBuffer();
        if(controller.signal.aborted)return null;
        return await this.context().decodeAudioData(bytes);
      }finally{clearTimeout(timer);}
    }
    begin(text=''){
      this.stop(speechChunks(text)[0]);const generation=this.generation;
      let queue=[],closed=false,wake=null,characters=0;
      const cancel=()=>{queue=[];closed=true;wake?.();};this.cancelStream=cancel;
      const append=value=>{
        if(closed || generation!==this.generation)return false;
        const part=String(value).slice(0,Math.max(0,2400-characters));characters+=part.length;
        queue.push(...speechChunks(part));wake?.();return true;
      };
      const finish=()=>{closed=true;wake?.();};
      const take=async()=>{
        while(!queue.length && !closed && generation===this.generation)await new Promise(resolve=>{wake=resolve;});
        wake=null;
        return generation===this.generation?queue.shift():null;
      };
      const prepare=async()=>{
        const chunk=await take();if(!chunk || generation!==this.generation)return null;
        try{return {chunk,audio:await this.prepare(chunk,generation)};}catch(error){return {error};}
      };
      const run=async()=>{
        let next=prepare();
        while(true){
          const part=await next;
          if(generation!==this.generation)return false;
          if(!part)return true;
          if(part.error)throw part.error;
          if(!part.audio)return false;
          // At most one clip ahead. A sentence arriving during playback can be
          // prepared immediately without queuing synthesis for the whole answer.
          next=prepare();
          await this.play(part.audio,part.chunk,generation);
          if(generation!==this.generation)return false;
        }
      };
      append(text);
      const done=run().finally(()=>{if(this.cancelStream===cancel){cancel();this.cancelStream=null;}});
      return {append,finish,done};
    }
    speak(text){const stream=this.begin(text);stream.finish();return stream.done;}
    async play(audio,chunk,generation){
        const context=this.context();await context.resume();
        if(generation!==this.generation)return;
        await new Promise(resolve=>{
          const source=this.source=context.createBufferSource(),analyser=context.createAnalyser();analyser.fftSize=256;
          source.buffer=audio;source.connect(analyser);analyser.connect(context.destination);
          this.finish=()=>{source.disconnect();analyser.disconnect();resolve();};
          const samples=new Float32Array(analyser.fftSize);
          const meter=()=>{if(generation!==this.generation)return;analyser.getFloatTimeDomainData(samples);this.onlevel?.(Math.min(1,Math.sqrt(samples.reduce((v,s)=>v+s*s,0)/samples.length)*5));this.frame=root.requestAnimationFrame(meter);};
          source.onended=()=>{if(generation!==this.generation)return;root.cancelAnimationFrame(this.frame);this.source=null;this.finish?.();this.finish=null;};
          this.onstart?.(chunk);source.start();meter();
        });
    }
  }
  const supported=()=>Boolean(root.isSecureContext && root.navigator?.mediaDevices?.getUserMedia && (root.AudioContext || root.webkitAudioContext) && root.AudioWorkletNode);
  const voice={LocalRecognition,LocalPlayback,VoiceBuffer,wav,speechChunks,supported,readyCue};
  if(typeof module!=='undefined' && module.exports)module.exports=voice;
  root.HubVoice=voice;
})(typeof window==='undefined'?globalThis:window);
