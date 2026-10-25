/* Capture mono PCM without browser-specific speech or recording services. */
class HubVoiceCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    // A fixed duration keeps endpoint timing and short-noise rejection stable
    // across device rates. 2048 samples could delay a 16 kHz input by 128 ms.
    this.size=Math.round(sampleRate*.02);
    this.buffer=new Float32Array(this.size);this.offset=0;this.silence=new Float32Array(128);
  }
  process(inputs) {
    // Firefox can omit a silent input. Keep the silence clock running so a
    // finished utterance does not stay open after the source becomes quiet.
    const samples=inputs[0]?.[0] || this.silence;
    for(const sample of samples){
      this.buffer[this.offset++]=sample;
      if(this.offset===this.buffer.length){
        this.port.postMessage(this.buffer,[this.buffer.buffer]);
        this.buffer=new Float32Array(this.size);this.offset=0;
      }
    }
    return true;
  }
}
registerProcessor('hub-voice-capture',HubVoiceCapture);
