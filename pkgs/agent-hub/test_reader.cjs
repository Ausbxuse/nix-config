const { test } = require('node:test');
const assert = require('node:assert/strict');
const reader = require('./static/reader.js');
const questionReply = replies => '<send_user_message_question_reply>\n' + JSON.stringify(replies) + '\n</send_user_message_question_reply>';
const replyItem = {question:'When should I notify you?',answer:'Only notify if I haven’t seen it',questionItemId:'["request_user_input_async","call_private",0]'};
const promptRedraw = require('./terminal-message-boundaries-fixtures.json').prompt_diff_redraw;

test('a partial terminal redraw never appends file changes to the user bubble', () => {
  const blocks = reader.parse(promptRedraw.capture);
  assert.deepEqual(blocks.map(b => b.type), ['input','output','tool']);
  assert.equal(blocks[0].text, promptRedraw.visible_prompt);
  assert.match(blocks[1].text, /import re/);
  assert.match(blocks[1].text, /supervisor-schema\.json/);
  for (const history of [false,true]) {
    const html = reader.render(promptRedraw.capture, [], history);
    const bubble = html.match(/<section[^>]*reader-user[\s\S]*?<\/section>/)?.[0];
    assert.ok(bubble);
    assert.doesNotMatch(bubble, /import re|coordination\.py|request_intent/);
    assert.match(html, /Terminal details<\/summary><div class="reader-details-body"><pre/);
    assert.match(html, /import re/);
  }
});

test('patch boundaries do not rewrite native messages, fenced examples, or ordinary user code', () => {
  const patch = promptRedraw.capture.split('\n').slice(1,-4).join('\n');
  for (const fence of ['```','~~~']) {
    const blocks = reader.parse('› Explain this example:\n  '+fence+'diff\n'+patch+'\n  '+fence);
    assert.deepEqual(blocks.map(b => b.type), ['input']);
    assert.match(blocks[0].text, /import re/);
  }
  assert.deepEqual(reader.parse('› Explain these lines:\n    1 +import re\n    2 +import os').map(b => b.type), ['input']);
  global.HubMarkdown = {render: text => text};
  try {
    const html = reader.render(promptRedraw.capture, [{id:'native',role:'user',text:promptRedraw.original}], true);
    assert.match(html, /You could just allocate an agent/);
    assert.doesNotMatch(html, /import re|coordination\.py/);
    assert.match(reader.render('', [{id:'quoted',role:'user',text:patch}], true), /import re/);
  } finally {delete global.HubMarkdown;}
});

test('latest response appears before earlier output and subsequent tools stay folded', () => {
  const html = reader.render('› Check the build\n• First response.\n• Ran make\n  └ ok\n• Current response.\n• Explored\n  └ Read app.js');
  assert(html.indexOf('Current response.') < html.indexOf('First response.'));
  assert.match(html, /data-reader-key="activity"><summary>Tool activity/);
  assert.match(html, /data-reader-key="earlier"><summary>Earlier output/);
  assert.match(html, /Read app.js/);
});

test('history folds adjacent tool steps without changing response order or hiding failures', () => {
  const html = reader.render('• Starting the check.\n• Ran make\n  └ ok\n• Explored\n  └ Read app.js\n• Build passed.\n• Failed (exit 1) deploy\n  └ permission denied\n• Deployment needs your help.', [], true);
  assert.match(html, /data-reader-key="history-tools-1"><summary>2 tool steps/);
  assert.match(html, /data-reader-key="history-tools-4-failed" open><summary>1 tool step · check failed step/);
  assert.match(html, /data-reader-key="history-tool-4" open/);
  assert.match(html, /permission denied/);
  const ordered = ['Starting the check.', 'Ran make', 'Read app.js', 'Build passed.', 'permission denied', 'Deployment needs your help.'];
  for (let i = 1; i < ordered.length; i++) assert(html.indexOf(ordered[i - 1]) < html.indexOf(ordered[i]));
});

