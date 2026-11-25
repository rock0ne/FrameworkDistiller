// The offline PWA shell must never cache or intercept study/API traffic.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../static/sw.js', import.meta.url), 'utf8');
const handlers = {};
let added = [];
let fallbackCount = 0;
const offline = {kind:'offline-launcher'};
const context = vm.createContext({
  URL, Promise,
  self: {
    location:{origin:'http://127.0.0.1:8776'},
    addEventListener:(name, handler) => { handlers[name] = handler; },
    skipWaiting() {}, clients:{claim() {}}
  },
  caches: {
    open:async () => ({addAll:async (paths) => { added = Array.from(paths); }}),
    match:async (path) => { fallbackCount++; assert.equal(path, '/offline.html'); return offline; },
    keys:async () => [], delete:async () => true
  },
  fetch:async () => { throw new Error('local service closed'); }
});
vm.runInContext(source, context);
let installed;
handlers.install({waitUntil:(promise) => { installed = promise; }});
await installed;
assert.deepEqual(added, ['/offline.html','/style.css','/icon-192.png']);

let response;
handlers.fetch({request:{url:'http://127.0.0.1:8776/', mode:'navigate'},
  respondWith:(promise) => { response = promise; }});
assert.equal(await response, offline);
assert.equal(fallbackCount, 1);

for (const [path, mode] of [['/api/cards','navigate'],['/session','navigate'],
                            ['/','cors'],['/app.js','navigate']]) {
  let intercepted = false;
  handlers.fetch({request:{url:`http://127.0.0.1:8776${path}`,mode},
    respondWith:() => { intercepted = true; }});
  assert.equal(intercepted, false, `${path} must not be intercepted`);
}
assert.equal(fallbackCount, 1);
console.log('PWA offline checks passed: only root navigation falls back; no API caching.');
