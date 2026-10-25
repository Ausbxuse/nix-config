import { localFilePath } from './links.mjs';
import { renderFilePreview } from './file-preview.mjs';

const images = new Set(['png', 'jpg', 'jpeg', 'webp', 'gif', 'svg']);
const videos = new Set(['mp4', 'webm', 'ogv', 'mov']);

export function localAsset(path, agentId) {
  path = localFilePath(path);
  if (!path || !agentId) return null;
  const name = path.split('/').at(-1), extension = name.split('.').at(-1).toLowerCase();
  const kind = images.has(extension) ? 'image' : videos.has(extension) ? 'video' : extension === 'pdf' ? 'document' : 'text';
  const query = new URLSearchParams({ agent_id: agentId, path });
  const thumbnail = ['image','video','document'].includes(kind) && extension !== 'svg' ? '/api/thumbnail?' + query : null;
  return { name, path, extension, kind, file: (kind === 'video' ? '/api/media?' : '/api/file?') + query,
    thumbnail, preview: kind === 'image' ? thumbnail || '/api/file?' + query + '&preview=1' : '/api/media?' + query };
}

let posterObserver;
const posters = new Map();
export function pruneMediaPosters() {
  for (const video of posters.keys()) if (!video.isConnected) {
    posterObserver?.unobserve(video); posters.delete(video);
  }
}
export function attachMediaPoster(video, asset) {
  if (!asset?.thumbnail) return;
  if (!globalThis.IntersectionObserver) { video.poster = asset.thumbnail; return; }
  posterObserver ||= new IntersectionObserver(entries => {
    for (const entry of entries) if (entry.isIntersecting) {
      if (entry.target.isConnected && posters.has(entry.target)) entry.target.poster = posters.get(entry.target);
      posterObserver.unobserve(entry.target); posters.delete(entry.target);
    }
  }, {rootMargin:'120px'});
  posters.set(video, asset.thumbnail); posterObserver.observe(video);
}

export function previewPosition(anchor, viewport, width, height) {
  const margin = 8, gap = 8, left = viewport.left || 0, top = viewport.top || 0;
  const below = top + viewport.height - anchor.bottom - margin - gap;
  const above = anchor.top - top - margin - gap;
  const down = below >= height || below >= above;
  return { left: Math.max(left + margin, Math.min(anchor.left, left + viewport.width - width - margin)),
    top: Math.max(top + margin, Math.min(down ? anchor.bottom + gap : anchor.top - height - gap, top + viewport.height - height - margin)) };
}

