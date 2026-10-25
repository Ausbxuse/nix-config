/* Apply server revisions without sharing mutable UI state with the cached base. */
((root)=>{
  function apply(base, packet){
    if(Object.hasOwn(packet,'value'))return structuredClone(packet.value);
    if(!base || packet.base!==base.version)throw new Error('Update base expired');
    let value=structuredClone(base.value);
    for(const [kind,path,...args] of packet.changes){
      if(path.some(key=>['__proto__','prototype','constructor'].includes(key)))throw new Error('Invalid update path');
      if(!path.length && kind==='set'){value=structuredClone(args[0]);continue;}
      const parent=path.slice(0,-1).reduce((node,key)=>node[key],value),key=path.at(-1);
      const target=path.length?parent[key]:value;
      if(kind==='set')parent[key]=structuredClone(args[0]);
      else if(kind==='delete')delete parent[key];
      else if(kind==='splice')target.splice(args[0],args[1],...structuredClone(args[2]));
      else if(kind==='text')parent[key]=target.slice(0,args[0])+args[2]+target.slice(args[0]+args[1]);
      else throw new Error('Unknown update operation');
    }
    return value;
  }
  const exports={apply};
  if(typeof module==='object' && module.exports)module.exports=exports;else root.HubUpdates=exports;
})(globalThis);
