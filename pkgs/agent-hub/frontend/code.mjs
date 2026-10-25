/* Shared syntax rendering for chat, captures and file previews. */
import hljs from 'highlight.js/lib/core';
import python from 'highlight.js/lib/languages/python';
import javascript from 'highlight.js/lib/languages/javascript';
import typescript from 'highlight.js/lib/languages/typescript';
import bash from 'highlight.js/lib/languages/bash';
import json from 'highlight.js/lib/languages/json';
import nix from 'highlight.js/lib/languages/nix';
import yaml from 'highlight.js/lib/languages/yaml';
import css from 'highlight.js/lib/languages/css';
import xml from 'highlight.js/lib/languages/xml';
import rust from 'highlight.js/lib/languages/rust';
import go from 'highlight.js/lib/languages/go';
import sql from 'highlight.js/lib/languages/sql';
import c from 'highlight.js/lib/languages/c';
import cpp from 'highlight.js/lib/languages/cpp';
import diff from 'highlight.js/lib/languages/diff';
import ini from 'highlight.js/lib/languages/ini';
import latex from 'highlight.js/lib/languages/latex';
import cmake from 'highlight.js/lib/languages/cmake';
import makefile from 'highlight.js/lib/languages/makefile';
import dockerfile from 'highlight.js/lib/languages/dockerfile';
import matlab from 'highlight.js/lib/languages/matlab';
import shellsession from 'highlight.js/lib/languages/shell';
import { highlightTerminal } from './terminal.mjs';

for (const [name, language] of Object.entries({python,javascript,typescript,bash,json,nix,yaml,css,xml,rust,go,sql,c,cpp,diff,ini,latex,cmake,makefile,dockerfile,matlab,shellsession})) hljs.registerLanguage(name, language);
const escape = text => String(text).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#x27;'}[c]));
const aliases = {py:'python',py3:'python',python3:'python',js:'javascript',jsx:'javascript',mjs:'javascript',cjs:'javascript',ts:'typescript',tsx:'typescript',sh:'bash',shell:'bash',zsh:'bash',command:'bash',console:'shellsession',terminal:'shellsession',yml:'yaml',rs:'rust',html:'xml',svg:'xml',vue:'xml',toml:'ini',cfg:'ini',conf:'ini',tex:'latex','c++':'cpp',h:'c',hxx:'cpp',cxx:'cpp',cc:'cpp',hpp:'cpp',patch:'diff',docker:'dockerfile',m:'matlab',jsonc:'json',jsonl:'json',ndjson:'json'};
const labels = {python:'Python',javascript:'JavaScript',typescript:'TypeScript',bash:'Shell',json:'JSON',nix:'Nix',yaml:'YAML',css:'CSS',xml:'HTML / XML',rust:'Rust',go:'Go',sql:'SQL',c:'C',cpp:'C++',diff:'Diff',ini:'INI / TOML',latex:'LaTeX',cmake:'CMake',makefile:'Makefile',dockerfile:'Dockerfile',matlab:'MATLAB',shellsession:'Console'};
const detectLanguages = ['python','javascript','bash','json','nix','yaml','rust','go','sql','cpp','diff'];
const cache = new Map();
let cacheSize = 0;
function languageHint(info) {
  const hint=String(info||'').trim().split(/\s+/)[0].replace(/^\{\.?|\}$/g,'').replace(/^language-/i,'').toLowerCase().slice(0,80);
  if (['text','txt','plaintext','plain','none','nohighlight','log','output'].includes(hint)) return {language:'',label:'Text',explicit:true};
  const name=hint.split(/[\\/]/).at(-1);
  const key=name==='cmakelists.txt'?'cmake':/^dockerfile(?:\.|$)/.test(name)?'dockerfile':name==='makefile'?'makefile':hint.includes('.')?hint.split('.').at(-1):hint;
  const language=aliases[key]||key;
  return {language:labels[language]?language:'',label:labels[language]||hint.slice(0,40)||'Code',explicit:Boolean(hint)};
}
export function highlightCode(value, info='') {
  const text=String(value??''),hint=languageHint(info),key=info+'\0'+text;
  if (cache.has(key)) { const result=cache.get(key);cache.delete(key);cache.set(key,result);return result; }
  let language=hint.language,html,detected=false;
  // Automatic detection is only for short, unlabelled code blocks. Never run
  // every language over long logs or colour ordinary conversation text.
  try {
    if (!hint.explicit && text.length<=12000 && text.trim()) {
      const result=hljs.highlightAuto(text,detectLanguages);
      if (result.language && result.relevance>=3) {language=result.language;html=result.value;detected=true;}
    }
    if (!html && language) {
      const prefix=text.length>60000?text.slice(0,60000):text;
      html=(language==='bash'?highlightTerminal(prefix,'command'):hljs.highlight(prefix,{language,ignoreIllegals:true}).value)+escape(text.slice(prefix.length));
    }
  } catch { html=undefined; }
  const result=Object.freeze({html:html??escape(text),language,label:detected?labels[language]:hint.label,detected});
  const size=key.length+result.html.length;
  if (size<=200000) {
    while (cache.size && (cache.size>=96 || cacheSize+size>1000000)) {
      const oldest=cache.keys().next().value;cacheSize-=oldest.length+cache.get(oldest).html.length;cache.delete(oldest);
    }
    cache.set(key,result);cacheSize+=size;
  }
  return result;
}
export function codeBlock(text, info='') {
  const result=highlightCode(text,info);
  return `<div class="md-code-block"><div class="code-toolbar"><span${result.detected?' title="Detected language"':''}>${escape(result.label)}</span><button type="button" data-copy-code>Copy</button></div><pre tabindex="0"><code class="hljs${result.language?' language-'+result.language:''}">${result.html}</code></pre></div>`;
}
