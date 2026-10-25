/* A reader for the visible terminal capture, not a reconstructed chat history. */
"use strict";
(function (root) {
  const content=typeof module==='object'&&module.exports?require('./message-content.js'):root.HubMessageContent;
  const escape = (text) => String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const inline = (text) => escape(text).replace(/`([^`\n]+)`/g, "<code>$1</code>").replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  const border = (line) => /^\s*[┌┬┐├┼┤└┴┘┏┳┓┣╋┫┗┻┛╭╮╰╯─━┄┅┈┉|+\-\s]+$/.test(line) && /[─━┄┅┈┉-]{3}/.test(line);
  const cells = (line) => line.trim().replace(/^\|\s*|\s*\|$/g, "").split(/\s{2,}|\s*\|\s*/).map((cell) => cell.trim());
  const tool = (text) => /^(?:Ran |Failed \(exit |Explored\b|Edited |Called |Searched |Read \d+ (?:files?|lines?)\b|Wrote |Updated (?:plan|file)|Bash\(|Read\(|Write\(|Edit\(|Grep\(|Glob\(|Web(?:Search|Fetch)\(|Task\(|Tool\b)/.test(text);
  const queueHint = (text) => /^(?:↳\s*)?shift\s*\+\s*(?:\S+\s+)?(?:to\s+)?edit last queued message(?:\s*[·•].*)?$/i.test(text);
  const activityRow = (text) => /^[•●✻✽✶✢✳·*]\s+[^()\n]+\((?:\d+[hms]\s*)+[•·][^)\n]*(?:esc to interrupt|tokens\b)[^)\n]*\)(?:\s*[·•].*)?[ \t]*$/i.test(text) ||
    /^[•●✻✽✶✢✳·*]\s+(?:Auto[- ]?)?Compacting(?:\s+(?:context|conversation))?\s*(?:…|\.{3})?(?:\s*\((?:\d+[hms]\s*)+(?:[•·][^)\n]*)?\))?(?:\s*·\s*\d+ background terminals? running(?:\s*·[^\n]*)?)?\s*$/i.test(text);
  const completionRow = text => /^(?:Worked for |[✻✽✶✢✳]\s+[^\n]+?\s+for )\d+(?:\.\d+)?(?:ms|[hms])\b[^\n]*$/i.test(text);
  const completedAt = text => /^done\s+(?:(?:[A-Z][a-z]{2,8}\s+\d{1,2}(?:,\s*\d{4})?|today|yesterday)\s+at\s+)?\d{1,2}:[0-5]\d(?::[0-5]\d)?(?:\s*[AP]M)?$/i.test(text);
  const editorBorder = text => /^[─━]{8,}\s*$/.test(text);
  const chrome = (text) => !text || queueHint(text) || /^(?:[›❯]\s*(?:Ask Codex to do anything)?\s*$|(?:GPT-|gpt-).+ · .+(?:left|context)|← for agents|new task\? \/clear|-- INSERT --|\+ Show details$|.* · \d+h .+\/rc\s*$)/.test(text);
  // Capture-only counterpart of bridge.terminal_content. Original messages and
  // the raw terminal are never rewritten. Shared fixtures cover both paths.
  function terminalRows(text) {
    const lines=String(text).split('\n'),rows=[];
    let editor=null,codexFooter=null;
    // The final Codex model/status row starts its footer. Keep all shortcuts
    // in that region out of messages, without matching phrases in user text.
    for(let index=lines.length-1;index>=0;index--){
      if(!/^›(?:\s|$)/.test(lines[index]))continue;
      if(!lines.slice(index+1).some(line=>/^[•●›❯■](?:\s|$)/.test(line))){
        const offset=lines.slice(index+1).findIndex(line=>/^(?:GPT-|gpt-).+ · .+(?:left|context)/.test(line.trim()));
        if(offset>=0)codexFooter=index+1+offset;
      }
      break;
    }
    for(let index=lines.length-1;index>0;index--){
      if(!/^❯(?:\s|$)/.test(lines[index]) || !editorBorder(lines[index-1]))continue;
      let end=-1;
      for(let i=index+1;i<Math.min(lines.length,index+34);i++)if(editorBorder(lines[i])){end=i;break;}
      if(end>=0 && !lines.slice(end+1).some(line=>/^[•●›❯■](?:\s|$)/.test(line))){editor=index;break;}
    }
    let fence = null, fenceIndent = 0, recapIndent = null, role = '';
    for (const [index,line] of lines.entries()) {
      const trimmed = line.trim(), indent = line.length - line.trimStart().length;
      const delimiter = trimmed.match(/^(`{3,}|~{3,})/)?.[1];
      if (fence && trimmed && indent < fenceIndent) fence = null;
      if(editor!==null && index>=editor-1){
        // A quoted editor must not hide prose after its closing fence.
        if(fence)editor=null;
        else{if(index===editor)rows.push(['editor',line]);continue;}
      }
      if(codexFooter!==null && index>=codexFooter){
        if(fence)codexFooter=null;
        else{rows.push(['chrome',line]);continue;}
      }
      if (fence || delimiter) {
        rows.push(['content',line]);
        if (!fence) { fence = delimiter; fenceIndent = indent; }
        else if (delimiter?.[0] === fence[0] && delimiter.length >= fence.length) fence = null;
        recapIndent = null;
        continue;
      }
      if (recapIndent !== null) {
        if(!trimmed){rows.push(['content',line]);continue;}
        if (indent >= recapIndent) continue;
        recapIndent = null;
      }
      if ((!indent && /^⚠\uFE0F?\s+Heads up, you have (?:less than )?<?\d+(?:\.\d+)?% of your (?:weekly|daily|session|5h|5-hour) limit left\.(?: Run \/status for a breakdown\.)?$/i.test(trimmed)) ||
          /^⚠\uFE0F?\s+(?:weekly|daily|session|5h|5-hour) limit: only <?\d+(?:\.\d+)?% left\s*·\s*resets [^\n]+\s*·\s*\/status$/i.test(trimmed)) continue;
      if(/^[─━]+\s*Conversation recap\s*[─━]+\s*$/i.test(line)){recapIndent=2;continue;}
      if (/^(?:※\s+recap:\s*| {0,2}↳\s+Recap:\s*)/i.test(line) && (line.startsWith('※') || !['input', 'queued', 'tool'].includes(role))) {
        recapIndent = line.startsWith('※') ? 2 : indent + 2;
        continue;
      }
      if (trimmed === '(disable recaps in /config)') continue;
      const protectedRow=indent>0 && ['input','queued','tool'].includes(role);
      if(!protectedRow && activityRow(line)){rows.push(['activity',line]);continue;}
      if(!protectedRow && (completionRow(trimmed) || (indent<=2 && completedAt(trimmed)))){rows.push(['completion',line]);continue;}
      const marker = line.match(/^[•●]\s+(.+)/);
      if (marker) role = marker[1].startsWith('Queued follow-up inputs') ? 'queued' : tool(marker[1]) ? 'tool' : 'assistant';
      else if (/^[›❯](?:\s|$)/.test(line)) role = 'input';
      rows.push(['content',line]);
    }
    return rows;
  }
  function terminalContent(text) {
    return terminalRows(text).filter(([kind])=>kind==='content').map(([,line])=>line).join('\n').replace(/\n+$/,'');
  }
  function partialPatch(lines, index) {
    // A redraw can omit the leading "Edited" row. Require the terminal's file
    // change record and numbered diff rows together; ordinary code is not a
    // role boundary. Uncertain output stays available as Terminal details.
    const file = line => /^\s*[└├]\s+.+\(\+\d+\s+[−-]\d+\)\s*$/.test(line);
    const change = line => /^\s+\d+\s+[+−-]/.test(line);
    if (!file(lines[index]) && !change(lines[index])) return false;
    let hasFile = false, hasChange = false;
    for (let i = index; i < Math.min(lines.length, index + 40); i++) {
      if (/^[•●›❯■](?:\s|$)/.test(lines[i]) || /^\s*(?:`{3,}|~{3,})/.test(lines[i])) break;
      if (!lines[i].trim()) continue;
      if (file(lines[i])) hasFile = true;
      else if (change(lines[i]) || /^\s+\d+\s/.test(lines[i])) hasChange = true;
      else break;
      if (hasFile && hasChange) return true;
    }
    return false;
  }
  function parse(text) {
    const clean = String(text).replace(/\x1b\](?:[^\x07\x1b]|\x1b(?!\\))*(?:\x07|\x1b\\)/g, "").replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, "").replace(/\r\n?/g, "\n").replace(/[\x00-\x08\x0b-\x1f\x7f]/g, "");
    const rows=terminalRows(clean),lines=rows.map(([,line])=>line.trimEnd());
    const blocks = [];
    // Some Codex versions omit the queue heading. Only an actual queue footer
    // establishes these arrows as queued input; tool and recap arrows do not.
    const queueStarts = new Set();
    lines.forEach((line, end) => {
      if (!queueHint(line.trim())) return;
      let start = null;
      for (let i = end - 1; i >= 0; i--) {
        if (/^[•●›❯※]|^\s*↳\s*(?:Recap:|Interacted with background terminal)/.test(lines[i])) break;
        if (/^\s*↳(?:\s|$)/.test(lines[i]) && !queueHint(lines[i].trim())) start = i;
      }
      if (start !== null) queueStarts.add(start);
    });
    let current = null, fence = null, fenceIndent = 0;
    const start = (type, first) => { current = { type, lines: [first] }; blocks.push(current); fence = first.match(/^(`{3,}|~{3,})/)?.[1] || null; fenceIndent = type === 'output' ? 0 : 2; };
    for (const [index, line] of lines.entries()) {
      if(rows[index][0]==='chrome')continue;
      const trimmed = line.trim();
      const delimiter = trimmed.match(/^(`{3,}|~{3,})/)?.[1];
      // A new terminal row can follow an unfinished, indented code block.
      const indent = line.length - line.trimStart().length;
      if (fence && trimmed && indent < fenceIndent) fence = null;
      if (fence || delimiter) {
        if (!current) start('output', '');
        current.lines.push(line.replace(/^ {2}/, ''));
        if (!fence) { fence = delimiter; fenceIndent = indent; }
        else if (delimiter?.[0] === fence[0] && delimiter.length >= fence.length) fence = null;
        continue;
      }
      // CLI activity belongs in the live indicator, never in the transcript.
      if (rows[index][0]==='activity' || rows[index][0]==='editor') continue;
      if (trimmed && chrome(trimmed)) { if (current?.type === 'queued') current = null; continue; }
      if (queueStarts.has(index) && current?.type !== 'queued') start('queued', 'Queued follow-up inputs');
      if (rows[index][0]==='completion') {
        if (current?.type==='assistant')current.duration=[trimmed.replace(/^[✻✽✶✢✳]\s+/, ''),current.duration].filter(Boolean).join(' · ');
        continue;
      }
      const marker = line.match(/^[•●]\s(.+)/);
      const prompt = line.match(/^[›❯]\s(.+)/);
      if (marker) start(tool(marker[1]) ? "tool" : /^Queued follow-up inputs:?$/.test(marker[1]) ? "queued" : "assistant", marker[1]);
      else if (prompt) start("input", prompt[1]);
      else if (/^\s{0,2}(?:Read \d+ files?(?:, ran \d+ shell commands?)?|Ran \d+ shell commands?)\s*$/.test(line)) start("tool", trimmed);
      else {
        if (current?.type === 'input' && partialPatch(lines, index)) start('output', '');
        if (!current) start("output", "");
        current.lines.push(line.replace(/^ {2}/, ""));
      }
    }
    return blocks.map((block) => ({ ...block, text: block.lines.join("\n").trim() })).filter((block) => block.text);
  }
  function table(rows) {
    if (rows.length < 2 || rows[0].length < 2 || rows.some((row) => row.length !== rows[0].length)) return null;
    return `<div class="reader-table" tabindex="0" role="region" aria-label="Agent output table"><table><thead><tr>${rows[0].map((cell) => `<th scope="col">${inline(cell)}</th>`).join("")}</tr></thead><tbody>${rows.slice(1).map((row) => `<tr>${row.map((cell) => `<td>${inline(cell)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  }
  function prose(text) {
    // Native Markdown keeps nested lists, code whitespace, math and diagrams.
    // Retain the terminal-table adapter for captures that have lost Markdown.
    if (root.HubMarkdown && !/^[ \t]*[┌┏╭│┃]/m.test(text)) return root.HubMarkdown.render(text);
    const lines = text.split("\n"), output = [];
    for (let i = 0; i < lines.length;) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      const fence = line.match(/^\s*(`{3,}|~{3,})(.*)$/);
      if (fence) {
        const code = [];
        const close = new RegExp('^\\s*'+fence[1][0]+'{'+fence[1].length+',}\\s*$');
        for (i++; i < lines.length && !close.test(lines[i]); i++) code.push(lines[i]);
        i++; output.push(root.HubMarkdown?.codeBlock ? root.HubMarkdown.codeBlock(code.join('\n'),fence[2]) :
          `<pre class="reader-code" tabindex="0"><code>${escape(code.join("\n"))}</code></pre>`); continue;
      }
      if (/^\s*[┌┏╭│┃]/.test(line)) {
        const original = [], rows = []; let row = null;
        while (i < lines.length && (/^\s*[│┃]/.test(lines[i]) || border(lines[i]))) {
          const entry = lines[i++]; original.push(entry);
          if (border(entry)) { if (row) rows.push(row); row = null; continue; }
          const values = entry.trim().replace(/^[│┃]|[│┃]$/g, "").split(/[│┃]/).map((cell) => cell.trim());
          if (row && row.length === values.length) row = row.map((cell, col) => [cell, values[col]].filter(Boolean).join(" "));
          else { if (row) rows.push(row); row = values; }
        }
        if (row) rows.push(row);
        output.push(table(rows) || `<pre class="reader-code" tabindex="0">${escape(original.join("\n"))}</pre>`); continue;
      }
      if (cells(line).length > 1 && i + 1 < lines.length && (border(lines[i + 1]) || cells(lines[i + 1]).length === cells(line).length)) {
        const rows = [], original = [], count = cells(line).length;
        while (i < lines.length && (border(lines[i]) || (lines[i].trim() && cells(lines[i]).length === count))) {
          original.push(lines[i]); if (!border(lines[i])) rows.push(cells(lines[i])); i++;
        }
        output.push(table(rows) || `<pre class="reader-code" tabindex="0">${escape(original.join("\n"))}</pre>`); continue;
      }
      if (/^\s{4}|^\s*\d+\s+[+−-]/.test(line)) {
        const code = [];
        while (i < lines.length && (/^\s{4}|^\s*\d+\s+[+−-]/.test(lines[i]) || !lines[i].trim())) code.push(lines[i++]);
        const source=code.join('\n').trimEnd();
        output.push(root.HubMarkdown?.codeBlock ? root.HubMarkdown.codeBlock(source) :
          `<pre class="reader-code" tabindex="0"><code>${escape(source)}</code></pre>`); continue;
      }
      if (border(line)) { i++; continue; }
      const list = line.match(/^\s*(?:[-*•]|\d+[.)])\s+(.+)/);
      const paragraph = [list ? list[1] : line.trim()]; i++;
      while (i < lines.length && lines[i].trim() && !/^\s*(?:```|~~~|[┌┏╭│┃]|[-*•] |\d+[.)] )|^\s{4}/.test(lines[i]) && !border(lines[i]) && cells(lines[i]).length < 2) paragraph.push(lines[i++].trim());
      output.push(list ? `<p class="reader-list-item">${inline(line.trim().slice(0, line.trim().indexOf(list[1])))}<span>${inline(paragraph.join(" "))}</span></p>` : `<p>${inline(paragraph.join(" "))}</p>`);
    }
    return output.join("");
  }
  function queueItems(text) {
    const messages = [];
    let current = null, indent = null;
    for (const line of text.split("\n")) {
      if (line.trim() && chrome(line.trim())) continue;
      const marker = line.match(/^([ \t]*)↳(?:[ \t]+(.*)|$)/);
      if (marker && (indent === null || marker[1].length === indent)) {
        indent = marker[1].length;
        current = [marker[2] || ""]; messages.push(current);
      } else {
        if (!current) continue;
        // Remove the terminal's arrow gutter, retaining message indentation.
        const gutter = indent === null ? "" : " ".repeat(indent + 2);
        current.push(gutter && line.startsWith(gutter) ? line.slice(gutter.length) : line);
      }
    }
    return messages.map(lines => lines.join("\n").trim()).filter(Boolean);
  }
  function queuedProse(text) {
    const items = queueItems(text);
    return `<ul class="reader-queue" role="list" aria-label="Queued messages">${items.map(text => `<li><span class="queue-arrow" aria-hidden="true"><svg class="icon"><use href="#i-queued"/></svg></span><div class="queue-message">${prose(text)}</div></li>`).join("")}</ul>`;
  }
  function queued(text) {
    const blocks = parse(text), index = blocks.findLastIndex(block => block.type === 'queued');
    if (index < 0 || blocks.slice(index + 1).some(block => ['assistant', 'input', 'tool'].includes(block.type))) return [];
    return queueItems(blocks[index].text);
  }
  function renderQueued(text) {
    const messages = queued(text);
    return messages.length ? `<section class="reader-entry reader-user" data-message-id="terminal-queue" data-message-source="terminal" data-message-role="user" aria-label="You">${heading('You', 'Queued')}<div class="reader-prose"><ul class="reader-queue" role="list" aria-label="Queued messages">${messages.map(text => `<li><span class="queue-arrow" aria-hidden="true"><svg class="icon"><use href="#i-queued"/></svg></span><div class="queue-message">${prose(text)}</div></li>`).join('')}</ul></div></section>` : '';
  }
  function heading(label, metadata = "", action = "") {
    return `<h3><span class="reader-speaker">${escape(label)}</span>${metadata ? ` <span class="reader-meta">${metadata}</span>` : ""}${action ? ` ${action}` : ""}</h3>`;
  }
  function terminalMetadata(text = "") {
    const parts = [];
    const clock = text.match(/\b(\d{1,2}):([0-5]\d)(?::([0-5]\d))?\s*([ap]m)?\b/i);
    if (clock) {
      let hour = Number(clock[1]);
      const period = clock[4]?.toLowerCase();
      if (period ? hour >= 1 && hour <= 12 : hour <= 23) {
        if (period) hour = hour % 12 + (period === "pm" ? 12 : 0);
        const time = `${String(hour).padStart(2, "0")}:${clock[2]}${clock[3] ? `:${clock[3]}` : ""}`;
        const date=text.match(/\bdone\s+((?:[A-Z][a-z]{2,8}\s+\d{1,2}(?:,\s*\d{4})?|today|yesterday))\s+at\s+/i)?.[1];
        parts.push(`<time datetime="${time}" title="Time shown by the terminal">${date?escape(date)+' · ':''}${time}</time>`);
      }
    }
    const elapsed = text.match(/\b(\d+(?:\.\d+)?(?:ms|[hms])(?:\s+\d+(?:\.\d+)?(?:ms|[hms]))*)\b/);
    if (elapsed) {
      const duration = escape(elapsed[1].replace(/\s+/g, " "));
      parts.push(`<span class="reader-duration" title="Elapsed time" aria-label="Elapsed time: ${duration}">${duration}</span>`);
    }
    return parts.join(' <span class="reader-meta-divider" aria-hidden="true">·</span> ');
  }
  function fold(kind, key, summary, body, open = false) {
    return `<details class="${kind}" data-reader-key="${escape(key)}"${open ? " open" : ""}><summary>${summary}</summary><div class="reader-details-body">${body}</div></details>`;
  }
  function terminal(text, kind = "capture") {
    return root.HubMarkdown?.highlightTerminal ? root.HubMarkdown.highlightTerminal(text, kind) : escape(text);
  }
  function questionReplies(text) {
    const open = '<send_user_message_question_reply>', close = '</send_user_message_question_reply>';
    // Recognize a complete protocol message, never tags embedded in prose or
    // quoted/fenced/indented examples. Keep the original transcript for forks.
    const value = String(text).replace(/^(?:[ \t]*\r?\n)+/, '').trimEnd();
    if (!value.startsWith(open) || !value.endsWith(close)) return null;
    try {
      const replies = JSON.parse(value.slice(open.length, -close.length));
      return Array.isArray(replies) && replies.length && replies.every(reply =>
        reply && !Array.isArray(reply) && typeof reply.answer === 'string' &&
        typeof reply.question === 'string' && typeof reply.questionItemId === 'string'
      ) ? replies : null;
    } catch { return null; }
  }
  function userHTML(text, render = prose) {
    text=content.userText(text);
    const replies = questionReplies(text);
    return replies ? replies.map(reply => `<div class="reader-question-reply"><div class="reader-reply-question">${escape(reply.question)}</div><div class="reader-reply-answer">${render(reply.answer)}</div></div>`).join('') : render(text);
  }
  function userText(text) {
    text=content.userText(text);
    const replies = questionReplies(text);
    return replies ? replies.map(reply => `${reply.question}\n\n${reply.answer}`).join('\n\n') : text;
  }
  function blockHTML(block, index, group = "history") {
    if (block.type === 'hidden-queue') return '';
    if (block.type === "tool") return fold(`reader-tool${/^Failed /.test(block.text) ? " tool-failed" : ""}`, `${group}-tool-${index}`, `<span class="reader-tool-label terminal-syntax">${terminal(block.lines[0])}</span>`, `<pre class="reader-code" tabindex="0"><code class="terminal-syntax">${terminal(block.text)}</code></pre>`, /^Failed /.test(block.text));
    if (block.type === "input" || block.type === "queued") {
      const queued = block.type === "queued";
      const text = queued ? block.text.replace(/^Queued follow-up inputs:?\n?/, "") : content.userText(block.text);
      if(!text)return '';
      if (queued && !queueItems(text).length) return '';
      return `<section class="reader-entry reader-user" data-message-source="terminal" data-message-role="user" aria-label="You" data-terminal-index="${index}">${heading("You", queued ? "Queued" : "")}<div class="reader-prose">${queued ? queuedProse(text) : userHTML(text)}</div></section>`;
    }
    if (block.type !== "assistant") return fold("reader-fold reader-terminal", `${group}-terminal-${index}`, 'Terminal details', `<pre class="reader-code" tabindex="0"><code class="terminal-syntax">${terminal(block.text)}</code></pre>`, group === "latest");
    return `<section class="reader-entry reader-assistant" data-message-source="terminal" data-terminal-index="${index}">${heading("Agent", terminalMetadata(block.duration))}<div class="reader-prose">${replyHTML(prose(block.text),block.text,group+':'+index)}</div></section>`;
  }
  function outline(html, text) {
    if(text.length<1500)return html;
    const headings=[];
    const body=html.replace(/<h([1-4])>([\s\S]*?)<\/h\1>/g,(whole,level,content)=>{
      const index=headings.length;headings.push(content.replace(/<[^>]*>/g,'').replace(/&[^;]+;/g,' ').replace(/\s+/g,' ').trim().slice(0,90));
      return `<h${level} data-reply-section="${index}">${content}</h${level}>`;
    });
    if(headings.length<2)return html;
    return `<details class="reply-outline"><summary>In this reply <span>${headings.length}</span></summary><nav aria-label="Reply sections">${headings.map((heading,index)=>`<button type="button" data-reply-jump="${index}">${escape(heading)}</button>`).join('')}</nav></details>`+body;
  }
  const visibleMessage = content.visible;
  const isCommentary = message => message.phase==='commentary'||message.channel==='commentary';
  // Keep the actual opening, not a generated summary. Split only at a text
  // boundary outside links, code and equations; all remaining nodes survive.
  function splitParagraph(paragraph) {
    const text=paragraph.textContent;
    if(text.length<720||paragraph.querySelector('img,video,audio,math,.katex'))return null;
    const sentences=[...text.slice(0,640).matchAll(/[.!?]["'”’)]*\s+/g)].map(match=>match.index+match[0].length).filter(index=>index>=200);
    const words=[...text.slice(0,540).matchAll(/\s+/g)].map(match=>match.index+match[0].length).filter(index=>index>=160);
    for(const end of [...sentences.reverse(),...words.reverse()]){
      const walker=paragraph.ownerDocument.createTreeWalker(paragraph,4);let node,offset=0;
      while((node=walker.nextNode())){
        if(offset+node.length>=end){
          if(node.parentElement.closest('a,code,math,.katex,svg'))break;
          const first=paragraph.cloneNode(false),rest=paragraph.cloneNode(false),range=paragraph.ownerDocument.createRange();
          rest.removeAttribute('id');range.selectNodeContents(paragraph);range.setEnd(node,end-offset);first.append(range.cloneContents());
          range.selectNodeContents(paragraph);range.setStart(node,end-offset);rest.append(range.cloneContents());
          return [first,rest];
        }
        offset+=node.length;
      }
    }
    return null;
  }
  const shortReply = text => String(text).trim().split(/\s+/u).length<=300;
  function replyHTML(html,text,key='reply') {
    if(shortReply(text))return html;
    html=outline(html,text);
    if(text.length<1200||!root.document)return html;
    const template=root.document.createElement('template');template.innerHTML=html;
    const nav=template.content.querySelector('.reply-outline');nav?.remove();
    const lead=[];let size=0;
    for(const node of [...template.content.children]){
      if(/^H[1-6]$/.test(node.tagName)&&!lead.length){lead.push(node);node.remove();continue;}
      if(node.tagName==='P'&&!node.querySelector('img,video,audio')){
        if(size&&size+node.textContent.length>640)break;
        const split=splitParagraph(node);
        if(split){lead.push(split[0]);node.replaceWith(split[1]);size+=split[0].textContent.length;break;}
        lead.push(node);size+=node.textContent.length;node.remove();
        if(size>=160)break;
      }else if(!size&&['UL','OL'].includes(node.tagName)){
        const first=node.cloneNode(false);let count=0;
        for(const item of [...node.children]){
          if(count&&size+item.textContent.length>640)break;
          size+=item.textContent.length;count++;first.append(item);
          if(size>=160)break;
        }
        if(node.tagName==='OL'&&node.children.length)node.setAttribute('start',String(Number(first.getAttribute('start')||1)+count));
        if(!node.children.length)node.remove();lead.push(first);break;
      }else break;
    }
    if(!size||template.content.textContent.trim().length<600)return html;
    const minutes=Math.max(1,Math.ceil(text.trim().split(/\s+/).length/220));
    return lead.map(node=>node.outerHTML).join('\n')+`\n<details class="reply-expansion" data-reader-key="${escape(key)}:full" open><summary><span class="reply-more">Read full reply</span><span class="reply-less">Collapse reply</span><span class="reply-length">${minutes} min read</span></summary><div class="reply-full">${nav?.outerHTML||''}${template.innerHTML}</div><button type="button" class="reply-collapse" data-reply-collapse>Collapse reply ↑</button></details>`;
  }
  const compactScreen=root.matchMedia?.('(max-width:800px)');
  function prepareReplies(container) {
    bindReplies(container);
    for(const node of container.querySelectorAll('.reply-expansion')){
      if(node._replyReady)continue;
      node._replyReady=true;node._mobileOpen=false;node.open=!compactScreen?.matches;
      node.addEventListener('toggle',()=>{
        if(compactScreen?.matches)node._mobileOpen=node.open;
        if(!node.open)for(const media of node.querySelectorAll('video,audio'))media.pause();
      });
    }
  }
  compactScreen?.addEventListener('change',event=>{
    for(const node of root.document.querySelectorAll('.reply-expansion')){
      if(event.matches)node.open=Boolean(node._mobileOpen)||protectedContent(node);
      else{node._mobileOpen=node.open;node.open=true;}
    }
  });
  function bindReplies(container) {
    if(container._repliesBound)return;container._repliesBound=true;
    container.addEventListener('click',event=>{
      const button=event.target.closest('[data-reply-jump],[data-reply-collapse]');if(!button)return;
      const entry=button.closest('.reader-entry,.chat-message');if(!entry)return;
      root.HubFocus?.stopRestoring?.();
      if(button.hasAttribute('data-reply-collapse')){
        const details=button.closest('.reply-expansion');details.open=false;
        entry.scrollIntoView({block:'start',behavior:'instant'});details.querySelector('summary').focus({preventScroll:true});
      }else{
        const node=entry.querySelector(`[data-reply-section="${button.dataset.replyJump}"]`);
        if(node){button.closest('.reply-outline').open=false;node.scrollIntoView({block:'start',behavior:'instant'});node.tabIndex=-1;node.focus({preventScroll:true});}
      }
    });
  }
  function messageHTML(message, index=0, messages=[message]) {
    if(!visibleMessage(message))return '';
    const role = message.role === "user" ? "user" : message.role === "assistant" ? "assistant" : message.role === "tool" ? "tool" : "unknown";
    const continuation = index > 0 && role !== "unknown" && messages[index - 1].role === message.role && (role !== "tool" || messages[index - 1].kind === message.kind);
    const label = role === "tool" ? ({command:"Command", "command-output":"Command output", "task-update":"Task update"}[message.kind] || "Tool") : {user: "You", assistant: "Agent", unknown: "Message"}[role];
    const date = new Date(message.created);
    const newDay = !index || date.toDateString() !== new Date(messages[index - 1].created).toDateString();
    const time = message.created && !Number.isNaN(date.getTime()) ? `<time datetime="${escape(date.toISOString())}" title="${escape(date.toLocaleString([], {hourCycle:"h23"}))}">${escape(date.toLocaleString([], {...(newDay ? {month:"short",day:"numeric"} : {}),hour:"2-digit",minute:"2-digit",hourCycle:"h23"}))}</time>` : "";
    const edit = role === "user" && message.fork ? `<button type="button" class="icon-button edit-message" data-edit-message="${escape(message.id)}" aria-label="Edit message" title="Edit into a new branch"><svg class="icon" aria-hidden="true"><use href="#i-edit"/></svg></button>` : "";
    const body=role==='tool'?`<pre class="reader-command" tabindex="0"><code class="terminal-syntax">${terminal(message.text,message.kind==='command'?'command':'output')}</code></pre>`:role==='user'?(root.HubImages?.html(message.attachments)||'')+userHTML(content.displayText(message),root.HubMarkdown.render):root.HubMarkdown.render(message.text);
    return `<section class="reader-entry reader-${role}${continuation ? " reader-continuation" : ""}" data-message-id="${escape(role + ':' + (message.id ?? index))}" data-message-role="${role}"${message.asset_agent_id?` data-agent-id="${escape(message.asset_agent_id)}"`:''}${message.phase?` data-message-phase="${escape(message.phase)}"`:''} aria-label="${label}">${heading(label, time, edit)}<div class="reader-prose">${role==='assistant'&&!isCommentary(message)?replyHTML(body,message.text,role+':'+(message.id??index)):body}</div></section>`;
  }
  function historyHTML(blocks) {
    const output = [];
    for (let index = 0; index < blocks.length;) {
      if (blocks[index].type !== "tool") { output.push(blockHTML(blocks[index], index++)); continue; }
      const start = index;
      while (index < blocks.length && blocks[index].type === "tool") index++;
      const tools = blocks.slice(start, index), failed = tools.some((block) => /^Failed /.test(block.text));
      output.push(fold(`reader-tools${failed ? " tool-failed" : ""}`, `history-tools-${start}${failed ? "-failed" : ""}`, `${tools.length} tool ${tools.length === 1 ? "step" : "steps"}${failed ? " · check failed step" : ""}`, tools.map((block, offset) => blockHTML(block, start + offset)).join(""), failed));
    }
    return output.join("");
  }
  function renderMessages(messages, start = 0) {
    messages=content.messages(messages);
    return messages.slice(start).map((message,index) => messageHTML(message,index+start,messages)).join('');
  }
  function render(text, messages, history = false, hideQueue = false) {
    if(messages?.length){messages=content.messages(messages);if(!messages.length)return '';}
    if (history && messages?.length && root.HubMarkdown) return '<div class="reader-history">' + renderMessages(messages) + "</div>" + (hideQueue?'':renderQueued(text));
    if (messages?.length && root.HubMarkdown) {
      return messageHTML(messages.at(-1)) +
        (messages.length > 1 ? fold("reader-fold", "earlier", `Earlier messages <span>${messages.length - 1}</span>`, `<div class="reader-history">${messages.slice(0, -1).map((message,index)=>messageHTML(message,index,messages)).join("")}</div>`) : "") + (hideQueue?'':renderQueued(text));
    }
    const blocks = parse(text).map(block=>hideQueue && block.type==='queued'?{...block,type:'hidden-queue'}:block);
    if (blocks.length === 1 && blocks[0].type === "output") return blockHTML(blocks[0], 0, "latest");
    if (history && blocks.length) return historyHTML(blocks);
    if (!blocks.length) return '<div class="reader-empty">No agent output yet.</div>';
    let latest = blocks.findLastIndex((block) => block.type === "assistant");
    if (latest < 0) latest = blocks.findLastIndex((block) => block.type === "output");
    const earlier = latest < 0 ? [] : blocks.slice(0, latest);
    const after = blocks.slice(latest + 1);
    const tools = after.filter((block) => block.type === "tool");
    const remainder = after.filter((block) => block.type !== "tool");
    const latestHTML = latest >= 0 ? `<article class="reader-latest">${blockHTML(blocks[latest], latest, "latest")}</article>` : '<div class="reader-empty">No response in this capture yet.</div>';
    return latestHTML + (tools.length ? fold("reader-fold", "activity", `Tool activity <span>${tools.length} ${tools.length === 1 ? "step" : "steps"}${latest >= 0 ? " since this response" : ""}</span>`, `<div class="reader-history">${tools.map((block, index) => blockHTML(block, index, "activity")).join("")}</div>`, tools.some((block) => /^Failed /.test(block.text))) : "") + remainder.map((block, index) => blockHTML(block, index, "following")).join("") + (earlier.length ? fold("reader-fold", "earlier", `Earlier output <span>${earlier.length} ${earlier.length === 1 ? "section" : "sections"} in this capture</span>`, `<div class="reader-history">${earlier.map((block, index) => blockHTML(block, index)).join("")}</div>`) : "");
  }
  // Retain unchanged DOM, including enhanced media, open tools and selections.
  // Compare source markup, not the DOM modified by Markdown enhancement.
  const sources = new WeakMap(), renderCache = new WeakMap(), histories=new WeakMap(), navigation=new WeakMap();
  const stopPaging=container=>navigation.get(container)?.cancel();
  const nodeKey = node => node.nodeType === 1 ? node.getAttribute('data-message-id') || node.getAttribute('data-reader-key') || '' : '';
  const source = node => node.nodeType === 1 ? node.outerHTML : node.nodeValue;
  function reconcile(parent, fresh) {
    const old = [...parent.childNodes], used = new Set();
    let cursor = parent.firstChild;
    for (const next of [...fresh.childNodes]) {
      const html = source(next), key = nodeKey(next);
      const sameKind = node => node.nodeType === next.nodeType && node.nodeName === next.nodeName;
      let node = old.find(node => !used.has(node) && sameKind(node) && (key ? nodeKey(node) === key : !nodeKey(node) && sources.get(node) === html));
      if (!node && cursor && !used.has(cursor) && !key && !nodeKey(cursor) && sameKind(cursor)) node = cursor;
      const unchanged = node && sources.get(node) === html;
      if (!node) {
        node = next.cloneNode(false);
        if (node.nodeType === 1) reconcile(node, next);
      } else if (sources.get(node) !== html) {
        if (node.nodeType === 1) {
          for (const attr of [...node.attributes]) if (!next.hasAttribute(attr.name) && !(node.tagName === 'DETAILS' && attr.name === 'open')) node.removeAttribute(attr.name);
          for (const attr of next.attributes) if (!(node.tagName === 'DETAILS' && attr.name === 'open')) node.setAttribute(attr.name, attr.value);
          reconcile(node, next);
        } else if (next.nodeValue.startsWith(node.nodeValue)) node.appendData(next.nodeValue.slice(node.nodeValue.length));
        else node.nodeValue = next.nodeValue;
      }
      sources.set(node, html); used.add(node);
      if (node !== cursor) parent.insertBefore(node, cursor);
      cursor = node.nextSibling;
      // enhance() inserts a player immediately after its source link.
      if (unchanged && node.nodeName === 'A' && cursor?.matches?.('.reader-video') && !sources.has(cursor)) {
        used.add(cursor); cursor = cursor.nextSibling;
      }
    }
    old.forEach(node => { if (!used.has(node)) node.remove(); });
  }
  function protectedContent(container){
    const selection=root.getSelection?.();
    return Boolean(selection&&!selection.isCollapsed&&(container.contains(selection.anchorNode)||container.contains(selection.focusNode))) ||
      [...container.querySelectorAll('video,audio')].some(media=>!media.paused) || root.HubMarkdown?.isPreviewing?.(container);
  }
  function progressHTML(group){
    return `<details class="reader-progress" data-reader-key="${escape(group.key)}"><summary>Progress <span>${group.messages.length}</span></summary><div class="reader-progress-body"></div></details>`;
  }
  function renderProgress(node,state,offset=null){
    if(!state)return;
    const group=state.window.groups.find(group=>group.key===node.dataset.readerKey);if(!group)return;
    if(!node.open)return;
    const oldLength=node._noteLength||0;
    let start=offset??node._noteStart??Math.max(0,group.messages.length-12);
    if(offset===null&&start+12>=oldLength&&!protectedContent(node))start=Math.max(0,group.messages.length-12);
    start=Math.max(0,Math.min(start,group.messages.length-1));
    let end=start,size=0;
    while(end<group.messages.length&&end-start<12&&(end===start||size+group.messages[end].text.length<=180000))size+=group.messages[end++].text.length;
    node._noteStart=start;node._noteEnd=end;node._noteLength=group.messages.length;
    const controls=(start||end<group.messages.length)?`<div class="progress-pages"><button type="button" data-progress-page="-1"${start?'':' disabled'}>Earlier</button><span>${start+1}–${end} / ${group.messages.length}</span><button type="button" data-progress-page="1"${end<group.messages.length?'':' disabled'}>Newer</button></div>`:'';
    const template=node.ownerDocument.createElement('template');
    template.innerHTML=controls+group.messages.slice(start,end).map((message,index)=>messageHTML(message,index,group.messages.slice(start,end))).join('');
    reconcile(node.querySelector('.reader-progress-body'),template.content);
    root.HubMarkdown?.enhance(node);
  }
  function drawHistory(container,state){
    const template=container.ownerDocument.createElement('template'),previous=renderCache.get(container)||new Map(),cache=new Map();
    const html=state.window.view.map(group=>{
      if(group.type==='progress')return progressHTML(group);
      const message=group.messages[0],signature=JSON.stringify(message);
      const html=previous.get(signature)||messageHTML(message);cache.set(signature,html);return html;
    }).join('');
    renderCache.set(container,cache);
    const older=state.window.start>0||state.timeline.earlier,newer=state.window.end<state.window.groups.length||state.timeline.past;
    const control=(direction,label)=>`<button type="button" class="reader-page" data-history-page="${direction}"${state.loading===direction?' disabled aria-busy="true"':''}>${state.loading===direction?'Loading history…':label}</button>`;
    template.innerHTML=(older?control('older','Earlier messages ↑'):'')+
      (state.historyError?`<p class="reader-history-error" role="status">${escape(state.historyError)}</p>`:'')+
      '<div class="reader-history">'+html+'</div>'+(newer?'<div class="reader-page-controls">'+control('newer','Newer messages ↓')+'<button type="button" class="reader-page" data-history-page="latest">Latest</button></div>':'')+
      (!newer&&!state.hideQueue?renderQueued(state.text):'');
    // A live append outside the page being read must not cancel a swipe.
    // Actual layout changes still cancel it so one gesture cannot page twice.
    if(container._historyMarkup!==template.innerHTML)stopPaging(container);
    container._historyMarkup=template.innerHTML;
    reconcile(container,template.content);
    prepareReplies(container);
    for(const node of container.querySelectorAll('.reader-progress[open]'))renderProgress(node,state);
    if(state.timeline.source)for(const message of state.timeline.messages){
      if(!message.part||message.part.total<=message.text.length)continue;
      const node=[...container.querySelectorAll('[data-message-id]')].find(node=>node.dataset.messageId===root.HubHistory.key(message));
      if(!node||node.querySelector('.reader-long-message'))continue;
      const more=container.ownerDocument.createElement('button');more.type='button';more.className='text-button reader-long-message';
      more.dataset.historyMessage=message.id;more.textContent='Long message · Read all parts';node.append(more);
    }
    if(container.dataset.historyPast!==String(newer)){
      container.dataset.historyPast=String(newer);
      container.dispatchEvent(new CustomEvent('readerwindowchange',{bubbles:true}));
    }
  }
  async function loadHistory(container,state,direction){
    if(state.loading)return;
    const request=state.timeline.request(direction);if(!request)return;
    const controller=new AbortController();state.request=controller;state.loading=direction;state.historyError='';
    drawHistory(container,state);root.HubMarkdown?.enhance(container);
    try{
      const response=await fetch('/api/archive/conversation?'+new URLSearchParams(request.parameters),{cache:'no-store',signal:controller.signal});
      const value=await response.json();if(!response.ok)throw Error(value.error||'Couldn’t load history. Try again.');
      if(controller.signal.aborted||histories.get(container)!==state||protectedContent(container))return;
      const reader=container.closest('.agent-reader'),top=reader?.getBoundingClientRect().top||0;
      const anchor=[...container.querySelectorAll('.reader-entry,.reader-progress')].find(node=>node.getBoundingClientRect().bottom>top);
      const before=anchor?.getBoundingClientRect().top;
      if(!state.timeline.accept(request,value))return;
      state.window.update(root.HubHistory.groups(state.timeline.messages,state.decision),false,anchor?.dataset.messageId||anchor?.dataset.readerKey);
      state.request=null;state.loading='';drawHistory(container,state);root.HubMarkdown?.enhance(container);
      if(reader&&anchor?.isConnected)reader.scrollTop+=anchor.getBoundingClientRect().top-before;
      else if(reader)reader.scrollTop=direction==='older'?reader.scrollHeight:0;
      container.dispatchEvent(new CustomEvent('readerpage',{bubbles:true}));
    }catch(error){
      if(error.name!=='AbortError'&&histories.get(container)===state)state.historyError=error.message;
    }finally{
      if(state.request===controller){
        state.request=null;state.loading='';
        if(histories.get(container)===state){drawHistory(container,state);root.HubMarkdown?.enhance(container);}
      }
    }
  }
  function page(container,direction){
    stopPaging(container);
    const state=histories.get(container);if(!state||direction!=='latest'&&protectedContent(container))return false;
    const reader=container.closest('.agent-reader'),top=reader?.getBoundingClientRect().top||0;
    const anchor=[...container.querySelectorAll('.reader-entry,.reader-progress')].find(node=>node.getBoundingClientRect().bottom>top);
    const before=anchor?.getBoundingClientRect().top;
    if(direction==='latest'){
      latest(container,true);
      if(reader)reader.scrollTop=reader.scrollHeight;
      container.dispatchEvent(new CustomEvent('readerpage',{bubbles:true}));return true;
    }else if(!state.window[direction]?.()){
      if(state.timeline.request(direction)){loadHistory(container,state,direction);return true;}
      if(direction==='newer'&&state.timeline.past)return page(container,'latest');
      return false;
    }
    drawHistory(container,state);root.HubMarkdown?.enhance(container);
    if(reader&&anchor?.isConnected)reader.scrollTop+=anchor.getBoundingClientRect().top-before;
    else if(reader)reader.scrollTop=direction==='older'?reader.scrollHeight:0;
    container.dispatchEvent(new CustomEvent('readerpage',{bubbles:true}));return true;
  }
  function latest(container,explicit=false){
    stopPaging(container);
    const state=histories.get(container);if(!state||!state.timeline.past&&!state.loading&&state.window.end===state.window.groups.length)return;
    if(protectedContent(container)&&!explicit)return;
    if(explicit){
      for(const media of container.querySelectorAll('video,audio'))media.pause();
      const selection=root.getSelection?.();
      if(selection&&(container.contains(selection.anchorNode)||container.contains(selection.focusNode)))selection.removeAllRanges();
    }
    state.request?.abort();state.request=null;state.loading='';state.historyError='';state.timeline.latest();
    state.window.update(root.HubHistory.groups(state.timeline.messages,state.decision),true);
    drawHistory(container,state);root.HubMarkdown?.enhance(container);
  }
  function reveal(container,identity){
    stopPaging(container);
    const state=histories.get(container);if(!state)return null;
    const existing=[...container.querySelectorAll('[data-message-id]')].find(node=>node.dataset.messageId===identity);
    if(existing?.getClientRects().length&&!existing.closest('details:not([open])'))return existing;
    if(protectedContent(container)||!state.window.reveal(identity))return null;
    drawHistory(container,state);
    const group=state.window.view.find(group=>group.messages.some(message=>root.HubHistory.key(message)===identity));
    if(group?.type==='progress'){
      const node=[...container.querySelectorAll('.reader-progress')].find(node=>node.dataset.readerKey===group.key);
      node.open=true;renderProgress(node,state,group.messages.findIndex(message=>root.HubHistory.key(message)===identity));
    }
    root.HubMarkdown?.enhance(container);
    return [...container.querySelectorAll('[data-message-id]')].find(node=>node.dataset.messageId===identity)||null;
  }
  function bindHistory(container){
    bindReplies(container);
    container.addEventListener('toggle',event=>{
      if(event.target.matches('.reader-progress'))renderProgress(event.target,histories.get(container));
    },true);
    container.addEventListener('click',event=>{
      const button=event.target.closest('[data-history-page],[data-progress-page],[data-history-message]');if(!button)return;
      if(button.hasAttribute('data-history-message')){
        const source=histories.get(container)?.timeline.source;
        if(source)root.HubArchive?.openSource({...source,message_id:button.dataset.historyMessage});
      }
      if(button.hasAttribute('data-history-page'))page(container,button.dataset.historyPage);
      if(button.hasAttribute('data-progress-page')){
        const node=button.closest('.reader-progress');
        if(!protectedContent(node)){renderProgress(node,histories.get(container),Number(button.dataset.progressPage)<0?Math.max(0,node._noteStart-12):node._noteEnd);node.scrollIntoView({block:'start',behavior:'instant'});}
      }
    });
    const reader=container.closest('.agent-reader');if(!reader)return;
    let intent=null,frame=null;
    function cancel(){intent=null;if(frame!==null)cancelAnimationFrame(frame);frame=null;}
    navigation.set(container,{cancel});
    function check(){
      frame=null;
      if(!intent)return;
      if(Date.now()-intent.at>1800||!reader.getClientRects().length){cancel();return;}
      const direction=intent.direction<0&&reader.scrollTop<100?'older':intent.direction>0&&reader.scrollHeight-reader.scrollTop-reader.clientHeight<100?'newer':null;
      const moved=intent.direction*(reader.scrollTop-intent.top)>0;
      // One input may load one page. Layout/anchor changes cannot reuse it.
      if(direction&&(moved||intent.edge)){cancel();page(container,direction);}
    }
    function arm(event,direction){
      cancel();
      if(!direction)return;
      // Scrolling inside a code block or player must not page the conversation.
      for(let node=event.target;node&&node!==reader;node=node.parentElement){
        if(node.scrollHeight>node.clientHeight&&/auto|scroll/.test(getComputedStyle(node).overflowY))return;
      }
      intent={direction,at:Date.now(),top:reader.scrollTop,edge:direction<0?reader.scrollTop<=1:reader.scrollHeight-reader.scrollTop-reader.clientHeight<=1};
      if(frame===null)frame=requestAnimationFrame(check);
    }
    reader.addEventListener('wheel',event=>{if(event.ctrlKey||Math.abs(event.deltaX)>Math.abs(event.deltaY))cancel();else arm(event,Math.sign(event.deltaY));},{passive:true});
    let finger=null;
    reader.addEventListener('pointerdown',cancel,{passive:true});
    reader.addEventListener('touchstart',event=>{cancel();finger=event.touches?.length===1?event.touches[0].clientY:null;},{passive:true});
    reader.addEventListener('touchmove',event=>{const next=event.touches?.length===1?event.touches[0].clientY:null;if(finger!==null&&next!==null)arm(event,Math.sign(finger-next));else cancel();finger=next;},{passive:true});
    reader.addEventListener('touchcancel',cancel,{passive:true});
    reader.addEventListener('keydown',event=>{if(['ArrowUp','PageUp','ArrowDown','PageDown'].includes(event.key)&&!event.target.closest('input,textarea,select,[contenteditable=true]'))arm(event,event.key.endsWith('Up')?-1:1);else cancel();});
    reader.addEventListener('scroll',()=>{
      // Touch momentum can reach the edge well after the last touchmove.
      if(intent&&Date.now()-intent.at<=1800)intent.at=Date.now();
      if(intent&&frame===null)frame=requestAnimationFrame(check);
    },{passive:true});
  }
  function update(container, text, messages, history, hideQueue = false, options={}) {
    if(!container._historyBound){bindHistory(container);container._historyBound=true;}
    const template = container.ownerDocument.createElement('template');
    if(history&&messages?.length&&root.HubMarkdown&&root.HubHistory){
      let state=histories.get(container);
      if(!state){state={window:new root.HubHistory.Window(),timeline:new root.HubHistory.Timeline()};histories.set(container,state);}
      if(state.timeline.update(messages,options.source)){state.request?.abort();state.request=null;state.loading='';state.historyError='';}
      Object.assign(state,{text,hideQueue,decision:Boolean(options.decision)});
      state.window.update(root.HubHistory.groups(state.timeline.messages,state.decision),Boolean(options.follow)&&!state.timeline.past&&!protectedContent(container),options.anchor);
      drawHistory(container,state);return;
    }
    stopPaging(container);
    histories.get(container)?.request?.abort();histories.delete(container);delete container.dataset.historyPast;delete container._historyMarkup;
    if (history && messages?.length && root.HubMarkdown) {
      const previous = renderCache.get(container) || new Map(), cache = new Map();
      const html = messages.map((message, index) => {
        const signature = JSON.stringify([message, messages[index - 1]?.role, messages[index - 1]?.kind, messages[index - 1]?.created]);
        const item = previous.get(signature) || messageHTML(message, index, messages);
        cache.set(signature, item); return item;
      }).join('');
      renderCache.set(container, cache);
      template.innerHTML = '<div class="reader-history">' + html + '</div>' + (hideQueue?'':renderQueued(text));
    } else { renderCache.delete(container); template.innerHTML = render(text, messages, history, hideQueue); }
    reconcile(container, template.content);
    prepareReplies(container);
  }
  function reset(container){stopPaging(container);histories.get(container)?.request?.abort();histories.delete(container);delete container.dataset.historyPast;delete container._historyMarkup;}
  const reader = { parse, prose, render, renderMessages, userText, queued, update, terminalContent,terminalRows,outline,shortReply,replyHTML,prepareReplies,page,latest,reveal,reset };
  if (typeof module !== "undefined" && module.exports) module.exports = reader;
  else root.AgentReader = reader;
})(globalThis);
