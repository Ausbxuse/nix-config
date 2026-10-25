// File contents stay data: no HTML evaluation, formula execution, or numeric rounding.
export function parseDelimited(text, delimiter = ',', truncated = false, limit = 1000) {
  text = text.replace(/^\uFEFF/, '');
  const rows = []; let row = [], field = '', quoted = false, closed = false;
  const cell = () => { row.push(field); field = ''; closed = false; };
  const line = () => { cell(); rows.push(row); row = []; };
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (quoted) {
      if (char === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (char === '"') { quoted = false; closed = true; }
      else field += char;
    } else if (char === delimiter) cell();
    else if (char === '\n' || char === '\r') {
      if (char === '\r' && text[i + 1] === '\n') i++;
      line();
      if (rows.length >= limit && i + 1 < text.length) return {rows, more:true};
    } else if (char === '"' && !field && !closed) quoted = true;
    else if (closed && !/[ \t]/.test(char)) throw Error('Unexpected text after a quoted field.');
    else if (!closed) field += char;
  }
  if (quoted && !truncated) throw Error('Unclosed quoted field.');
  if (!truncated && (row.length || field || closed)) line();
  return {rows, more:truncated};
}

export function delimitedPreview(text, extension, truncated = false) {
  let delimiter = extension === 'tsv' ? '\t' : ',';
  if (extension !== 'tsv') {
    let best = 0;
    for (const option of [',', ';', '\t']) {
      try {
        const {rows} = parseDelimited(text, option, truncated, 12);
        const counts = new Map();
        for (const row of rows) if (row.length > 1) counts.set(row.length, (counts.get(row.length) || 0) + 1);
        const score = Math.max(0, ...[...counts].map(([columns, count]) => count * 100 + Math.min(columns, 80)));
        if (score > best) { best = score; delimiter = option; }
      } catch { /* Try the other separators before the strict parse below. */ }
    }
  }
  return {...parseDelimited(text, delimiter, truncated), delimiter};
}

