/* Apply local appearance before first paint; no account or server state involved. */
((root) => {
  const storageKey = 'hub-appearance-v1';
  const defaults = Object.freeze({size:100,font:'maple',spacing:'default',contrast:'default',motion:'full'});
  const choices = {font:['maple','sans','mono'],spacing:['compact','default','relaxed'],contrast:['default','high'],motion:['full','subtle','system','reduce']};
  function normalize(value) {
    const input = value && typeof value === 'object' ? value : {};
    const size = typeof input.size === 'number' && Number.isFinite(input.size) ? Math.round(input.size / 5) * 5 : defaults.size;
    return {size:Math.min(130,Math.max(85,size)),...Object.fromEntries(Object.entries(choices).map(([key,allowed]) => [key,allowed.includes(input[key]) ? input[key] : defaults[key]]))};
  }
  function preferences(getStorage) {
    let value = {...defaults}, saved = true;
    function read() {
      try {
        const storage = getStorage(), raw = storage.getItem(storageKey);
        if (raw === null) value = normalize({motion:storage.getItem('hub-motion')});
        else {
          let stored;
          try { stored = JSON.parse(raw); } catch {}
          value = stored?.version === 1 ? normalize(stored) : {...defaults};
        }
        saved = true;
      } catch { saved = false; }
      return {...value};
    }
    function write(next) {
      value = normalize(next);
      try {
        const storage = getStorage();
        storage.setItem(storageKey,JSON.stringify({version:1,...value}));
        // Prevent a reset from resurrecting the old standalone motion choice.
        storage.removeItem('hub-motion');
        saved = true;
      } catch { saved = false; }
      return {...value};
    }
    read();
    return {read,update:patch => write({...value,...patch}),reset:() => write(defaults),get value() { return {...value}; },get saved() { return saved; }};
  }
  const exported = {defaults,normalize,preferences,storageKey};
  if (typeof module === 'object' && module.exports) { module.exports = exported; return; }

  const store = preferences(() => root.localStorage), document = root.document;
  let applied;
  function apply() {
    const next = store.value, detail = {previous:applied,value:next};
    if (applied && JSON.stringify(applied) !== JSON.stringify(next)) root.dispatchEvent(new CustomEvent('hub-appearance-changing',{detail}));
    document.documentElement.style.setProperty('--text-scale',String(next.size / 100));
    document.documentElement.dataset.largeText = String(next.size > 110);
    for (const key of Object.keys(choices)) document.documentElement.dataset[key] = next[key];
    const changed = applied && JSON.stringify(applied) !== JSON.stringify(next);
    applied = next;
    if (changed) root.dispatchEvent(new CustomEvent('hub-appearance-change',{detail}));
  }
  apply();
  root.HubAppearance = {get value() { return store.value; }};
  function mount() {
    const panel = document.getElementById('appearance-settings');
    if (!panel) return;
    panel.parentElement.prepend(panel);
    const controls = [...panel.querySelectorAll('[data-appearance]')];
    function render() {
      const value = store.value;
      for (const input of controls) if (input.value !== String(value[input.dataset.appearance])) input.value = String(value[input.dataset.appearance]);
      document.getElementById('appearance-size-value').value = `${value.size}%`;
      document.getElementById('appearance-size').setAttribute('aria-valuetext',`${value.size} percent`);
      document.getElementById('appearance-reset').disabled = Object.keys(defaults).every(key => value[key] === defaults[key]);
      document.getElementById('appearance-storage').textContent = store.saved ? 'Saved in this browser.' : 'Applied for now. Browser storage is unavailable.';
    }
    for (const input of controls) input.addEventListener(input.type === 'range' ? 'input' : 'change',() => {
      store.update({[input.dataset.appearance]:input.type === 'range' ? Number(input.value) : input.value});
      apply();render();
    });
    document.getElementById('appearance-reset').addEventListener('click',() => { store.reset();apply();render(); });
    root.addEventListener('storage',event => {
      if (event.key !== null && event.key !== storageKey && event.key !== 'hub-motion') return;
      try { if (event.storageArea !== root.localStorage) return; } catch { return; }
      store.read();apply();render();
    });
    render();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded',mount,{once:true});
  else mount();
})(globalThis);
