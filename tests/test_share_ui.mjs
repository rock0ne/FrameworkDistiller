// Offline email-draft checks. No email handler, network, or real document.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../static/app.js', import.meta.url), 'utf8');
const fields = new Map();
const field = id => {
  if (!fields.has(id)) fields.set(id, {value:'', hidden:false, textContent:''});
  return fields.get(id);
};
const context = vm.createContext({document:{getElementById:field}});
vm.runInContext(source.replace(/\binit\(\);\s*$/, ''), context);
const state = vm.runInContext('state', context);
state.structure = {document:{id:'doc-1', title:'Synthetic Handbook', sha256:'a'.repeat(64)}};
state.page = {page_no:3, source_ref:'p.3', source_text:'Alpha principle. Beta control.'};
field('share-thought').value = 'Please discuss the boundary.';
field('share-snippet').value = 'Beta control.';

const draft = vm.runInContext('emailDraft()', context);
assert.match(draft.body, /Pinned SHA-256: a{64}/);
assert.match(draft.body, /p\.3/);
assert.match(draft.body, /Please discuss the boundary/);
assert.match(draft.body, /Beta control/);
assert.ok(draft.url.startsWith('mailto:?subject='));
assert.ok(!draft.url.includes('api/'));
state.structure.document.title = 'Synthetic\r\nBcc: hidden@example.invalid';
const sanitized = vm.runInContext('emailDraft()', context);
assert.ok(!sanitized.subject.includes('\r'));
assert.ok(!sanitized.subject.includes('\n'));
state.structure.document.title = 'Synthetic Handbook';

field('share-snippet').value = 'A made-up quote.';
assert.throws(() => vm.runInContext('emailDraft()', context), /exact selection/);
field('share-snippet').value = 'Beta control.';
state.page = {page_no:4, source_ref:'p.4', source_text:'Different page.'};
assert.throws(() => vm.runInContext('emailDraft()', context), /exact selection/);
state.page = {page_no:3, source_ref:'p.3', source_text:'Alpha principle. Beta control.'};
field('share-snippet').value = '';
field('share-thought').value = '';
assert.throws(() => vm.runInContext('emailDraft()', context), /Write a thought/);
field('share-thought').value = '中'.repeat(500);
assert.throws(() => vm.runInContext('emailDraft()', context), /too long/);

state.shareDraft = draft;
vm.runInContext('invalidateShare()', context);
assert.equal(state.shareDraft, null);
assert.equal(field('share-review').hidden, true);
console.log('Share draft checks passed: source pin, bounded content, stale source, no automatic send.');
