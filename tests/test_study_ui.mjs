// Offline UI-logic regression: no browser, key, or provider request.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../static/app.js', import.meta.url), 'utf8');
assert.match(source, /'page-select'\)\.addEventListener\('change', \(\) => \{ invalidateStudy\(\)/);
assert.match(source, /'chapter-select'\)\.addEventListener\('change', \(\) => \{\s*invalidateStudy\(\)/);
assert.match(source, /'study-lens'\)\.addEventListener\('change', \(\) => \{ invalidateStudy\(\)/);
assert.match(source, /'ask-question'\)\.addEventListener\('input', invalidateStudy\)/);

const elements = new Map();
function field(id) {
  if (!elements.has(id)) elements.set(id, {
    value: '', checked: false, hidden: true, disabled: false, textContent: '',
    style: {}, scrollIntoView() {}
  });
  return elements.get(id);
}
const context = vm.createContext({
  document: {getElementById: field},
  fetch: async (path) => {
    if (path !== '/api/study/preview') throw new Error(`Unexpected provider send: ${path}`);
    return {ok:true, json:async () => ({provider:'openai', model:'fake-model',
      characters:120, coverage_disclosure:'Source-character coverage: 100 of 100.',
      text:'[p.1] Example.', instruction:'Explain the example.', approval_hash:'fake-approval'})};
  }
});
vm.runInContext(source.replace(/\binit\(\);\s*$/, ''), context);
const state = vm.runInContext('state', context);
const baseline = {
  'document-select':'doc-1', 'chapter-select':'1', 'page-select':'1',
  'study-lens':'general', 'study-mode':'ai', 'ai-provider':'openai',
  'ask-scope':'page', 'ask-question':'What does this control do?'
};
async function openPreview() {
  for (const [id, value] of Object.entries(baseline)) field(id).value = value;
  field('show-front').checked = false;
  state.structure = {document:{id:'doc-1'}};
  state.page = {page_no:1};
  state.ai = {configured:['openai']};
  await vm.runInContext("runStudy('ask_page')", context);
  assert.ok(state.preview);
  assert.equal(field('ai-preview').hidden, false);
}

for (const [id, changed] of [
  ['page-select','2'], ['chapter-select','2'], ['study-lens','audit'],
  ['ask-question','What changed?'], ['ask-scope','chapter'],
  ['ai-provider','anthropic']
]) {
  await openPreview();
  state.study = {body:{title:'Old result'}};
  field('study-result').hidden = false;
  field('study-exports').hidden = false;
  field(id).value = changed;
  vm.runInContext('invalidateStudy()', context);
  assert.equal(state.preview, null, `${id} should clear preview`);
  assert.equal(state.study, null, `${id} should clear old study result`);
  assert.equal(field('ai-preview').hidden, true);
  assert.equal(field('study-result').hidden, true);
  assert.equal(field('study-exports').hidden, true);
  await vm.runInContext('sendStudyPreview()', context);
}

// Defence in depth: even a programmatic selector change that missed its change
// event must be refused immediately before any generate/provider request.
for (const [id, changed] of [
  ['page-select','2'], ['chapter-select','2'], ['study-lens','audit'],
  ['ask-question','What changed?']
]) {
  await openPreview();
  field(id).value = changed;
  await vm.runInContext('sendStudyPreview()', context);
  assert.equal(state.preview, null);
  assert.match(field('study-message').textContent, /Study inputs changed/);
}
console.log('UI stale-preview negative checks passed: page, chapter, lens, question, scope, provider.');
