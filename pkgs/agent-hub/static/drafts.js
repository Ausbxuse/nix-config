/* Merge only this tab's edits; a reload also retains this tab's own draft. */
((root)=>{
  function read(storage,key){try{const value=JSON.parse((typeof storage==='function'?storage():storage).getItem(key));return value&&typeof value==='object'&&!Array.isArray(value)?value:{};}catch{return {};}}
  function write(storage,key,value){try{(typeof storage==='function'?storage():storage).setItem(key,JSON.stringify(value));}catch{}}
  class Drafts {
    constructor(storage,session,key){
      this.storage=storage;this.session=session;this.key=key;
      this.value={...read(storage,key),...read(session,key)};this.baseline={...this.value};
    }
    save(value){
      const shared=read(this.storage,this.key),own=read(this.session,this.key);
      for(const key of new Set([...Object.keys(this.baseline),...Object.keys(value)])){
        const before=this.baseline[key] || '',after=value[key] || '';
        if(before===after)continue;
        own[key]=after;
        if(after)shared[key]=after;
        else if(shared[key]===before)delete shared[key];
      }
      this.value={...value};this.baseline={...value};
      write(this.storage,this.key,shared);write(this.session,this.key,own);
    }
  }
  if(typeof module==='object'&&module.exports)module.exports={Drafts};else root.HubDrafts={Drafts};
})(globalThis);
