const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '..', 'static', 'app.js'), 'utf8');
const files = ['one.md', 'two.md', 'three.md'].map(name => ({name, size:4, importable:true}));
const digest = 'a'.repeat(64);
const success = (index) => ({ok:true, json:async()=>({document_id:`doc-${index}`, sha256:digest, cards:1, source_bytes:4})});
const truncated = () => ({ok:true, json:async()=>{throw new SyntaxError('truncated body');}});
const refusal = () => ({ok:false, json:async()=>({error:'Invalid source metadata'})});

function harness(responses) {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      textContent:'', disabled:false, files:[], children:[],
      append(child){ this.children.push(child); }
    });
    return elements.get(id);
  }
  let confirmations = 0;
  const posts = [];
  const context = vm.createContext({
    navigator:{userAgent:'Edge'}, URLSearchParams,
    document:{getElementById:element, createElement(tag){return {tag};}},
    window:{confirm(){confirmations++; return true;}},
    fetch:async (url, options) => {
      posts.push({url, options});
      const response = responses[posts.length - 1];
      if (response instanceof Error) throw response;
      return response;
    },
  });
  vm.runInContext(script.replace(/\binit\(\);\s*$/, ''), context);
  vm.runInContext('refreshStatus=async()=>{throw new TypeError("Failed to fetch")}', context);
  element('folder-files').files = files;
  context.items = files;
  vm.runInContext('state.inbox=items', context);
  return {context, element, posts, get confirmations(){return confirmations;}};
}

async function run(kind, responses) {
  const h = harness(responses);
  await vm.runInContext(kind === 'picker' ? 'importChosenFolder()' : 'importInboxAll()', h.context);
  return {summary:h.element(kind === 'picker' ? 'folder-summary' : 'inbox-summary').textContent,
    posts:h.posts, confirmations:h.confirmations};
}

(async () => {
  for (const kind of ['picker', 'inbox']) {
    const lostBody = await run(kind, [success(1), truncated(), success(3)]);
    assert.match(lostBody.summary, /1 imported, 0 skipped, 0 failed, 1 unconfirmed, 1 unattempted/);
    assert.match(lostBody.summary, /Check the library before retrying/);
    assert.match(lostBody.summary, /Library refresh failed/);
    assert.equal(lostBody.posts.length, 2, `${kind}: no third POST after incomplete 200 body`);

    const noHeaders = await run(kind, [success(1), new TypeError('Failed to fetch'), success(3)]);
    assert.match(noHeaders.summary, /1 imported, 0 skipped, 0 failed, 1 unconfirmed, 1 unattempted/);
    assert.equal(noHeaders.posts.length, 2, `${kind}: no third POST after fetch rejection`);

    const denied = await run(kind, [success(1), refusal(), success(3)]);
    assert.match(denied.summary, /2 imported, 0 skipped, 1 failed, 0 unconfirmed, 0 unattempted/);
    assert.equal(denied.posts.length, 3, `${kind}: complete server refusal is a failure, not an uncertain write`);
  }

  const large = harness([]);
  large.context.big = Array.from({length:42}, (_, i) => ({name:`f${i}.pdf`, size:25*1024*1024, importable:true}));
  vm.runInContext('state.inbox=big', large.context);
  await vm.runInContext('importInboxAll()', large.context);
  assert.match(large.element('inbox-summary').textContent, /exceeds 300 importable files or 1 GB listed bytes/);
  assert.equal(large.confirmations, 0);
  assert.equal(large.posts.length, 0);
  console.log('Folder/import acknowledgement, refusal and size preflight checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