test('reflows terminal wrapping while preserving paragraphs, lists and fenced code', () => {
  const html = reader.render('• This sentence wraps\n  across the terminal.\n\n  - One item wraps\n    onto this line.\n  - Second item\n\n  ```js\n  if (ok) {\n    run();\n  }\n  ```');
  assert.match(html, /This sentence wraps across the terminal\./);
  assert.match(html, /One item wraps onto this line\./);
  assert.match(html, /if \(ok\) \{\n  run\(\);\n\}/);
  assert.equal((html.match(/class="reader-list-item"/g) || []).length, 2);
});

test('terminal column tables become accessible HTML tables', () => {
  const html = reader.render('• Checkpoint     10k     20k\n  Real           60%     40%\n  Sim             0%     25%\n\n  Task    Rank\n  ━━━━━━  ━━━━\n  Plates  +0.5\n  ──────  ────\n  Bowl    −1.0');
  assert.equal((html.match(/<table>/g) || []).length, 2);
  assert.match(html, /<th scope="col">Checkpoint<\/th>/);
  for (const value of ['60%', '40%', '0%', '25%', '+0.5', '−1.0']) assert(html.includes(`>${value}</td>`));
});

test('boxed tables preserve multiline cells and units', () => {
  const html = reader.render('● Recovered space.\n\n  ┌──────┬─────────┐\n  │ Size │ What    │\n  ├──────┼─────────┤\n  │ 4.1  │ Old logs│\n  │ GB   │         │\n  ├──────┼─────────┤\n  │ 0.1  │ Caches  │\n  │ GB   │         │\n  └──────┴─────────┘');
  assert.match(html, /<td>4.1 GB<\/td>/);
  assert.match(html, /<td>0.1 GB<\/td>/);
});

