/* Endpoint timing on explicitly labelled speech/pause/noise fixtures.
   Compare packet size and silence duration without changing the live recognizer. */
const fs=require('node:fs');
const {VoiceBuffer}=require('./static/voice.js');
const results=[];
for(const silence of [.5,.8,1.2,1.5]){
  for(const packet of ['2048','20ms']){
    const rows=[];let noiseSends=0;
    for(const rate of [16000,44100,48000])for(const alignment of [0,.005,.017]){
      const size=packet==='2048'?2048:Math.round(rate*.02);
      for(const pause of [0,.2,.35,.45,.8,1,1.2]){
        const pieces=[[.3+alignment,0],[.9,.1],[pause,0],[.7,.1],[2,0]];
        const audio=Float32Array.from(pieces.flatMap(([seconds,level])=>Array(Math.round(seconds*rate)).fill(level)));
        const last=Math.round((.3+alignment+.9+pause+.7)*rate),buffer=new VoiceBuffer(rate,{silence});
        let final=null,at=0;
        for(let offset=0;offset+size<=audio.length;offset+=size){final=buffer.push(audio.subarray(offset,offset+size));if(final){at=offset+size;break;}}
        const kept=final?.reduce((sum,sample)=>sum+(sample>.05),0)||0;
        rows.push({rate,alignment,pause,early:at<last,delayMs:Math.round((at-last)*1000/rate),allSpeech:kept===Math.round(rate*1.6)});
      }
      for(const duration of [.05,.11,.16]){
        const samples=Float32Array.from([[.3+alignment,0],[duration,.1],[2,0]].flatMap(([s,v])=>Array(Math.round(s*rate)).fill(v)));
        const buffer=new VoiceBuffer(rate,{silence});
        for(let offset=0;offset+size<=samples.length;offset+=size)if(buffer.push(samples.subarray(offset,offset+size))){noiseSends++;break;}
      }
    }
    const delays=rows.filter(r=>!r.early).map(r=>r.delayMs).sort((a,b)=>a-b);
    results.push({silenceMs:silence*1000,packet,cases:rows.length,cutInstructions:rows.filter(r=>r.early||!r.allSpeech).length,
      medianDelayMs:delays[Math.floor(delays.length/2)],maxDelayMs:delays.at(-1),noiseSends,rows});
  }
}
if(process.argv[2])fs.writeFileSync(process.argv[2],JSON.stringify(results,null,2)+'\n');
console.log(JSON.stringify(results.map(({rows,...summary})=>summary)));