export function parseJSONTree(text) {
  // Keep exact number spellings and duplicate object keys. JSON.parse alone
  // rounds identifiers larger than 2^53 and silently removes duplicate keys.
  let offset = 0, nodes = 0;
  const token = /\s*("(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null|[\[\]{},:])/y;
  const take = () => {
    token.lastIndex = offset;
    const match = token.exec(text);
    if (!match) throw Error('Incomplete or invalid JSON.');
    offset = token.lastIndex; return match[1];
  };
  const read = (first, depth = 0) => {
    if (++nodes > 30000 || depth > 96) throw Error('Use Source to read this deeply nested file.');
    if (first === '{' || first === '[') {
      const object = first === '{', end = object ? '}' : ']', children = [];
      let next = take();
      if (next === end) return {kind:object ? 'object' : 'array', children};
      while (true) {
        let key = String(children.length);
        if (object) {
          if (!next.startsWith('"')) throw Error('Invalid JSON object key.');
          key = JSON.parse(next);
          if (take() !== ':') throw Error('Invalid JSON object.');
          next = take();
        }
        children.push({key, node:read(next, depth + 1)});
        next = take(); if (next === end) break;
        if (next !== ',') throw Error('Invalid JSON separator.');
        next = take();
      }
      return {kind:object ? 'object' : 'array', children};
    }
    if (first.startsWith('"')) return {kind:'string', value:JSON.parse(first)};
    if (/^-?\d/.test(first)) return {kind:'number', value:first};
    if (['true', 'false', 'null'].includes(first)) return {kind:first === 'null' ? 'null' : 'boolean', value:first};
    throw Error('Invalid JSON value.');
  };
  const value = read(take());
  if (text.slice(offset).trim()) throw Error('Unexpected content after JSON.');
  return value;
}

export function jsonPreview(text, extension, truncated = false) {
  if (!['jsonl', 'ndjson'].includes(extension)) return parseJSONTree(text.replace(/^\uFEFF/, ''));
  const lines = text.replace(/^\uFEFF/, '').split(/\r?\n/);
  if (truncated) lines.pop();
  const children = []; let more = false;
  for (let i = 0; i < lines.length; i++) {
    if (!lines[i].trim()) continue;
    try { children.push({key:String(i + 1), node:parseJSONTree(lines[i])}); }
    catch { throw Error('Invalid JSON on line ' + (i + 1) + '.'); }
    if (children.length >= 1000) { more = lines.slice(i + 1).some(line => line.trim()); break; }
  }
  return {kind:'array', children, records:true, more:more || truncated};
}

const textNode = (tag, value, className = '') => {
  const node = document.createElement(tag); node.textContent = value; node.className = className; return node;
};
const button = (label, action, className = '') => {
  const node = textNode('button', label, className); node.type = 'button'; node.onclick = action; return node;
};

function jsonView(root) {
  const tree = document.createElement('div'); tree.className = 'file-json';
  const make = (value, key = null, expanded = false) => {
    const branch = Array.isArray(value.children), row = document.createElement(branch ? 'details' : 'div');
    row.className = branch ? 'file-json-branch' : 'file-json-row';
    const heading = branch ? document.createElement('summary') : row;
    if (key !== null) heading.append(textNode('span', key + ':', 'file-json-key'));
    if (!branch) {
      heading.append(textNode('span', value.kind === 'string' ? JSON.stringify(value.value) : value.value, 'file-json-value ' + value.kind));
      return row;
    }
    const count = value.children.length, unit = value.records ? 'record' : value.kind === 'array' ? 'item' : 'field';
    heading.append(textNode('span', count + (value.more ? '+ loaded ' : ' ') + unit + (count === 1 ? '' : 's'), 'file-json-count'));
    row.append(heading);
    const children = document.createElement('div'); children.className = 'file-json-children'; row.append(children);
    let shown = 0;
    const more = button('Show more', () => fill(), 'file-json-more');
    const fill = () => {
      more.remove();
      for (const entry of value.children.slice(shown, shown + 50)) children.append(make(entry.node, entry.key));
      shown = Math.min(count, shown + 50);
      if (shown < count) { more.textContent = 'Show next ' + Math.min(50, count - shown); children.append(more); }
    };
    row.addEventListener('toggle', () => { if (row.open && !shown) fill(); });
    row.open = expanded; if (expanded) fill();
    return row;
  };
  tree.append(make(root, null, true)); return tree;
}

function tableView(data) {
  const wrap = document.createElement('div'); wrap.className = 'file-data';
  if (!data.rows.length) { wrap.append(textNode('p', 'No complete rows in this preview.', 'file-preview-note')); return wrap; }
  const numeric = value => /^-?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(value.trim());
  const hasHeader = !data.rows[0].some(numeric), header = hasHeader ? data.rows[0] : data.rows[0].map((_, i) => 'Column ' + (i + 1));
  const rows = hasHeader ? data.rows.slice(1) : data.rows;
  const totalColumns = Math.max(header.length, ...rows.map(row => row.length)), columns = Math.min(40, totalColumns);
  const filter = document.createElement('input'); filter.type = 'search'; filter.placeholder = 'Filter rows'; filter.setAttribute('aria-label', 'Filter preview rows');
  const tools = document.createElement('div'); tools.className = 'file-table-tools'; tools.append(filter); wrap.append(tools);
  const scroll = document.createElement('div'); scroll.className = 'file-table-scroll'; scroll.tabIndex = 0; scroll.setAttribute('role', 'region'); scroll.setAttribute('aria-label', 'Data table');
  const table = document.createElement('table'), head = document.createElement('thead'), body = document.createElement('tbody'), heading = document.createElement('tr');
  head.append(heading); table.append(head, body); scroll.append(table); wrap.append(scroll);
  const footer = document.createElement('div'); footer.className = 'file-table-pages';
  const count = textNode('span', ''); let page = 0, order = -1, direction = 1;
  const previous = button('←', () => { page--; draw(); }), next = button('→', () => { page++; draw(); });
  previous.setAttribute('aria-label', 'Previous rows'); next.setAttribute('aria-label', 'Next rows'); footer.append(count, previous, next); wrap.append(footer);
  const collator = new Intl.Collator(undefined, {numeric:true, sensitivity:'base'});
  const numericColumns = Array.from({length:columns}, (_, i) => rows.length && rows.every(row => !row[i] || numeric(row[i])));
  for (let i = 0; i < columns; i++) {
    const cell = document.createElement('th'); cell.scope = 'col'; cell.setAttribute('aria-sort', 'none');
    const sort = button(header[i] || 'Column ' + (i + 1), () => { direction = order === i ? -direction : 1; order = i; page = 0; draw(); });
    sort.title = 'Sort by ' + sort.textContent; cell.append(sort); heading.append(cell);
  }
  function draw() {
    const query = filter.value.toLocaleLowerCase();
    const selected = rows.filter(row => row.some(value => value.toLocaleLowerCase().includes(query)));
    if (order >= 0) selected.sort((a, b) => {
      const difference = numericColumns[order] && a[order] && b[order] ? Number(a[order]) - Number(b[order]) : 0;
      return direction * (Number.isFinite(difference) && difference !== 0 ? difference : collator.compare(a[order] || '', b[order] || ''));
    });
    const start = page * 50; body.replaceChildren();
    for (const values of selected.slice(start, start + 50)) {
      const row = document.createElement('tr');
      for (let i = 0; i < columns; i++) row.append(textNode('td', values[i] || '', numericColumns[i] ? 'numeric' : ''));
      body.append(row);
    }
    [...heading.children].forEach((cell, i) => cell.setAttribute('aria-sort', i === order ? direction === 1 ? 'ascending' : 'descending' : 'none'));
    count.textContent = (selected.length ? (start + 1) + '–' + Math.min(start + 50, selected.length) + ' / ' : '') + selected.length + (data.more ? '+ loaded rows' : ' rows');
    if (totalColumns > 40) count.textContent += ' · first 40 columns';
    previous.disabled = page === 0; next.disabled = start + 50 >= selected.length;
    previous.hidden = next.hidden = selected.length <= 50;
    scroll.scrollTop = 0;
  }
  filter.oninput = () => { page = 0; draw(); }; draw(); return wrap;
}

export function renderFilePreview({text, extension, truncated, highlight, markdown}) {
  const root = document.createElement('div'); root.className = 'file-preview-content';
  if (!text) { root.append(textNode('p', 'Empty file.', 'file-preview-note')); return root; }
  const toolbar = document.createElement('div'); toolbar.className = 'file-preview-toolbar';
  const content = document.createElement('div'); content.className = 'file-preview-value';
  let formatted, label, failure = '';
  try {
    if (['csv', 'tsv'].includes(extension)) { const data = delimitedPreview(text, extension, truncated); formatted = () => tableView(data); label = 'Table'; }
    else if (['json', 'jsonl', 'ndjson', 'ipynb'].includes(extension)) { const value = jsonPreview(text, extension, truncated); formatted = () => jsonView(value); label = 'Tree'; }
    else if (extension === 'md' && markdown) {
      formatted = () => { const prose = document.createElement('div'); prose.className = 'file-markdown reader-prose'; prose.innerHTML = markdown(text); return prose; }; label = 'Preview';
    }
  } catch (error) { failure = truncated ? 'Partial file · showing source' : error.message + ' Showing source.'; }
  const source = () => {
    const pre = document.createElement('pre'), code = document.createElement('code'); pre.tabIndex = 0; pre.className = 'file-source';
    code.className = 'hljs'; code.innerHTML = highlight(text, ['jsonl', 'ndjson', 'ipynb'].includes(extension) ? 'json' : extension); pre.append(code); return pre;
  };
  const wrap = button('Wrap', () => { const on = root.classList.toggle('file-wrap'); wrap.setAttribute('aria-pressed', String(on)); });
  wrap.setAttribute('aria-pressed', 'false');
  if (formatted) {
    const pretty = button(label, () => choose(true)), raw = button('Source', () => choose(false));
    const choose = value => { pretty.setAttribute('aria-pressed', String(value)); raw.setAttribute('aria-pressed', String(!value)); wrap.hidden = value; content.replaceChildren(value ? formatted() : source()); };
    toolbar.append(pretty, raw, wrap); choose(true);
  } else { toolbar.append(textNode('span', failure || extension.toUpperCase() || 'Text', 'file-preview-format'), wrap); content.append(source()); }
  root.append(toolbar, content); return root;
}
