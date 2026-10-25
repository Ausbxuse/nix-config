const {test}=require('node:test');
const assert=require('node:assert/strict');
const preview=import('./frontend/file-preview.mjs');

test('CSV preserves quoted commas, newlines, escaped quotes, empty fields and exact values',async()=>{
  const {parseDelimited}=await preview;
  const result=parseDelimited('\uFEFFtask,value,note\r\n"bowl, plate",9007199254740993123,"two\nlines and ""quotes"""\r\n,,\r\n');
  assert.deepEqual(result.rows,[['task','value','note'],['bowl, plate','9007199254740993123','two\nlines and "quotes"'],['','','']]);
  assert.equal(result.more,false);
  assert.deepEqual(parseDelimited('a,b\n=1+1,<script>evil()</script>').rows[1],['=1+1','<script>evil()</script>']);
});
test('partial CSV rows are withheld until complete, and parsing stays bounded',async()=>{
  const {parseDelimited}=await preview;
  assert.deepEqual(parseDelimited('a,b\none,2\ntwo,"par',',',true),{rows:[['a','b'],['one','2']],more:true});
  assert.deepEqual(parseDelimited('a,b\none,2\n',',',true).rows,[['a','b'],['one','2']]);
  assert.throws(()=>parseDelimited('a,"unfinished'));
  assert.throws(()=>parseDelimited('a,"done"unexpected'));
  assert.equal(parseDelimited('a\n'.repeat(2000)).rows.length,1000);
});
test('CSV separators are detected outside quotes and TSV tabs remain tabs',async()=>{
  const {delimitedPreview}=await preview;
  assert.equal(delimitedPreview('task;note;score\nbowl;"a,b,c";0.8\nstack;"d,e,f";0.3','csv').delimiter,';');
  assert.deepEqual(delimitedPreview('task\tnote\nplate\t"a,b"','tsv').rows,[['task','note'],['plate','a,b']]);
});
test('JSON trees retain precise numbers, duplicate keys and prototype-looking keys as data',async()=>{
  const {parseJSONTree}=await preview;
  const value=parseJSONTree('{"id":9007199254740993123,"rate":1.2300e-8,"id":2,"__proto__":{"safe":true}}');
  assert.deepEqual(value.children.map(v=>v.key),['id','rate','id','__proto__']);
  assert.equal(value.children[0].node.value,'9007199254740993123');
  assert.equal(value.children[1].node.value,'1.2300e-8');
  assert.equal(value.children[3].node.children[0].node.value,'true');
  assert.equal({}.safe,undefined);
  assert.equal(parseJSONTree('"line\\n\\u03b1"').value,'line\nα');
});
test('invalid, incomplete and deeply nested JSON falls back rather than inventing values',async()=>{
  const {parseJSONTree}=await preview;
  for(const input of ['','[1,]','{"x":1,}','{"x":01}','{"x":1','null false','"\\qbad"','[truefalse]','['.repeat(100)+'1'+']'.repeat(100)])assert.throws(()=>parseJSONTree(input),input);
  for(const input of ['null','true','[]','{}','[1,false,{"a":[null]}]'])assert.doesNotThrow(()=>parseJSONTree(input));
});
test('JSON Lines keeps line identity and drops only an incomplete last line',async()=>{
  const {jsonPreview}=await preview;
  const value=jsonPreview('{"step":1}\n\n{"step":2}\n{"ste','jsonl',true);
  assert.deepEqual(value.children.map(c=>c.key),['1','3']);
  assert.equal(value.more,true);
  const bounded=jsonPreview('{"step":1}\n'.repeat(1002),'ndjson');
  assert.equal(bounded.children.length,1000);assert.equal(bounded.more,true);
  assert.throws(()=>jsonPreview('{"step":1}\ninvalid','ndjson'),/line 2/);
});
