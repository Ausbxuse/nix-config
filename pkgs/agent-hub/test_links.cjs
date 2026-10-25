const { test } = require('node:test');
const assert = require('node:assert/strict');
const MarkdownIt = require('markdown-it');

async function renderer() {
  const { fileLinks } = await import('./frontend/links.mjs');
  const md = new MarkdownIt({ html: false, linkify: true }).use(fileLinks);
  md.linkify.set({ fuzzyLink: false, fuzzyEmail: false });
  return md;
}

test('website links preserve fragments and balanced parentheses, excluding sentence punctuation', async () => {
  const md = await renderer();
  const url = 'https://ghostty.org/docs/config/reference#mouse-scroll-multiplier';
  const html = md.render(`See (${url}). Also https://example.com/a_(b).`);
  assert(html.includes(`<a href="${url}">${url}</a>).`));
  assert(html.includes('href="https://example.com/a_(b)"'));
});

test('bare and inline-code file references keep the line label while opening the file', async () => {
  const md = await renderer();
  const html = md.render('Updated modules/home/ghostty/default.nix:122. See `src/main.py:12:3`, README.md and .gitignore.');
  assert(html.includes('href="modules/home/ghostty/default.nix" title="modules/home/ghostty/default.nix:122"'));
  assert(html.includes('>modules/home/ghostty/default.nix:122</a>.'));
  assert(html.includes('href="src/main.py" title="src/main.py:12:3"><code>src/main.py:12:3</code></a>'));
  assert(html.includes('href="README.md"'));
  assert(html.includes('href=".gitignore"'));
});

test('file references do not nest inside links or turn commands and fenced code into links', async () => {
  const md = await renderer();
  const html = md.render('[src/main.py](src/main.py) https://example.com/file.py\n\n`cat file.py`\n\n```python\nvalue = "file.py"\n```');
  assert.equal((html.match(/<a /g) || []).length, 2);
  assert(html.includes('<code>cat file.py</code>'));
  assert(html.includes('value = &quot;file.py&quot;'));
  assert.doesNotMatch(md.render('$ rm -rf /tmp\napi/health\nv1.2.3'), /<a /);
});

test('local paths decode safely and never convert external URLs or unsafe schemes to files', async () => {
  const { localFilePath } = await import('./frontend/links.mjs');
  assert.equal(localFilePath('src/My%20File.nix:12'), 'src/My File.nix');
  assert.equal(localFilePath('/home/zhenyu/project/main.py#L14-L22'), '/home/zhenyu/project/main.py');
  for (const path of ['https://example.com/a.py', '//example.com/a.py', 'javascript:alert.py', 'file:///tmp/a.py', 'a%00.py', '%zz.py', '~/project/a.py']) assert.equal(localFilePath(path), null, path);
  assert.doesNotMatch((await renderer()).render('[click](javascript:alert(1))'), /<a /);
});
