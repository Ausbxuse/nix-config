const test=require('node:test');
const assert=require('node:assert/strict');
const reader=require('./static/reader.js');
const plain=html=>html.replace(/<\/?span[^>]*>/g,'').replace(/&(?:amp|lt|gt|quot|#x27|#39);/g,value=>({'&amp;':'&','&lt;':'<','&gt;':'>','&quot;':'"','&#x27;':"'",'&#39;':"'"}[value]));

test('untagged code detects Python, JavaScript and JSON without changing its contents',async()=>{
  const {highlightCode}=await import('./frontend/code.mjs');
  for(const [language,text] of [
    ['python','def predict(x):\n    # Run the policy\n    return x * 0.5\n'],
    ['javascript','const value = 42;\nconsole.log("hello", value);\n'],
    ['json','{ "count": 42, "enabled": true }\n'],
  ]){
    const value=highlightCode(text);
    assert.equal(value.language,language);assert(value.detected);assert.match(value.html,/hljs-/);
    assert.equal(plain(value.html),text);
    assert.strictEqual(highlightCode(text),value,'Repeated history renders reuse the result');
  }
});

test('common aliases, file names and added research/configuration languages are highlighted',async()=>{
  const {highlightCode}=await import('./frontend/code.mjs');
  for(const [hint,language,text] of [
    ['{.python}','python','def policy(x): return True'],['language-ts','typescript','const ready: boolean = true;'],
    ['train.py','python','import torch\nprint(torch.zeros(2))'],['C','c','int main(void) { return 0; }'],
    ['c++','cpp','std::vector<int> values = {1, 2};'],['settings.toml','ini','[model]\nname = "sol"\nthreads = 4'],
    ['tex','latex','\\begin{equation}\nx = \\frac{1}{2}\n\\end{equation}'],
    ['Dockerfile','dockerfile','FROM python:3.14\nRUN pip install torch'],
    ['CMakeLists.txt','cmake','cmake_minimum_required(VERSION 3.20)\nproject(robot)'],
    ['Makefile','makefile','run:\n\tpython train.py'],['m','matlab','function y = policy(x)\ny = x.^2;\nend'],
    ['patch','diff','--- a/train.py\n+++ b/train.py\n@@ -1 +1 @@\n-old\n+new'],
  ]){
    const value=highlightCode(text,hint);
    assert.equal(value.language,language,hint);assert.match(value.html,/hljs-/,hint);assert.equal(plain(value.html),text,hint);
  }
});

test('shell code colours executable, flags, strings, variables and numeric arguments',async()=>{
  const {highlightCode}=await import('./frontend/code.mjs');
  const text='python train.py --seed 42 --output "$RESULT_DIR"';
  for(const hint of ['bash','sh','shell','zsh']){
    const {html}=highlightCode(text,hint);
    assert.match(html,/hljs-title">python/);assert.match(html,/hljs-meta">--seed/);
    assert.match(html,/hljs-number">42/);assert.equal(plain(html),text);
  }
});

test('explicit text and unknown languages remain literal; labels cannot inject HTML',async()=>{
  const {highlightCode,codeBlock}=await import('./frontend/code.mjs');
  const text='const x = "<script>bad()</script>";\n<img src=x onerror=bad()> & \'quote\'';
  for(const hint of ['text','plaintext','log','unknown-language']){
    const value=highlightCode(text,hint);
    assert.equal(value.language,'');assert.doesNotMatch(value.html,/hljs-|<(?:script|img)/);assert.equal(plain(value.html),text);
  }
  const value=highlightCode(text,'js');
  assert.doesNotMatch(value.html,/<(?:script|img)/);assert.equal(plain(value.html),text);
  assert.doesNotMatch(codeBlock(text,'"><img/src=x>'),/<img/);
});

test('large code keeps every character while bounding highlighting and detection work',async()=>{
  const {highlightCode}=await import('./frontend/code.mjs');
  const text='print("<image>", 42)\n'.repeat(5000);
  const value=highlightCode(text,'python');
  assert.match(value.html,/hljs-/);assert.equal(plain(value.html),text);
  const unknown=highlightCode(text);
  assert.equal(unknown.detected,false);assert.equal(plain(unknown.html),text);
});

test('code beside a terminal table uses the shared highlighter, including tilde fences',async()=>{
  const {codeBlock}=await import('./frontend/code.mjs');
  global.HubMarkdown={codeBlock};
  try{
    const table='┌─────┬─────┐\n│ Run │ Val │\n├─────┼─────┤\n│ One │ Two │\n└─────┴─────┘';
    for(const marker of ['```','~~~~']){
      const result=reader.prose(table+'\n\n'+marker+'python\ndef policy(x):\n    return True\n'+marker);
      assert.match(result,/reader-table/);assert.match(result,/language-python/);assert.match(result,/hljs-keyword/);
    }
    const result=reader.prose(table+'\n\n    def policy(x):\n        return True');
    assert.match(result,/language-python/);assert.match(result,/hljs-keyword/);
  }finally{delete global.HubMarkdown;}
});