test('terminal prompts are labeled You without inventing a delivery receipt', () => {
  const blocks = reader.parse('● Finished the task.\n✻ Crunched for 2m 9s · done 8:19 PM\n※ recap: Work is done.\n  (disable recaps in /config)\n❯\u00a0Check the other agent');
  assert.deepEqual(blocks.map((b) => b.type), ['assistant', 'input']);
  const html = reader.render('● Finished.\n❯\u00a0Delete the files');
  assert.match(html, /reader-entry reader-user" data-message-source="terminal" data-message-role="user" aria-label="You"/);
  assert.match(html, /<span class="reader-speaker">You<\/span>/);
  assert.doesNotMatch(html, /Input in terminal|May include an unsent draft/);
  assert.doesNotMatch(html, /You sent/);
});

test('terminal user prompts and queued instructions use the same author style as native user messages', () => {
  const html = reader.render('› Check the build\n• Running the check.\n• Queued follow-up inputs\n  ↳ Check logs next', [], true);
  assert.equal((html.match(/reader-entry reader-user/g) || []).length, 2);
  assert.equal((html.match(/reader-speaker">You</g) || []).length, 2);
  assert.match(html, /reader-meta">Queued<\/span>/);
  assert.match(html, /Check logs next/);
});

test('queued messages stay in separate rows while terminal wrapping stays within its message', () => {
  const text = '• Queued follow-up inputs\n  ↳ Check the checkpoint comparison\n    across both hosts.\n  ↳ Plot `stdmean` vs **quantile**.\n  ↳ Keep the arrow ↳ inside this message.';
  for (const history of [true, false]) {
    const html = reader.render(text, [], history);
    assert.match(html, /role="list" aria-label="Queued messages"/);
    assert.equal((html.match(/class="queue-arrow"/g) || []).length, 3);
    assert.match(html, /<p>Check the checkpoint comparison across both hosts\.<\/p><\/div><\/li><li>/);
    assert.match(html, /<code>stdmean<\/code> vs <strong>quantile<\/strong>/);
    assert.match(html, /Keep the arrow ↳ inside this message/);
    assert.equal((html.match(/reader-speaker">You</g) || []).length, 1);
  }
});

test('queued message formatting retains code indentation and escapes untrusted text', () => {
  const text = '• Queued follow-up inputs\n  ↳ Show this example:\n\n    ```text\n    ↳ not a separate message\n      preserve this indentation\n    ```\n  ↳ <img src=x onerror=alert(1)>';
  const html = reader.render(text, [], true);
  assert.equal((html.match(/class="queue-arrow"/g) || []).length, 2);
  assert.match(html, /<code>↳ not a separate message\n  preserve this indentation<\/code>/);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(html, /<img/);
});

test('queue shortcut variants never become queued instructions', () => {
  for (const hint of ['shift+← edit last queued message', 'Shift+↑ edit last queued message', 'shift+up to edit last queued message', 'shift+ edit last queued message', '↳ shift+tab edit last queued message']) {
    const text = '• Queued follow-up inputs\n  ↳ First instruction\n  ↳ Second instruction\n  ' + hint + '\n› Ask Codex to do anything';
    assert.deepEqual(reader.queued(text), ['First instruction', 'Second instruction']);
    const html = reader.render(text, [], true);
    assert.equal((html.match(/class="queue-arrow"/g) || []).length, 2);
    assert.doesNotMatch(html, /edit last queued|shift\+/i);
  }
});

test('a footer-confirmed queue works without a heading but ordinary arrows stay ordinary', () => {
  const queue = '\n  ↳ Compare the checkpoints\n    across both hosts.\n  ↳ Plot the result\n  shift+↑ edit last queued message\n› ';
  const text = '• Checking progress.\n• Working (1s • esc to interrupt)' + queue;
  assert.deepEqual(reader.queued(text), ['Compare the checkpoints\nacross both hosts.', 'Plot the result']);
  for (const ordinary of ['• Here is an example:\n  ↳ Not queued', '  ↳ Recap: The task is ready.', '↳ Interacted with background terminal · make']) {
    assert.deepEqual(reader.queued(ordinary), []);
    assert.doesNotMatch(reader.render(ordinary, [], true), /class="queue-arrow"/);
  }
  assert.deepEqual(reader.queued('shift+↑ edit last queued message\n› '), []);
});

test('Codex footer hints never enter drafts or queues in either reader mode', () => {
  const capture = '• Checking the build.\n• Queued follow-up inputs\n  ↳ Save the trace\n  shift+↑ edit last queued message\n› Keep the draft\n\n  GPT-6.1-Sol low · /tmp/project · weekly 62% left\n  tab to queue message\n  A new terminal shortcut';
  assert.deepEqual(reader.parse(capture).findLast(block=>block.type==='input').text, 'Keep the draft');
  for (const history of [true, false]) {
    const html = reader.render(capture, [], history);
    assert.doesNotMatch(html, /tab to queue|new terminal shortcut|weekly 62%/);
    assert.match(html, /Keep the draft/);
  }
  const queued=capture.replace('› Keep the draft','› ');
  assert.deepEqual(reader.queued(queued), ['Save the trace']);
  global.HubMarkdown = {render: text => text};
  try {
    const html = reader.render(queued, [{id:'reply',role:'assistant',text:'The trace is saved.'}], true);
    assert.match(html, /The trace is saved/);
    assert.match(html, /Save the trace/);
    assert.doesNotMatch(html, /tab to queue|new terminal shortcut/);
    assert.match(reader.renderMessages([{id:'literal',role:'user',text:'tab to queue message'}]), /tab to queue message/);
  } finally {delete global.HubMarkdown;}
});

test('native history retains the live queue and removes it once it is consumed', () => {
  global.HubMarkdown = {render: text => text};
  try {
    const messages = [{id:'one', role:'assistant',text:'The latest native reply.'}];
    const text = '• The latest native reply.\n• Queued follow-up inputs\n  ↳ Compare both hosts\n  ↳ Save the plot\n  shift+↑ edit last queued message\n› ';
    for (const history of [true, false]) {
      const html = reader.render(text, messages, history);
      assert.match(html, /The latest native reply/);
      assert.equal((html.match(/class="queue-arrow"/g) || []).length, 2);
      assert.match(html, /reader-speaker">You<.*reader-meta">Queued/);
      assert.doesNotMatch(html, /edit last queued/);
      assert.doesNotMatch(reader.render(text + '\n› Compare both hosts\n• Comparing now.', messages, history), /class="queue-arrow"/);
    }
  } finally {delete global.HubMarkdown;}
});

test('raw terminal details stay inside a disclosure and open when there is no conversation to show', () => {
  for (const history of [true, false]) {
    const html = reader.render('server: connection refused\nRetry later', [], history);
    assert.match(html, /reader-fold reader-terminal" data-reader-key="latest-terminal-0" open><summary>Terminal details<\/summary><div class="reader-details-body"><pre/);
    assert.match(html, /server: connection refused\nRetry later/);
  }
  const html = reader.render('Welcome to the terminal\n› Check the build\n• Build passed.', [], true);
  assert.match(html, /reader-terminal" data-reader-key="history-terminal-0"><summary>Terminal details/);
  assert.match(html, /<\/pre><\/div><\/details><section class="reader-entry reader-user"/);
  assert.match(html, /Build passed/);
});

test('every expanded tool and history section has a contained body', () => {
  const text = '› Check the build\n• First response.\n• Ran make\n  └ ok\n• Failed (exit 1) deploy\n  └ denied\n• Current response.\n※ recap: Both steps were checked.\n• Explored\n  └ Read app.js';
  for (const history of [true, false]) {
    const html = reader.render(text, [], history);
    assert.equal((html.match(/<details /g) || []).length, (html.match(/<div class="reader-details-body">/g) || []).length);
    assert.doesNotMatch(html, /<\/summary><(?:pre|details)/);
    assert.match(html, /Failed \(exit 1\) deploy/);
    assert.match(html, /denied/);
    assert.doesNotMatch(html, /Both steps were checked|Terminal recap/);
  }
});

test('capture notice fixtures match the backend without erasing real content', () => {
  for (const {name, capture, expected} of require('./terminal-notices-fixtures.json')) {
    assert.equal(reader.terminalContent(capture), expected, name);
    assert.equal(reader.terminalContent(expected), expected, name + ' is idempotent');
  }
});

test('weekly nags and wrapped recaps disappear from both reader modes, not original messages', () => {
  const warning = '⚠ Heads up, you have less than 5% of your weekly limit left. Run /status for a breakdown.';
  const text = '• The traces are saved.\n\n' + warning + '\n\n  ↳ Recap: Duplicate terminal summary.\n           Wrapped duplicate line.\n\n› Next instruction\n• Next reply.';
  for (const history of [true, false]) {
    const html = reader.render(text, [], history);
    assert.doesNotMatch(html, /Heads up|weekly limit|Duplicate terminal|Wrapped duplicate|Recap/);
    for (const expected of ['The traces are saved.', 'Next instruction', 'Next reply.']) assert(html.includes(expected));
  }
  global.HubMarkdown = {render: text => text};
  try {
    const html = reader.render(text, [
      {id:'user',role:'user',text:'Explain this: ' + warning},
      {id:'agent',role:'assistant',text:'Recap: The study is complete.'},
    ], true);
    assert.match(html, /Explain this: ⚠ Heads up/);
    assert.match(html, /Recap: The study is complete/);
  } finally {delete global.HubMarkdown;}
});

test('HTML and terminal hyperlink escapes cannot inject active markup', () => {
  const html = reader.render('• \x1b[31m<script>alert(1)</script>\x1b[0m\n  `</code><img src=x onerror=alert(1)>`\n  [click](javascript:alert(1))\n  \x1b]8;;https://example.com\x1b\\Visible link\x1b]8;;\x1b\\');
  assert.doesNotMatch(html, /<(?:script|img|a)\b|\x1b/);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /Visible link/);
});

test('known UI chrome is removed without hiding unfamiliar output or queued work', () => {
  const html = reader.render('• The task is still working.\n  Important error: access denied\n• Working (2m • esc to interrupt)\n• Queued follow-up inputs\n  ↳ Check logs next\n  shift+← edit last queued message\n› Ask Codex to do anything\n  GPT-6.1-Sol low · ~/project · weekly 62% left\n  ← for agents · ? for shortcuts');
  assert.match(html, /Important error: access denied/);
  assert.match(html, /Check logs next/);
  assert.doesNotMatch(html, /weekly|shortcuts|esc to interrupt|Ask Codex/);
});

test('unknown output and failure details remain accessible', () => {
  assert.match(reader.render('server: connection refused\nRetry later'), /server: connection refused\nRetry later/);
  const html = reader.render('• Failed (exit 1) make\n  └ fatal: no target');
  assert.match(html, /data-reader-key="activity" open/);
  assert.match(html, /fatal: no target/);
});

test('reasoning never leaks through latest, full or direct message rendering',()=>{
  global.HubMarkdown={render:text=>text};
  try{
    for(const kind of ['analysis','thinking','reasoning','reasoning_summary'])for(const field of ['phase','channel','type']){
      const hidden={id:'hidden',role:'assistant',text:'PRIVATE TEST REASONING',[field]:kind};
      const answer={id:'answer',role:'assistant',text:'I think the result is useful.'};
      for(const history of [false,true]){
        const html=reader.render('• PRIVATE TEST REASONING',[answer,hidden],history);
        assert(!html.includes('PRIVATE'));assert(html.includes(answer.text));
        assert.equal(reader.render('• PRIVATE TEST REASONING',[hidden],history),'');
      }
      assert.equal(reader.renderMessages([hidden]),'');
    }
    assert.match(reader.renderMessages([{role:'user',id:'quote',text:'Explain the analysis channel.'}]),/Explain the analysis channel/);
  }finally{delete global.HubMarkdown;}
});

test('nested response bullets do not get mistaken for another agent response', () => {
  const blocks = reader.parse('• Results:\n  • First result\n  • Second result');
  assert.equal(blocks.length, 1);
  assert.match(blocks[0].text, /First result/);
});

test('Claude spinner counters do not appear as new response content', () => {
  const first = reader.parse('● Checking the build.\n✻ Churning… (1m 8s · 3.2k tokens)');
  const next = reader.parse('● Checking the build.\n✻ Churning… (1m 9s · 3.3k tokens)');
  assert.deepEqual(first, next);
  assert.equal(first[0].text, 'Checking the build.');
});

test('every Claude spinner frame, including ASCII *, stays out of the response', () => {
  const base = reader.parse('● Checking the build.');
  for (const glyph of ['·', '✢', '*', '✶', '✻', '✽']) {
    assert.deepEqual(reader.parse(`● Checking the build.\n${glyph} Honking… (1m 15s · ↓ 3.8k tokens)`), base, glyph);
  }
});

test('Claude framed status bars and newer thought counters never alter message identity', () => {
  const capture = seconds => `● Checking the trace.\n· Crafting… (6m ${seconds}s · ↓ 31.2k tokens · thought for 1s)           \n────────────────────────────────\n❯ \n────────────────────────────────\n  study · Opus 5.5 M · 5h ${seconds}% · wk 20%\n  -- INSERT -- ⏵⏵ auto mode on · ${seconds} shells · ← for agents`;
  assert.deepEqual(reader.parse(capture(40)), reader.parse(capture(41)));
  assert.equal(reader.parse(capture(40))[0].text, 'Checking the trace.');
  for(const history of [true,false])assert.doesNotMatch(reader.render(capture(40),[],history),/Opus|Crafting|INSERT|shells|31\.2k/);
});

test('dated done rows are compact 24-hour metadata and decorated recaps never become agent replies', () => {
  const capture='• The actual result.\n\n  done Oct 10 at 6:05 PM\n\n─ Conversation recap ────────────\n\n  Generated summary.\n  Wrapped recap.\n\n› ';
  for(const history of [true,false]){
    const html=reader.render(capture,[],history);
    assert.match(html,/<time datetime="18:05"[^>]*>Oct 10 · 18:05<\/time>/);
    assert.doesNotMatch(html,/done Oct|6:05 PM|Conversation recap|Generated summary|Wrapped recap/);
    assert.equal((html.match(/reader-entry reader-assistant/g)||[]).length,1);
  }
  assert.match(reader.render('● Saved.\n✻ Brewed for 2m 9s · done Oct 10 at 6:05 PM',[],true),/Elapsed time: 2m 9s/);
  assert.equal(reader.parse('  done Oct 10 at 6:05 PM').length,0);
});

test('compaction and other timed CLI phases do not become replies or duplicate timers', () => {
  const response = '• The comparison is saved.';
  for (const status of [
    '• Compacting context (2m 13s • esc to interrupt)',
    '• Compacting context (2m 13s • esc to interrupt) · 1 background terminal running · /ps to view',
    '• Compacting context… (2m 13s)', '• Compacting context',
    '✻ Compacting conversation… (2m 13s · ↓ 3.2k tokens)', '✻ Compacting…',
    '✢ Auto-compacting… (2m 13s · esc to interrupt)',
    '• Waiting for background terminal (2m 13s • esc to interrupt)',
    '• Thinking (2m 13s • esc to interrupt)',
  ]) {
    const text = response + '\n' + status + '\n› ';
    assert.deepEqual(reader.parse(text), reader.parse(response), status);
    assert.deepEqual(reader.parse(text.replace('13s', '14s')), reader.parse(response), status);
    for (const history of [true, false]) {
      assert.doesNotMatch(reader.render(text, [], history), /Compacting|compacting|Thinking|Waiting for background|esc to interrupt|2m 13s|tokens/);
    }
    assert.deepEqual(reader.queued('• Queued follow-up inputs\n  ↳ Save the plot\n' + status + '\n  shift+↑ edit last queued message'), ['Save the plot']);
  }
});

test('compaction errors, ordinary prose and quoted status examples remain visible', () => {
  for (const message of ['Compacting context failed: access denied.', 'I am compacting the dataset.', 'Context compacted']) {
    assert.equal(reader.parse('• ' + message)[0].text, message);
  }
  const status = '• Compacting context (2m 13s • esc to interrupt)';
  for (const fence of ['```', '~~~']) {
    const text = '• Here is the example:\n  ' + fence + 'text\n  ' + status + '\n  ' + fence + '\n' + status;
    const blocks = reader.parse(text);
    assert.equal(blocks.length, 1);
    assert.equal(blocks[0].text, 'Here is the example:\n' + fence + 'text\n' + status + '\n' + fence);
    const unfinished = reader.parse('• Here is the example:\n  ' + fence + 'text\n  example\n' + status + '\n• The result is ready.');
    assert.equal(unfinished.length, 2);
    assert.doesNotMatch(unfinished[0].text, /Compacting/);
    assert.equal(unfinished[1].text, 'The result is ready.');
  }
  global.HubMarkdown = {render: text => text};
  try {
    assert.match(reader.render('', [{id:'quoted',role:'user',text:status}], true), /Compacting context \(2m 13s/);
  } finally {delete global.HubMarkdown;}
});

test('terminal completion metadata shares the Agent heading and separates time from elapsed duration', () => {
  for (const history of [true, false]) {
    const html = reader.render('• Finished the check.\nWorked for 10s • 3:25 PM', [], history);
    assert.match(html, /reader-entry reader-assistant/);
    assert.match(html, /<span class="reader-speaker">Agent<\/span> <span class="reader-meta">/);
    assert.match(html, /<time datetime="15:25"[^>]*>15:25<\/time>/);
    assert.match(html, /aria-label="Elapsed time: 10s">10s<\/span>/);
    assert.doesNotMatch(html, /Agent output|Worked for|3:25 PM|Latest output/);
    assert.match(html, /Finished the check\./);
  }
});

test('terminal footer clocks handle noon, midnight, and already-24-hour time without inventing a date', () => {
  for (const [reported, expected] of [['12:05 AM','00:05'],['12:05 PM','12:05'],['3:25 pm','15:25'],['9:04','09:04'],['23:42:09','23:42:09']]) {
    const html = reader.render(`• Done.\nWorked for 1m 2s • ${reported}`, [], true);
    assert(html.includes(`datetime="${expected}"`), reported);
    assert.match(html, /aria-label="Elapsed time: 1m 2s">1m 2s/);
  }
  const withoutClock = reader.render('• Done.\nWorked for 10s', [], true);
  assert.doesNotMatch(withoutClock, /<time/);
  assert.match(withoutClock, /aria-label="Elapsed time: 10s"/);
  assert.doesNotMatch(reader.render('• Done.\nWorked for 10s • 29:00', [], true), /<time/);
});

test('Claude completion metadata uses the same clock and elapsed style while native roles stay authoritative', () => {
  const html = reader.render('● Done.\n✻ Crunched for 2m 9s · done 8:19 PM', [], true);
  assert.match(html, /<time datetime="20:19"/);
  assert.match(html, /aria-label="Elapsed time: 2m 9s"/);
  assert.doesNotMatch(html, /Crunched|8:19 PM/);
  const ordinary = reader.render('• The meeting is at 3:25 PM; allow 10s for the check.', [], true);
  assert.match(ordinary, /meeting is at 3:25 PM; allow 10s/);
  assert.doesNotMatch(ordinary, /reader-meta|<time/);
});


test('native transcript roles determine labels and visual classes in every view', () => {
  global.HubMarkdown = {render: text => text};
  try {
    const messages = [{role:'user',text:'Agent: this is my question'}, {role:'assistant',text:'You: this is my reply'}, {text:'Unknown source'}];
    for (const history of [true, false]) {
      const html = reader.render('', messages, history);
      assert.match(html, /reader-user" data-message-id="[^"]+" data-message-role="user" aria-label="You"><h3><span class="reader-speaker">You<\/span>/);
      assert.match(html, /reader-assistant" data-message-id="[^"]+" data-message-role="assistant" aria-label="Agent"><h3><span class="reader-speaker">Agent<\/span>/);
      assert.match(html, /reader-unknown" data-message-id="[^"]+" data-message-role="unknown" aria-label="Message"><h3><span class="reader-speaker">Message<\/span>/);
    }
  } finally {delete global.HubMarkdown;}
});


test('consecutive messages retain roles but use one visible speaker heading', () => {
  global.HubMarkdown = {render: text => text};
  try {
    const html=reader.render('',[{role:'user',text:'Question'},{role:'assistant',text:'Checking'},{role:'assistant',text:'Result'},{role:'user',text:'Next'}],true);
    assert.equal((html.match(/reader-continuation/g)||[]).length,1);
    assert.match(html,/reader-assistant reader-continuation/);
  } finally {delete global.HubMarkdown;}
});
test('command entries have distinct labels and escape output without treating it as HTML', () => {
  global.HubMarkdown = {render: text => text};
  try {
    const html=reader.render('',[{role:'tool',kind:'command',text:'/model opus'},{role:'tool',kind:'command-output',text:'<img src=x onerror=bad()>'}],true);
    assert.match(html,/aria-label="Command"/);
    assert.match(html,/aria-label="Command output"/);
    assert.doesNotMatch(html,/reader-continuation|<img/);
    assert.match(html,/&lt;img/);
  } finally {delete global.HubMarkdown;}
});

test('question replies show question and answer as You in latest, history and archive rendering', () => {
  const text=questionReply([replyItem]);
  const message=Object.freeze({id:'reply-id',role:'user',text,fork:Object.freeze({thread:'thread-id',turn:'turn-id'})});
  global.HubMarkdown={render:value=>`<p>${value}</p>`};
  try {
    for(const html of [reader.render('',[message]),reader.render('',[message],true),reader.renderMessages([message])]) {
      assert.match(html,/data-message-id="user:reply-id" data-message-role="user" aria-label="You"/);
      assert.match(html,/reader-reply-question">When should I notify you\?/);
      assert.match(html,/reader-reply-answer"><p>Only notify if I haven’t seen it<\/p>/);
      assert.match(html,/data-edit-message="reply-id"/);
      assert.doesNotMatch(html,/send_user_message_question|questionItemId|call_private|thread-id|turn-id/);
    }
    assert.equal(message.text,text);
    assert.deepEqual(message.fork,{thread:'thread-id',turn:'turn-id'});
  } finally {delete global.HubMarkdown;}
});

test('multiple question replies retain their order, empty answers and exact Markdown input', () => {
  const replies=[{...replyItem,question:'Use <local> & "private"?',answer:'**Local**\n\n```js\nconst x = "</send_user_message_question_reply>";\n```'},
    {...replyItem,question:'Which devices?',answer:'Phone and desktop — 手机'},
    {...replyItem,question:'Any constraints?',answer:''}];
  const rendered=[];
  global.HubMarkdown={render:value=>{rendered.push(value);return '<p>Rendered answer</p>';}};
  try {
    const html=reader.renderMessages([{role:'user',text:'\r\n'+questionReply(replies)+'\r\n'}]);
    assert.deepEqual(rendered,replies.map(reply=>reply.answer));
    assert.equal((html.match(/class="reader-question-reply"/g)||[]).length,3);
    assert.match(html,/Use &lt;local&gt; &amp; &quot;private&quot;\?/);
    assert(html.indexOf('Use &lt;local&gt;')<html.indexOf('Which devices?'));
    assert(html.indexOf('Which devices?')<html.indexOf('Any constraints?'));
    assert.doesNotMatch(html,/<local>|questionItemId|call_private/);
  } finally {delete global.HubMarkdown;}
});

test('question reply parsing preserves literal examples and incomplete or unknown protocol data', () => {
  const envelope=questionReply([replyItem]);
  const texts=[
    'There are things not parsed correctly: <send_user_message_question... shown in my bubble.',
    'Please fix this:\n'+envelope, envelope+'\nKeep this text.',
    '```xml\n'+envelope+'\n```','~~~text\n'+envelope+'\n~~~',
    envelope.split('\n').map(line=>'    '+line).join('\n'),
    envelope.split('\n').map(line=>'> '+line).join('\n'),
    envelope.replace('send_user_message_question_reply','send_user_message_question_other'),
    envelope.replace('"answer":','"response":'),
    questionReply([{answer:['Phone','Desktop'],question:'Devices?',questionItemId:'id'}]),
    questionReply([{answer:'Yes',question:'Proceed?'}]),
    questionReply([replyItem,null]),questionReply([replyItem,{}]),questionReply([]),
    questionReply(replyItem),envelope.slice(0,-10),
    '<send_user_message_question_reply>not JSON</send_user_message_question_reply>'
  ];
  const rendered=[];
  global.HubMarkdown={render:value=>{rendered.push(value);return 'Original message';}};
  try {
    for(const text of texts) {
      assert.equal(reader.userText(text),text);
      assert.doesNotMatch(reader.renderMessages([{role:'user',text}]),/reader-question-reply/);
      assert.equal(rendered.at(-1),text);
    }
  } finally {delete global.HubMarkdown;}
});

test('question reply conversion does not apply to assistant or tool messages', () => {
  const text=questionReply([replyItem]),rendered=[];
  global.HubMarkdown={render:value=>{rendered.push(value);return 'Original message';}};
  try {
    for(const role of ['assistant','tool','unknown']) {
      const html=reader.renderMessages([{role,text}]);
      assert.doesNotMatch(html,/reader-question-reply/);
      if(role!=='tool')assert.equal(rendered.at(-1),text);
      else assert.match(html,/&lt;send_user_message_question_reply&gt;/);
    }
  } finally {delete global.HubMarkdown;}
});

test('complete terminal question replies use the same bubble and escape untrusted answers', () => {
  const text=questionReply([{...replyItem,answer:'<img src=x onerror=bad()> & **private**'}]);
  for(const history of [false,true]) {
    const html=reader.render('› '+text,[],history);
    assert.match(html,/data-message-role="user" aria-label="You"/);
    assert.match(html,/reader-question-reply/);
    assert.match(html,/&lt;img src=x onerror=bad\(\)&gt; &amp; <strong>private<\/strong>/);
    assert.doesNotMatch(html,/<img|questionItemId|send_user_message_question/);
  }
});

test('editing a question reply offers readable question and answer while retaining the source', () => {
  const replies=[replyItem,{...replyItem,question:'Which devices?',answer:'Phone\nand desktop'}];
  const text=questionReply(replies),message=Object.freeze({role:'user',text});
  assert.equal(reader.userText(message.text),'When should I notify you?\n\nOnly notify if I haven’t seen it\n\nWhich devices?\n\nPhone\nand desktop');
  assert.equal(message.text,text);
});