export function createAssetPreviews(highlight, markdown) {
  const links = new WeakMap();
  let panel, body, title, open, close, active, controller, showTimer, hideTimer, observer;
  let pinned = false, keyboard = false, restoringFocus = false;
  const hovering = () => matchMedia('(any-hover: hover)').matches;
  const entry = target => target instanceof Element ? target.closest('a[data-asset-preview]') : null;
  const viewport = () => ({ width: window.visualViewport?.width || innerWidth, height: window.visualViewport?.height || innerHeight,
    left: window.visualViewport?.offsetLeft || 0, top: window.visualViewport?.offsetTop || 0 });

  function hide(restoreFocus = false) {
    clearTimeout(showTimer); clearTimeout(hideTimer);
    const previous = active;
    active = null; pinned = keyboard = false; controller?.abort(); observer?.disconnect();
    if (!panel) return;
    const video = panel.querySelector('video');
    if (video) { video.pause(); video.removeAttribute('src'); video.load(); }
    panel.querySelector('img')?.removeAttribute('src');
    restoringFocus = true;
    if (panel.open) panel.close();
    panel.hidden = true; body.replaceChildren();
    previous?.setAttribute('aria-expanded', 'false');
    if (restoreFocus && previous?.isConnected) {
      previous.focus({ preventScroll: true });
    }
    restoringFocus = false;
  }
  function scheduleHide() {
    clearTimeout(showTimer); clearTimeout(hideTimer);
    hideTimer = setTimeout(() => {
      if (!active || pinned || panel.matches(':hover') || active.matches(':hover') ||
          panel.contains(document.activeElement) || keyboard && document.activeElement === active) return;
      hide();
    }, 140);
  }
  function position() {
    if (!active?.isConnected) { hide(); return; }
    const bounds = viewport();
    panel.style.maxWidth = Math.max(0, bounds.width - 16) + 'px';
    panel.style.maxHeight = Math.max(0, bounds.height - 16) + 'px';
    panel.style.setProperty('--preview-height', Math.max(0, bounds.height - 70) + 'px');
    const point = pinned ? {left:bounds.left + (bounds.width - panel.offsetWidth) / 2,
      top:bounds.top + Math.max(8, (bounds.height - panel.offsetHeight) / 2)} :
      previewPosition(active.getBoundingClientRect(), bounds, panel.offsetWidth, panel.offsetHeight);
    panel.style.left = point.left + 'px'; panel.style.top = point.top + 'px';
  }
  function createPanel() {
    if (panel) return;
    panel = document.createElement('dialog'); panel.id = 'asset-preview'; panel.className = 'asset-preview'; panel.hidden = true;
    panel.setAttribute('aria-label', 'File preview');
    const header = document.createElement('header'); header.className = 'asset-preview-header';
    title = document.createElement('span'); title.className = 'asset-preview-name';
    open = document.createElement('a'); open.textContent = 'Original'; open.target = '_blank'; open.rel = 'noopener noreferrer';
    close = document.createElement('button'); close.type = 'button'; close.className = 'icon-button'; close.setAttribute('aria-label', 'Close preview');
    close.innerHTML = '<svg class="icon" aria-hidden="true"><use href="#i-close"/></svg>';
    close.onclick = () => hide(true);
    header.append(title, open, close);
    body = document.createElement('div'); body.className = 'asset-preview-body';
    panel.append(header, body); document.body.append(panel);
    panel.addEventListener('pointerenter', () => clearTimeout(hideTimer));
    panel.addEventListener('pointerleave', scheduleHide);
    panel.addEventListener('focusout', scheduleHide);
    panel.addEventListener('cancel', event => { event.preventDefault(); hide(true); });
    panel.addEventListener('click', event => {
      if (event.target !== panel) return;
      const rect = panel.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) hide(true);
    });
    new ResizeObserver(() => { if (active) position(); }).observe(panel);
    observer = new MutationObserver(() => {
      if (active && (!active.isConnected || active.closest('[hidden]'))) hide();
    });
  }
  function status(text, loading = false) {
    const note = document.createElement('p'); note.className = 'asset-preview-status'; note.textContent = text;
    note.setAttribute('role', 'status'); note.classList.toggle('loading', loading); body.replaceChildren(note);
  }
  async function show(link, pin = false, fromKeyboard = false) {
    clearTimeout(showTimer); clearTimeout(hideTimer);
    if (active === link) {
      keyboard ||= fromKeyboard;
      if (pin && !pinned) { pinned = true; panel.dataset.pinned = 'true'; panel.close(); panel.showModal(); position(); close.focus({preventScroll:true}); }
      return;
    }
    hide();
    const asset = links.get(link);
    if (!asset || !link.isConnected) return;
    createPanel(); active = link; pinned = pin; keyboard = fromKeyboard;
    controller = new AbortController(); const signal = controller.signal;
    title.textContent = asset.name; title.title = asset.path; open.href = asset.file;
    open.setAttribute('aria-label', 'Open ' + asset.name);
    link.setAttribute('aria-expanded', 'true'); panel.dataset.kind = asset.kind;
    panel.dataset.pinned = String(pinned);
    status('Loading preview…', true); panel.hidden = false;
    if (pinned) { panel.showModal(); close.focus({preventScroll:true}); }
    else panel.setAttribute('open', ''); // Hover must not move keyboard focus.
    position(); observer.observe(document.querySelector('.app-shell') || document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['hidden'] });
    const failed = message => { if (!signal.aborted) { status(message || 'Preview unavailable. Open the original file.'); position(); } };
    if (asset.kind === 'image' || asset.kind === 'video' || asset.kind === 'document') {
      const media = document.createElement(asset.kind === 'video' ? 'video' : 'img');
      media.hidden = true;
      const ready = () => {
        if (signal.aborted) return;
        body.querySelector('.asset-preview-status')?.remove(); media.hidden = false; position();
      };
      if (asset.kind !== 'video') {
        media.alt = asset.name; media.decoding = 'async'; media.onload = ready;
      } else {
        media.controls = true; media.muted = true; media.playsInline = true; media.loop = true; media.preload = 'metadata';
        media.onloadeddata = ready;
        media.onloadedmetadata = () => { ready(); if (!matchMedia('(prefers-reduced-motion: reduce)').matches) media.play().catch(() => {}); };
      }
      media.onerror = () => failed('Preview unavailable or unsupported. Open the original file.');
      body.append(media); media.src = asset.kind === 'document' ? asset.thumbnail : asset.preview;
      if (asset.kind === 'document') { const note = document.createElement('p'); note.className = 'asset-preview-more'; note.textContent = 'Page 1 · Original opens the full PDF'; body.append(note); }
      return;
    }
    const read = async (limit = 65536) => { try {
      const response = await fetch(asset.preview, { signal, headers: { Range: 'bytes=0-' + (limit - 1) } });
      if (!response.ok) throw new Error('Preview unavailable. The file may have moved or its host is offline.');
      const bytes = new Uint8Array(await response.arrayBuffer());
      if (signal.aborted) return;
      const size = Number(response.headers.get('Content-Range')?.split('/')[1] || response.headers.get('Content-Length') || bytes.length);
      const truncated = size > bytes.length;
      const text = new TextDecoder().decode(bytes, {stream:truncated});
      body.replaceChildren(renderFilePreview({text, extension:asset.extension, truncated, highlight,
        markdown:markdown ? value => markdown(value, asset) : null}));
      if (truncated) {
        const more = document.createElement('div'); more.className = 'asset-preview-more';
        const note = document.createElement('span'); note.textContent = 'First ' + Math.ceil(bytes.length / 1024) + ' KB of ' + (size >= 1048576 ? (size / 1048576).toFixed(1) + ' MB' : Math.ceil(size / 1024) + ' KB'); more.append(note);
        if (limit < 524288) { const load = document.createElement('button'); load.type = 'button'; load.textContent = 'Load more'; load.onclick = () => { load.disabled = true; load.textContent = 'Loading…'; read(Math.min(limit * 4, 524288)); }; more.append(load); }
        body.append(more);
      }
      position();
    } catch (error) { if (!signal.aborted) failed(error.message); } };
    await read();
  }

  document.addEventListener('pointerover', event => {
    const link = entry(event.target);
    if (pinned || !link || link.contains(event.relatedTarget) || event.pointerType === 'touch' || !hovering()) return;
    clearTimeout(showTimer); clearTimeout(hideTimer);
    showTimer = setTimeout(() => show(link), 180);
  });
  document.addEventListener('pointerout', event => {
    const link = entry(event.target);
    if (link && !link.contains(event.relatedTarget) && !panel?.contains(event.relatedTarget)) scheduleHide();
  });
  document.addEventListener('focusin', event => {
    const link = entry(event.target);
    if (!pinned && !restoringFocus && link?.matches(':focus-visible')) show(link, false, true);
  });
  document.addEventListener('focusout', event => { if (entry(event.target)) scheduleHide(); });
  document.addEventListener('click', event => {
    const link = entry(event.target);
    if (link && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey && !event.button) {
      event.preventDefault(); show(link, true); return;
    }
    if (active && !panel.contains(event.target) && event.target !== active && !active.contains(event.target)) hide();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && active) { event.preventDefault(); event.stopPropagation(); hide(panel.contains(document.activeElement)); }
    if (event.key === 'ArrowDown' && active && document.activeElement === active) { event.preventDefault(); open.focus(); }
  }, true);
  document.addEventListener('scroll', event => { if (active && !pinned && !panel.contains(event.target)) hide(); }, true);
  window.addEventListener('resize', () => { if (active) position(); });
  window.addEventListener('blur', () => { if (!pinned) hide(); });
  window.visualViewport?.addEventListener('resize', () => { if (active) position(); });
  document.addEventListener('visibilitychange', () => { if (document.hidden && !pinned) hide(); });
  const attach = (link, path, agentId) => {
    const asset = localAsset(path, agentId);
    if (!asset) return;
    links.set(link, asset); link.dataset.assetPreview = asset.kind;
    link.href = asset.file;
    link.setAttribute('aria-haspopup', 'dialog'); link.setAttribute('aria-expanded', 'false'); link.setAttribute('aria-controls', 'asset-preview');
    link.setAttribute('aria-description', 'File preview available. Press ArrowDown to use preview controls.');
    // The file name is in the preview; avoid a second native tooltip over it.
    link.removeAttribute('title');
  };
  attach.isOpenFor = root => Boolean(active && root.contains(active));
  return attach;
}
