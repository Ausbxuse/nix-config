(() => {
  const section = document.createElement('section');
  section.id = 'backup-settings'; section.className = 'status-section'; section.hidden = true;
  section.innerHTML = '<h2>Recovery</h2><p id="backup-summary" role="status"></p><p id="backup-detail" class="muted"></p><div class="backup-actions"><button id="backup-download" class="text-button" type="button">Download recovery key</button><button id="backup-confirm" class="text-button" type="button" hidden>I’ve saved it elsewhere</button></div>';
  document.querySelector('.status-grid').append(section);
  const summary = document.getElementById('backup-summary'), detail = document.getElementById('backup-detail');
  const download = document.getElementById('backup-download'), confirm = document.getElementById('backup-confirm');
  let current = null, fetched = 0, pending = false, downloadedKey = '';
  const stamp = value => new Date(value * 1000).toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit', hour12:false});
  function render(value) {
    current = value; section.hidden = !value.configured;
    if (section.hidden) return;
    summary.textContent = value.error || (value.phase === 'saving' ? 'Saving encrypted backup…' : value.phase === 'checking' ? 'Checking restore…' : value.last_verified ? `Verified ${stamp(value.last_verified)} · ${value.target}` : 'Waiting for the first verified backup');
    detail.textContent = value.key_saved ? 'Manual restore · encrypted backups every 15 minutes while this host is online.' : 'Save the recovery download on your phone or in your password manager. Keep it off the backup host.';
    download.disabled = !value.kit_ready;
    confirm.hidden = value.key_saved || downloadedKey !== value.key_id;
  }
  async function refresh() {
    if (document.getElementById('status-view').hidden || pending || Date.now() - fetched < 15000) return;
    pending = true;
    try { render(await window.api('/api/backup')); fetched = Date.now(); }
    catch { if (!section.hidden) summary.textContent = 'Backup status is unavailable'; }
    finally { pending = false; }
  }
  download.onclick = async () => {
    if (!current?.kit_ready || download.disabled) return;
    const keyId = current.key_id;
    download.disabled = true;
    try {
      const response = await fetch('/api/backup/key', {method:'POST', headers:{'Content-Type':'application/json', 'X-Hub-Token':state.snapshot?.csrf_token || ''}, body:'{}'});
      if (!response.ok) throw Error((await response.json()).error || 'Download failed');
      const blob = await response.blob(), url = URL.createObjectURL(blob), link = document.createElement('a');
      link.href = url; link.download = 'agent-hub-recovery.zip'; document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      downloadedKey = keyId; render(current);
    } catch (error) { summary.textContent = error.message; }
    finally { download.disabled = !current?.kit_ready; }
  };
  confirm.onclick = async () => {
    confirm.disabled = true;
    try { render(await window.api('/api/backup/key-saved', {key_id:downloadedKey, saved:true})); }
    catch (error) { summary.textContent = error.message; }
    finally { confirm.disabled = false; }
  };
  new MutationObserver(refresh).observe(document.getElementById('status-view'), {attributes:true, attributeFilter:['hidden']});
  window.addEventListener('focus', refresh);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  window.HubBackup = {changed:refresh};
})();
