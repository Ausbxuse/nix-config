const {test}=require('node:test');
const assert=require('node:assert/strict');
const MarkdownIt=require('markdown-it');
async function renderer(){
  const {fileLinks}=await import('./frontend/links.mjs'),{artifactMarkdown}=await import('./frontend/artifacts.mjs');
  return new MarkdownIt({html:false,linkify:true}).use(fileLinks).use(artifactMarkdown);
}
test('adjacent local images, linked videos and simple file lists group inside their reply',async()=>{
  const md=await renderer();
  for(const text of ['![Mean](plots/mean.png)\n\n![Quantile](plots/quantile.png)',
    '[First](first.webm) [Second](second.webm)','- [Mean](mean.csv)\n- [Quantile](quantile.csv)']){
    const html=md.render(text);assert.match(html,/data-artifact-group/);assert.equal((html.match(/data-artifact-href=/g)||[]).length,2);
  }
  assert.match(md.render('[Mean](mean.png)\n\n[Quantile](quantile.mp4)'),/data-artifact-compare/);
  assert.doesNotMatch(md.render('[Mean](mean.csv)\n\n[Quantile](quantile.csv)'),/data-artifact-compare/);
});
test('grouping retains surrounding explanation, headings and inline links without merging sections',async()=>{
  const md=await renderer(),html=md.render('Measured results.\n\n[a](a.png)\n\n[b](b.png)\n\n## Another trial\n\n[c](c.png)\n\nThe [trace](trace.png) shows the result.');
  assert.match(html,/<p>Measured results.<\/p>/);assert.equal((html.match(/data-artifact-group/g)||[]).length,1);
  assert.match(html,/<h2>Another trial<\/h2>/);assert.match(html,/<p>The <a href="trace.png">trace<\/a> shows the result.<\/p>/);
  assert(html.indexOf('Another trial')>html.indexOf('data-artifact-href="b.png"'));
});
test('duplicate file links become one card with the original descriptive label',async()=>{
  const html=(await renderer()).render('![Matched mean trace](mean.png)\n\n[Open full resolution](mean.png)');
  assert.equal((html.match(/data-artifact-href=/g)||[]).length,1);assert.match(html,/Matched mean trace/);assert.doesNotMatch(html,/Open full resolution|data-artifact-compare/);
});
test('code, external addresses, source files and unsafe schemes never become artifact groups',async()=>{
  const md=await renderer();
  for(const input of ['```text\n[a](a.png)\n[b](b.png)\n```','[a](https://example.com/a.png)\n\n[b](https://example.com/b.png)',
    '[a](src/a.py)\n\n[b](src/b.py)','[a](javascript:evil.png)\n\n[b](file:///private.png)'])assert.doesNotMatch(md.render(input),/data-artifact-group/);
  const {candidate}=await import('./frontend/artifacts.mjs');
  for(const value of [null,'//evil/a.png','a%00.png','%zz.png','~/a.png','data:image/png,abc'])assert.equal(candidate(value),null);
});
test('labels and paths are escaped and all files remain available in large groups',async()=>{
  const md=await renderer(),items=Array.from({length:30},(_,i)=>`[file ${i}](plot-${i}.png)`),html=md.render(items.join('\n\n'));
  for(let i=0;i<30;i++)assert(html.includes(`href="plot-${i}.png"`));
  assert.match(html,/data-artifact-more/);assert.match(html,/data-artifact-index="4"[^>]*hidden/);
  const escaped=md.render('[<script>alert(1)</script>](a.png)\n\n[Two](b.png)');assert(!escaped.includes('<script>'));
});
