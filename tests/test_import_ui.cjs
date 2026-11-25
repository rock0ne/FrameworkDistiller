const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const script = fs.readFileSync(path.join(root, 'static', 'app.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'static', 'index.html'), 'utf8');
assert.match(script, /\binit\(\);\s*$/);
const context = vm.createContext({
  navigator:{userAgent:'Edge'},
  document:{createElement(tag){return {tag};}},
});
vm.runInContext(script.replace(/\binit\(\);\s*$/, ''), context);
const candidates = vm.runInContext('folderCandidates', context);
const pick = candidates([
  {name:'chapter.pdf',size:100},
  {name:'notes.MD',size:200},
  {name:'secret.key',size:100},
  {name:'too-big.pdf',size:25 * 1024 * 1024 + 1},
  {name:'empty.txt',size:0},
]);
assert.equal(pick.accepted.length, 2);
assert.equal(pick.skipped, 3);
assert.equal(pick.totalBytes, 300);
assert.match(html, /id="folder-files"[^>]*webkitdirectory/);
assert.match(html, /id="import-inbox-all"/);
assert.match(html, /id="change-inbox"/);
assert.match(html, /id="import-shortcut"[^>]*type="button"/);
assert.doesNotMatch(html, /href="#add-document"/);
const stopped = vm.runInContext('localNetworkError(new TypeError("Failed to fetch")).message', context);
assert.match(stopped, /local study service has stopped/i);
assert.doesNotMatch(stopped, /Android/);
const container = {children:[],append(child){this.children.push(child);}};
context.recoveryContainer = container;
vm.runInContext('addLauncherRecovery(recoveryContainer, "The local study service has stopped")', context);
assert.equal(container.children[0].href, 'frameworkdistiller://open');
console.log('Import UI negatives and folder filtering passed');
