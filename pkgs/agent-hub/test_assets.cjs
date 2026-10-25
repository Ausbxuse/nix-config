const test = require('node:test');
const assert = require('node:assert/strict');

test('local asset previews preserve the owning agent and encode paths without trusting links', async () => {
  const { localAsset } = await import('./frontend/assets.mjs');
  for (const [path, kind] of [['plots/trace.png', 'image'], ['trace.svg', 'image'], ['rollout.mp4', 'video'], ['report.pdf', 'document'], ['src/main.py:12', 'text']]) {
    const asset = localAsset(path, 'gpu-004:%2');
    assert.equal(asset.kind, kind);
    const url = new URL(asset.preview, 'http://localhost');
    assert.equal(url.searchParams.get('agent_id'), 'gpu-004:%2');
    assert.equal(url.searchParams.get('path'), path.replace(':12', ''));
    assert.equal(url.searchParams.get('preview'), path.endsWith('.svg') ? '1' : null);
    assert.equal(url.pathname, kind === 'image' && !path.endsWith('.svg') ? '/api/thumbnail' : path.endsWith('.svg') ? '/api/file' : '/api/media');
    assert.equal(Boolean(asset.thumbnail), ['image','video','document'].includes(kind) && !path.endsWith('.svg'));
    assert.equal(asset.file.startsWith('/api/media?'), kind === 'video');
  }
  assert.equal(localAsset('plots/100%25%20success.png', 'local').path, 'plots/100% success.png');
  for (const path of ['https://example.com/trace.png', '//evil/trace.png', 'javascript:trace.png', 'file:///private.png', 'trace%00.png']) {
    assert.equal(localAsset(path, 'local'), null);
  }
  assert.equal(localAsset('trace.png', ''), null);
});

test('previews choose available space and stay inside narrow and shifted viewports', async () => {
  const { previewPosition } = await import('./frontend/assets.mjs');
  for (const viewport of [{width:320,height:568}, {width:390,height:440}, {width:1440,height:900}, {width:390,height:300,top:100,left:12}]) {
    const width = Math.min(352, viewport.width - 16), height = Math.min(290, viewport.height - 16);
    for (const [x, y] of [[0,0], [viewport.width-30,0], [0,viewport.height-24], [viewport.width-30,viewport.height-24]]) {
      const anchor = {left:x+(viewport.left||0),top:y+(viewport.top||0),bottom:y+(viewport.top||0)+24};
      const point = previewPosition(anchor, viewport, width, height);
      assert(point.left >= (viewport.left||0)+8);
      assert(point.left+width <= (viewport.left||0)+viewport.width-8);
      assert(point.top >= (viewport.top||0)+8);
      assert(point.top+height <= (viewport.top||0)+viewport.height-8);
    }
  }
});

test('video posters wait until visible and release removed conversation elements', async () => {
  const {attachMediaPoster,pruneMediaPosters,localAsset}=await import('./frontend/assets.mjs');
  let callback;const observed=new Set();
  global.IntersectionObserver=class {
    constructor(fn){callback=fn;}
    observe(node){observed.add(node);}
    unobserve(node){observed.delete(node);}
  };
  const visible={isConnected:true},removed={isConnected:true},asset=localAsset('clip.mp4','host:task');
  attachMediaPoster(visible,asset);attachMediaPoster(removed,asset);
  assert.equal(visible.poster,undefined,'offscreen videos must not download a poster');
  callback([{target:visible,isIntersecting:true}]);
  assert.equal(visible.poster,asset.thumbnail);assert(!observed.has(visible));
  removed.isConnected=false;pruneMediaPosters();assert(!observed.has(removed));
  callback([{target:removed,isIntersecting:true}]);assert.equal(removed.poster,undefined);
  delete global.IntersectionObserver;
});
