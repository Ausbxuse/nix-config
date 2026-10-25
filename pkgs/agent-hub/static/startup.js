/* Reveal the composed view with its first data, never the intermediate layout. */
(() => {
  const root=document.documentElement,screen=document.getElementById('startup');
  let done=false,domReady=false,failedAsset=false,scheduled=false;
  function waiting(message){
    if(done)return;
    document.getElementById('startup-status').textContent=failedAsset?'Couldn’t load the workspace.':message;
    document.getElementById('startup-retry').hidden=false;
    if(failedAsset)clearTimeout(timer);
  }
  const timer=setTimeout(()=>waiting('Still connecting…'),12000);
  const ready=()=>domReady && !failedAsset && window.HubFocus && typeof state!=='undefined' && state.snapshot &&
    (state.view!=='agent' || detail.text!==null || !document.getElementById('preview-error').hidden);
  function check(){
    if(done || scheduled || !ready())return;
    scheduled=true;
    requestAnimationFrame(()=>{
      scheduled=false;
      if(!ready())return;
      done=true;clearTimeout(timer);
      root.removeAttribute('data-starting');
      screen.setAttribute('data-ready','');
      // Retain the same DOM and measured reading position through the reveal.
      setTimeout(()=>{screen.hidden=true;},180);
    });
  }
  window.HubStartup={check,waiting};
  window.addEventListener('error',event=>{
    if(done || !(event.target instanceof Element) || !event.target.matches('script,link[rel=stylesheet]'))return;
    failedAsset=true;waiting('Couldn’t load the workspace.');
  },true);
  document.addEventListener('DOMContentLoaded',()=>{
    domReady=true;
    if(!window.HubFocus || [...document.querySelectorAll('link[rel=stylesheet]')].some(link=>!link.sheet)){
      failedAsset=true;waiting('Couldn’t load the workspace.');
    }else check();
  },{once:true});
})();
