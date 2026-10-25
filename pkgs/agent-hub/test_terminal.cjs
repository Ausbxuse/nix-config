const test = require('node:test');
const assert = require('node:assert/strict');
const reader = require('./static/reader.js');
const sample = 'Ran busctl --user --no-pager call :1.301 /com/mitchellh/ghostty org.gtk.Actions List\n└ as 6 "open-config" "present-surface" "quit" "new-window-command" "new-window" "reload-config"';
const plain = html => html.replace(/<\/?span[^>]*>/g, '').replace(/&(?:amp|lt|gt|quot|#x27|#39);/g, value => ({'&amp;':'&','&lt;':'<','&gt;':'>','&quot;':'"','&#x27;':"'",'&#39;':"'"}[value]));

test('terminal commands and returned values have distinct tokens without changing their text', async () => {
  const { highlightTerminal } = await import('./frontend/terminal.mjs');
  const html = highlightTerminal(sample);
  assert.match(html, /hljs-title">busctl<\/span>/);
  assert.match(html, /hljs-meta">--user<\/span>/);
  assert.match(html, /hljs-link">\/com\/mitchellh\/ghostty<\/span>/);
  assert.match(html, /hljs-number">6<\/span>/);
  assert.match(html, /hljs-string">&quot;open-config&quot;<\/span>/);
  assert.equal(plain(html), sample);
});

test('shell quoting, pipelines, and untrusted output remain literal', async () => {
  const { highlightTerminal } = await import('./frontend/terminal.mjs');
  for (const text of [
    'Ran printf "%s\\n" "$HOME" | rg --line-number "hello world"',
    'Failed (exit 1) cat ./missing.txt\n└ No such file',
    'Bash(echo "<script>alert(1)</script>")\n└ <img src=x onerror=bad()>',
    'Read 2 files\n  \\"escaped quote and tab\tend',
  ]) {
    const html = highlightTerminal(text);
    assert.equal(plain(html), text);
    assert.doesNotMatch(html, /<(?:script|img|a)\b/);
  }
  const huge = 'output "<img>" 42\n'.repeat(5000);
  assert.equal(plain(highlightTerminal(huge, 'output')), huge);
});

test('collapsed summaries and expanded tool output both use highlighting', async () => {
  const { highlightTerminal } = await import('./frontend/terminal.mjs');
  global.HubMarkdown = { render: text => text, highlightTerminal };
  try {
    const html = reader.render('• ' + sample, [], true);
    assert.equal((html.match(/hljs-title">busctl/g) || []).length, 2);
    assert.match(html, /reader-details-body"><pre class="reader-code"[^>]*><code class="terminal-syntax">/);
    const native = reader.render('', [{role:'tool',kind:'command',text:'busctl --user list'}, {role:'tool',kind:'command-output',text:'as 6 "quit"'}], true);
    assert.match(native, /hljs-meta">--user/);
    assert.match(native, /hljs-string">&quot;quit&quot;/);
  } finally { delete global.HubMarkdown; }
});

test('the terminal view preserves literal text without inventing syntax colours',()=>{
  const {render}=require('./static/terminal.js');
  const text='› Compare the checkpoints.\n\n• '+sample+'\n\n  checkpoint    score\n  quantile      0.859\n\n';
  const html=render(text);
  assert.equal(plain(html),text);
  assert.doesNotMatch(html,/terminal-prompt|hljs-|style=/);
  assert.doesNotMatch(html,/<(?:button|a|details)\b/);
});

test('capture styling never interprets terminal text as HTML',()=>{
  const {render}=require('./static/terminal.js');
  const text='› Show "<img src=x onerror=bad()>" & \'quotes\'\n• Ran echo "<script>bad()</script>"\n  └ <a href="javascript:bad()">test</a>\n';
  for(const html of [render(text),render('\x1b[31m'+text+'\x1b[0m')]){
    assert.equal(plain(html),text);assert.doesNotMatch(html,/<(?:img|script|a)\b/);
  }
});

test('original colours and style state survive line breaks and selective resets',()=>{
  const {render,plain:copy}=require('./static/terminal.js');
  const text='\x1b[1;31;44mred bold on blue\nnext\x1b[22;39mnormal on blue\x1b[49m default\x1b[0m\n';
  const html=render(text);
  assert.match(html,/<span style="color:var\(--ansi-1\);background-color:var\(--ansi-4\);font-weight:600">red bold on blue<\/span>\n/);
  assert.match(html,/font-weight:600">next<\/span><span style="background-color:var\(--ansi-4\)">normal on blue<\/span> default\n$/);
  assert.equal(copy(text),'red bold on blue\nnextnormal on blue default\n');
  assert.equal(plain(html),copy(text));
});

test('all standard, extended and truecolour SGR modes retain their values',()=>{
  const {render}=require('./static/terminal.js');
  assert.match(render('\x1b[94;103mbright'),/color:var\(--ansi-12\);background-color:var\(--ansi-11\)/);
  assert.match(render('\x1b[38;5;196;48;5;244mcube'),/color:rgb\(255,0,0\);background-color:rgb\(128,128,128\)/);
  assert.match(render('\x1b[38;5;16mblack\x1b[38;5;231mwhite'),/rgb\(0,0,0\).*rgb\(255,255,255\)/);
  assert.match(render('\x1b[38;2;137;180;250;48;2;51;53;62mCodex'),/color:rgb\(137,180,250\);background-color:rgb\(51,53,62\)/);
  assert.match(render('\x1b[38:2::137:180:250mcolon'),/color:rgb\(137,180,250\)/);
  assert.match(render('\x1b[38:2:137:180:250mcolon'),/color:rgb\(137,180,250\)/);
  assert.match(render('\x1b[48:5:4mindexed'),/background-color:var\(--ansi-4\)/);
  assert.equal(render('\x1b[38;2;999;0;0mbad\x1b[0m'), 'bad');
});

test('dim affects text without fading its background; inverse, underline and italic render',()=>{
  const {render}=require('./static/terminal.js');
  assert.match(render('\x1b[2;48;2;60;62;71mdim'),/color:color-mix\(in srgb,var\(--terminal-text\) 50%,transparent\);background-color:rgb\(60,62,71\)/);
  assert.match(render('\x1b[7minverse'),/color:var\(--terminal-bg\);background-color:var\(--terminal-text\)/);
  assert.match(render('\x1b[31;44;7mswap'),/color:var\(--ansi-4\);background-color:var\(--ansi-1\)/);
  assert.match(render('\x1b[3;4:3;9;58:5:1mstyle'),/font-style:italic;text-decoration-line:underline line-through;text-decoration-style:wavy;text-decoration-color:var\(--ansi-1\)/);
  assert.equal(render('\x1b[1;2;3;4;7;8;9m\x1b[0mreset'),'reset');
});

test('non-style terminal controls, links and clipboard escapes never run or leak',()=>{
  const {render,plain:copy}=require('./static/terminal.js');
  const text='before\x1b]8;;javascript:alert(1)\x1b\\label\x1b]8;;\x07\x1b]52;c;secret\x07\x1b[2Jafter\x1bPignored\x1b\\\x07\x1b[38;2;';
  assert.equal(render(text),'beforelabelafter');assert.equal(copy(text),'beforelabelafter');
  assert.equal(render('\x1b[31m<img onerror="bad()">\x1b[0m').includes('<img'),false);
});
