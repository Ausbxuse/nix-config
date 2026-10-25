import hljs from 'highlight.js/lib/core';
import bash from 'highlight.js/lib/languages/bash';

const escape = text => String(text).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#x27;'}[c]));
const path = { scope: 'link', match: /(?:~\/|\.{1,2}\/|\/)[^\s"'`<>|;&()]+/, relevance: 0 };
hljs.registerLanguage('hub-command', api => {
  const shell = bash(api);
  return { ...shell, contains: [
    { match: [/(?:^\s*|[|;&]\s*)/, /[a-zA-Z_~./][\w~./+-]*/], scope: {2:'title'}, relevance: 0 },
    { scope: 'meta', match: /--?[a-zA-Z][\w-]*/, relevance: 0 },
    path,
    ...shell.contains,
    api.NUMBER_MODE,
  ] };
});
hljs.registerLanguage('hub-output', api => ({
  contains: [api.QUOTE_STRING_MODE, api.APOS_STRING_MODE, path, api.NUMBER_MODE],
}));

function highlight(text, language) {
  // Keep large captures cheap without dropping any output.
  if (text.length > 60000) return escape(text);
  try { return hljs.highlight(text, { language, ignoreIllegals: true }).value; }
  catch { return escape(text); }
}

export function highlightTerminal(text, kind = 'capture') {
  text = String(text);
  if (kind === 'command') return highlight(text, 'hub-command');
  if (kind === 'output') return highlight(text, 'hub-output');
  const match = text.match(/^(Ran |Failed \(exit -?\d+\) |Bash\()([^\n]*)([\s\S]*)$/);
  if (!match) return highlight(text, 'hub-output');
  const [, label, command, output] = match;
  return `<span class="terminal-label">${escape(label)}</span>${highlight(command, 'hub-command')}${highlight(output, 'hub-output')}`;
}
