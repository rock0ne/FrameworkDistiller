const $ = (id) => document.getElementById(id);
const state = { allCards: [], shown: [], documents: [], official: [], themes: [], ai: null,
  structure: null, page: null, study: null, preview: null, studyEpoch: 0, shareDraft: null };

function invalidateShare() {
  state.shareDraft = null;
  $('share-review').hidden = true;
  $('share-status').textContent = '';
}

function emailDraft() {
  const thought = $('share-thought').value.trim();
  const snippet = $('share-snippet').value.trim();
  if (!thought && !snippet) throw new Error('Write a thought or choose a source snippet first.');
  if (thought.length > 500 || snippet.length > 900) throw new Error('Keep the thought under 500 and the snippet under 900 characters.');
  if (snippet && (!state.page || !state.page.source_text.includes(snippet))) {
    throw new Error('The snippet must be an exact selection from the open reading page.');
  }
  const doc = state.structure?.document;
  const title = (doc?.title || 'Framework Distiller study thought')
    .replace(/[\x00-\x1f\x7f]/g, ' ').replace(/\s+/g, ' ').trim();
  const subject = `Study note: ${title}`.slice(0, 120);
  const source = state.page && doc
    ? `Source: ${title}\n${state.page.source_ref}\nPinned SHA-256: ${doc.sha256}\n`
    : 'No source page attached.\n';
  const body = `${source}\n${thought ? `My thought:\n${thought}\n\n` : ''}${snippet ? `Source excerpt (short selection; check the pinned original):\n${snippet}\n\n` : ''}Shared for discussion, not an authoritative quotation or audit conclusion.`;
  const url = `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
  if (url.length > 3500) throw new Error('The email draft is too long for a reliable mail link; shorten the text.');
  return {subject, body, url, fingerprint:JSON.stringify([doc?.id, state.page?.page_no, thought, snippet])};
}

function invalidateStudy() {
  state.studyEpoch += 1;
  state.preview = state.study = null;
  $('ai-preview').hidden = true;
  $('study-result').hidden = true;
  $('study-exports').hidden = true;
  $('study-message').textContent = '';
}

function studyInputFingerprint(action) {
  return JSON.stringify({action, documentId:$('document-select').value,
    chapter:$('chapter-select').value, page:$('page-select').value,
    lens:$('study-lens').value, mode:$('study-mode').value,
    provider:$('ai-provider').value, question:$('ask-question').value.trim(),
    askScope:$('ask-scope').value, showFront:$('show-front').checked});
}

function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
}
function say(message, isError = false) {
  $('message').textContent = message;
  $('message').style.color = isError ? '#ffb6aa' : '';
  if (isError) addLauncherRecovery($('message'), message);
}
function sourceSay(message, isError = false) {
  say(message, isError);
  $('source-message').textContent = message;
  $('source-message').style.color = isError ? '#ffb6aa' : '';
  if (isError) addLauncherRecovery($('source-message'), message);
}
function addLauncherRecovery(container, message) {
  if (!/local study service has stopped/i.test(message)) return;
  const launcher = node('a', '', ' Open the Windows launcher');
  launcher.href = 'frameworkdistiller://open';
  container.append(launcher);
}
function localNetworkError(error) {
  if (error instanceof TypeError && /fetch|network/i.test(error.message)) {
    return new Error(navigator.userAgent.includes('FrameworkDistillerAndroid')
      ? 'The local study service is unavailable. Close and reopen Framework Distiller, then retry.'
      : 'The local study service has stopped. Reopen Framework Distiller from Start or its taskbar launcher, then retry here. An Edge app window alone cannot run the service.');
  }
  return error;
}
async function localFetch(path, options) {
  try { return await fetch(path, options); }
  catch (error) { throw localNetworkError(error); }
}
async function api(path, body) {
  const options = body === undefined ? {credentials:'same-origin'} : {
    method: 'POST', headers: {'Content-Type':'application/json', 'X-Local-Client':'FrameworkDistiller'},
    body: JSON.stringify(body), credentials:'same-origin'
  };
  const response = await localFetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

class UnconfirmedWrite extends Error {
  constructor() {
    super('Write acknowledgement incomplete; this file may have imported. Check the library before retrying. If the local service stopped, reopen the Windows launcher or Android app.');
    this.name = 'UnconfirmedWrite';
  }
}

async function acknowledgedImport(path, options, validResult) {
  let response;
  try { response = await fetch(path, options); }
  catch (_error) { throw new UnconfirmedWrite(); }
  let result;
  try { result = await response.json(); }
  catch (_error) { throw new UnconfirmedWrite(); }
  if (!response.ok) {
    // A complete, well-formed server refusal is a confirmed failure.
    if (result && typeof result.error === 'string' && result.error) throw new Error(result.error);
    throw new UnconfirmedWrite();
  }
  if (!validResult(result)) throw new UnconfirmedWrite();
  return result;
}

function validImportResult(result) {
  return !!result && typeof result.document_id === 'string' && result.document_id.length > 0
    && typeof result.sha256 === 'string' && /^[a-f0-9]{64}$/i.test(result.sha256)
    && Number.isSafeInteger(result.cards) && result.cards >= 0;
}

const DOCUMENT_EXTENSION = /\.(pdf|epub|docx|odt|pptx|rtf|md|markdown|txt|text|html|htm)$/i;
const MAX_FOLDER_FILES = 300;
const MAX_FOLDER_BYTES = 1024 * 1024 * 1024;
function folderCandidates(files) {
  const all = Array.from(files);
  const accepted = all.filter(file => DOCUMENT_EXTENSION.test(file.name) && file.size > 0 && file.size <= 25 * 1024 * 1024);
  return {all, accepted, skipped:all.length - accepted.length,
    totalBytes:accepted.reduce((sum, file) => sum + file.size, 0)};
}
async function uploadDocument(file, meta) {
  const query = new URLSearchParams({...meta, name:file.name});
  return acknowledgedImport(`/api/ingest/upload?${query}`, {
    method:'POST', headers:{'Content-Type':'application/octet-stream',
      'X-Local-Client':'FrameworkDistiller'}, credentials:'same-origin', body:file
  }, validImportResult);
}
async function importInboxDocument(name, remaining) {
  return acknowledgedImport('/api/ingest/dropin', {
    method:'POST', headers:{'Content-Type':'application/json', 'X-Local-Client':'FrameworkDistiller'},
    credentials:'same-origin', body:JSON.stringify({name, max_batch_bytes:remaining})
  }, result => validImportResult(result)
    && Number.isSafeInteger(result.source_bytes)
    && result.source_bytes > 0 && result.source_bytes <= remaining);
}

async function startLocalSession() {
  const launchToken = new URLSearchParams(window.location.hash.slice(1)).get('launch');
  if (!launchToken) return; // Reloads use the existing HttpOnly session cookie.
  window.history.replaceState(null, '', window.location.pathname + window.location.search);
  const response = await fetch('/session', {
    method:'POST', credentials:'same-origin',
    headers:{'X-Local-Client':'FrameworkDistiller', 'X-Session-Token':launchToken}
  });
  if (!response.ok) throw new Error('Local session could not start. Reopen the desktop or Android app.');
}
function params() {
  const value = new URLSearchParams();
  for (const [field, key] of [['document-select','document_id'],['theme','theme'],['depth','depth'],['state','state'],['search','q']]) {
    const entry = $(field).value.trim();
    if (entry) value.set(key, entry);
  }
  return value;
}

async function refreshAiStatus() {
  state.ai = await api('/api/ai/status');
  const provider = $('ai-provider').value;
  const hasKey = provider && state.ai.configured.includes(provider);
  $('activity-badge').textContent = hasKey
    ? `● AI ready · ${{openai:'OpenAI',anthropic:'Claude',gemini:'Gemini'}[provider]} (BYOK)`
    : state.ai.configured.length ? '● AI key in memory · no provider selected' : '● Local study · AI off';
  $('ai-settings').querySelector('summary').textContent = hasKey
    ? `Optional AI study assistant · ${provider} ready`
    : state.ai.configured.length ? 'Optional AI study assistant · key in memory' : 'Optional AI study assistant · off';
  $('ai-status').textContent = hasKey
    ? `${provider} key ready for this app session · ${state.ai.models[provider]}. Nothing sent until you explicitly test or send a study request.`
    : 'AI is off for this provider. No key is included or saved.';
  $('ai-test').disabled = !hasKey;
}

function sourceMeta(kind, file) {
  const meta = {
    title: $('source-title').value.trim() || (kind === 'file' && file ? file.name.replace(/\.[^.]+$/, '') : ''),
    source_url: $('source-url').value.trim(),
    version: $('source-version').value.trim() || (kind === 'file' ? 'Local copy' : ''),
    publication_date: $('source-date').value.trim() || (kind === 'file' ? 'Unknown' : ''),
    sha256: $('source-sha').value.trim()
  };
  if (!meta.title || !meta.version || !meta.publication_date || (kind === 'link' && !meta.source_url)) {
    throw new Error('For a link pull, enter title, official URL, version and publication date.');
  }
  if (meta.source_url && !/^https:\/\//i.test(meta.source_url)) throw new Error('Use an official HTTPS source URL.');
  if (meta.sha256 && !/^[a-f0-9]{64}$/i.test(meta.sha256)) {
    throw new Error('Expected SHA-256 needs exactly 64 hex characters.');
  }
  return meta;
}

async function importNewSource(kind) {
  const uploadButton = $('upload-file'), linkButton = $('pull-link');
  try {
    const file = $('source-file').files[0];
    if (kind === 'file' && !file) throw new Error('Choose a supported document first.');
    const meta = sourceMeta(kind, file);
    if (kind === 'file' && file.size > 25 * 1024 * 1024) throw new Error('File exceeds 25 MB.');
    uploadButton.disabled = linkButton.disabled = true;
    sourceSay(kind === 'file' ? `Hashing and importing ${file.name}…` : 'Pulling the official public link…');
    let result;
    if (kind === 'file') {
      result = await uploadDocument(file, meta);
    } else result = await api('/api/ingest/url', meta);
    sourceSay(`${result.cards} cards imported; SHA-256 ${result.sha256.slice(0,16)}… · ${result.provenance.replaceAll('_',' ')}.`);
    await refreshStatus(); await openDocument(result.document_id); await refreshCards();
  } catch (error) { sourceSay(error.message, true); }
  finally { uploadButton.disabled = linkButton.disabled = false; }
}

async function importChosenFolder() {
  const choice = folderCandidates($('folder-files').files);
  if (!choice.accepted.length) { $('folder-summary').textContent = 'Choose a folder with supported files under 25 MB.'; return; }
  if (choice.accepted.length > MAX_FOLDER_FILES || choice.totalBytes > MAX_FOLDER_BYTES) {
    $('folder-summary').textContent = 'This folder is too large for one import. Choose a smaller folder (maximum 300 supported files and 1 GB per batch).';
    return;
  }
  if (!window.confirm(`Import ${choice.accepted.length} supported local file(s)? ${choice.skipped} other file(s) will be skipped. Original files stay in place; no file is sent to an AI provider.`)) return;
  const button = $('import-folder'); button.disabled = true;
  let imported = 0, attempted = 0, unconfirmed = 0; const failures = [];
  let recovery = '';
  try {
    for (const [index, file] of choice.accepted.entries()) {
      $('folder-summary').textContent = `Importing ${index + 1} of ${choice.accepted.length}: ${file.name}`;
      try {
        await uploadDocument(file, {title:file.name.replace(/\.[^.]+$/, '').slice(0, 160),
          source_url:'', version:'Local copy', publication_date:'Unknown', sha256:''});
        imported += 1;
      } catch (error) {
        if (error instanceof UnconfirmedWrite) { unconfirmed += 1; recovery = error.message; }
        else failures.push(`${file.name}: ${error.message}`);
      }
      attempted += 1;
      if (recovery) break;
    }
  } finally {
    let refreshError = '';
    try { await refreshStatus(); await refreshCards(); }
    catch (error) { refreshError = localNetworkError(error).message; }
    showBatchResult('folder-summary', {imported, skipped:choice.skipped, failed:failures.length,
      unconfirmed, unattempted:choice.accepted.length - attempted, firstError:failures[0], recovery, refreshError});
    button.disabled = false;
  }
}

function showBatchResult(id, result) {
  const message = `${result.imported} imported, ${result.skipped} skipped, ${result.failed} failed, `
    + `${result.unconfirmed} unconfirmed, ${result.unattempted} unattempted.`
    + (result.firstError ? ` First error: ${result.firstError}` : '')
    + (result.recovery ? ` ${result.recovery}` : '')
    + (result.refreshError ? ` Library refresh failed: ${result.refreshError}` : '');
  const target = $(id); target.textContent = message;
  addLauncherRecovery(target, message);
}

async function importInboxAll() {
  const files = (state.inbox || []).filter(file => file.importable);
  if (!files.length) return;
  const listedBytes = files.reduce((sum, file) => sum + file.size, 0);
  const blocked = (state.inbox || []).filter(file => !file.importable).length;
  if (files.length > MAX_FOLDER_FILES || listedBytes > MAX_FOLDER_BYTES) {
    $('inbox-summary').textContent = 'This inbox exceeds 300 importable files or 1 GB listed bytes. Choose a smaller inbox folder for one batch.';
    return;
  }
  if (!window.confirm(`Import all ${files.length} supported files listed from this inbox? Originals stay in place. Optional sidecars will be checked per file.`)) return;
  const button = $('import-inbox-all'); button.disabled = true;
  let imported = 0, attempted = 0, unconfirmed = 0, consumed = 0; const failures = [];
  let recovery = '';
  try {
    for (const [index, file] of files.entries()) {
      const remaining = MAX_FOLDER_BYTES - consumed;
      if (remaining <= 0) break;
      $('inbox-summary').textContent = `Importing ${index + 1} of ${files.length}: ${file.name}`;
      try {
        const result = await importInboxDocument(file.name, remaining);
        imported += 1; consumed += result.source_bytes;
      }
      catch (error) {
        if (error instanceof UnconfirmedWrite) { unconfirmed += 1; recovery = error.message; }
        else {
          failures.push(`${file.name}: ${error.message}`);
          // A failed parse may already have read the file. Charge its full per-file
          // bound rather than infer a smaller amount from stale directory metadata.
          consumed += Math.min(25 * 1024 * 1024, remaining);
        }
      }
      attempted += 1;
      if (recovery) break;
    }
  } finally {
    let refreshError = '';
    try { await refreshStatus(); await refreshCards(); }
    catch (error) { refreshError = localNetworkError(error).message; }
    showBatchResult('inbox-summary', {imported, skipped:blocked,
      failed:failures.length, unconfirmed, unattempted:files.length - attempted,
      firstError:failures[0], recovery, refreshError});
    button.disabled = false;
  }
}

async function refreshStatus() {
  const info = await api('/api/status');
  state.documents = info.documents;
  state.official = info.official;
  state.themes = info.themes;
  state.storage = info.storage;
  state.inbox = info.inbox;
  const documentSelect = $('document-select');
  const chosen = documentSelect.value;
  documentSelect.replaceChildren(node('option', '', 'Choose a document'));
  documentSelect.firstChild.value = '';
  for (const doc of info.documents) {
    const option = node('option', '', `${doc.title} · ${doc.version}`);
    option.value = doc.id;
    documentSelect.append(option);
  }
  documentSelect.value = info.documents.some(doc => doc.id === chosen) ? chosen : '';
  $('library-path').textContent = info.storage.data_root;
  $('desktop-storage-controls').hidden = !info.storage.editable;
  $('temporary-storage-info').hidden = info.storage.editable || info.storage.platform === 'android';
  $('android-storage-info').hidden = info.storage.platform !== 'android';
  $('storage-path').value = info.storage.data_root;
  $('inbox-setting').value = info.storage.inbox_custom ? info.storage.inbox_root : '';
  $('inbox-path').textContent = info.inbox_path;
  $('change-inbox').hidden = !info.storage.editable;
  $('folder-picker-controls').hidden = info.storage.platform === 'android';
  $('android-folder-controls').hidden = info.storage.platform !== 'android';
  const theme = $('theme');
  const selected = theme.value;
  theme.replaceChildren(node('option', '', 'All themes'));
  theme.firstChild.value = '';
  for (const value of info.themes) {
    const option = node('option', '', value);
    option.value = value;
    theme.append(option);
  }
  theme.value = selected;
  drawSources(info);
}

function drawSources(info) {
  const official = $('official-sources'); official.replaceChildren();
  for (const source of info.official) {
    const row = node('div','source-row');
    const title = node('span','',source.title);
    const button = node('button','', 'Import');
    button.type = 'button';
    button.addEventListener('click', async () => {
      button.disabled = true; button.textContent = 'Importing…';
      say(`Downloading and pinning ${source.title}…`);
      try {
        const result = await api('/api/ingest/official', {source_id: source.source_id});
        say(`${source.title}: ${result.cards} cards; SHA-256 ${result.sha256.slice(0,12)}…`);
        await refreshStatus(); await openDocument(result.document_id); await refreshCards();
      } catch (error) { say(error.message, true); }
      finally { button.disabled = false; button.textContent = 'Import'; }
    });
    row.append(title, button); official.append(row);
  }
  const inbox = $('inbox-files'); inbox.replaceChildren();
  const blocked = info.inbox.filter(file => !file.importable).length;
  $('inbox-summary').textContent = `${info.inbox.length} supported file${info.inbox.length===1?'':'s'} found${blocked ? ` · ${blocked} over the 25 MB per-file limit` : ''}. Nothing imports until you choose it.`;
  $('import-inbox-all').disabled = !info.inbox.some(file => file.importable);
  if (!info.inbox.length) inbox.append(node('p','small','No supported files in this folder. Press Refresh after adding files, or check the selected folder above.'));
  for (const file of info.inbox) {
    const row = node('div','source-row');
    const name = file.name;
    const label = file.has_sidecar ? 'metadata sidecar' : 'local copy';
    row.append(node('span','',`${name} · ${(file.size/1048576).toFixed(1)} MB · ${label}${file.importable?'':' · over limit'}`));
    const button = node('button','', 'Import');
    button.type = 'button';
    button.disabled = !file.importable;
    button.addEventListener('click', async () => {
      button.disabled = true; say(`Checking local file ${name}…`);
      try {
        const result = await api('/api/ingest/dropin', {name});
        say(`Imported ${result.cards} draft cards. Hash ${result.sha256.slice(0,12)}…; URL not independently verified.`);
        await refreshStatus(); await openDocument(result.document_id); await refreshCards();
      } catch (error) { say(error.message, true); }
      finally { button.disabled = false; }
    });
    row.append(button); inbox.append(row);
  }
  const library = $('library'); library.replaceChildren();
  if (!info.documents.length) library.append(node('p','small','Import a source to start.'));
  for (const doc of info.documents) {
    const row = node('div','library-row');
    const label = node('span','',`${doc.title} · ${doc.card_count} cards`);
    label.title = `${doc.version} · ${doc.sha256}`;
    row.append(label); library.append(row);
  }
}

function visibleChapters() {
  if (!state.structure) return [];
  const chapters = state.structure.chapters;
  const visible = $('show-front').checked ? chapters : chapters.filter(chapter => !chapter.front_matter);
  return visible.length ? visible : chapters;
}

function visiblePages(chapter) {
  const pages = $('show-front').checked ? chapter.pages : chapter.pages.filter(page => !page.front_matter);
  return pages.length ? pages : chapter.pages;
}

function jumpToPage(pageNo) {
  const chapter = state.structure?.chapters.find(item => item.pages.some(page => page.page_no === pageNo));
  if (!chapter) return;
  invalidateStudy();
  if (chapter.front_matter && !$('show-front').checked) $('show-front').checked = true;
  fillChapterOptions(chapter.chapter_no);
  fillPageOptions(pageNo);
  loadPage().catch(error => say(error.message, true));
  $('reader-panel').scrollIntoView({behavior:'smooth', block:'start'});
}

function drawOutline() {
  const list = $('outline-list'); list.replaceChildren();
  if (!state.structure) {
    list.append(node('p','small','Choose a document to see its source-order structure.'));
    return;
  }
  for (const chapter of visibleChapters()) {
    const entry = node('details','outline-entry');
    entry.append(node('summary','',`${chapter.chapter_no}. ${chapter.title} · ${chapter.pages.length} page/segment${chapter.pages.length===1?'':'s'}`));
    const open = node('button','','Read from start'); open.type = 'button';
    open.addEventListener('click', () => jumpToPage(chapter.pages[0].page_no));
    entry.append(open);
    entry.addEventListener('toggle', () => {
      if (!entry.open || entry.dataset.pagesBuilt || chapter.pages.length <= 1) return;
      const pages = node('ul','outline-pages');
      for (const page of chapter.pages) {
        const row = node('li');
        const button = node('button','',page.label); button.type = 'button';
        button.addEventListener('click', () => jumpToPage(page.page_no));
        row.append(button); pages.append(row);
      }
      entry.append(pages); entry.dataset.pagesBuilt = 'true';
    });
    list.append(entry);
  }
}

function fillChapterOptions(preferred) {
  const selector = $('chapter-select');
  selector.replaceChildren();
  const chapters = visibleChapters();
  for (const chapter of chapters) {
    const option = node('option', '', `${chapter.chapter_no}. ${chapter.title}`);
    option.value = String(chapter.chapter_no);
    selector.append(option);
  }
  selector.value = chapters.some(chapter => String(chapter.chapter_no) === String(preferred))
    ? String(preferred) : (chapters[0] ? String(chapters[0].chapter_no) : '');
  fillPageOptions();
}

function fillPageOptions(preferred) {
  const selector = $('page-select');
  selector.replaceChildren();
  const chapter = visibleChapters().find(item => String(item.chapter_no) === $('chapter-select').value);
  const pages = chapter ? visiblePages(chapter) : [];
  for (const page of pages) {
    const option = node('option', '', page.label);
    option.value = String(page.page_no);
    selector.append(option);
  }
  selector.value = pages.some(page => String(page.page_no) === String(preferred))
    ? String(preferred) : (pages[0] ? String(pages[0].page_no) : '');
}

async function openDocument(documentId) {
  $('document-select').value = documentId || '';
  invalidateStudy();
  invalidateShare();
  state.structure = state.page = null;
  $('reader-text').textContent = '';
  $('reader-challenge-answer').value = '';
  if (!documentId) {
    $('reader-title').textContent = 'No document selected';
    $('reader-status').textContent = 'Choose a document to start reading in order.';
    $('chapter-select').replaceChildren(); $('page-select').replaceChildren();
    drawOutline();
    $('prev-page').disabled = $('next-page').disabled = true;
    return;
  }
  $('reader-status').textContent = 'Checking pinned source and building reading order…';
  try {
    const data = await api(`/api/reader?document_id=${encodeURIComponent(documentId)}`);
    if ($('document-select').value !== documentId) return;
    state.structure = data;
    fillChapterOptions();
    drawOutline();
    $('reader-status').textContent = `${data.structure_kind.replaceAll('_',' ')} · ${data.chapters.length} chapter/section groups · ${data.omitted_blank_pages} blank pages omitted. Extracted text is not a visual facsimile.`;
    await loadPage();
  } catch (error) {
    $('reader-status').textContent = error.message;
    $('reader-title').textContent = 'This source could not be opened';
    say(error.message, true);
  }
}

async function loadPage() {
  const documentId = $('document-select').value;
  const pageNo = $('page-select').value;
  if (!state.structure || !pageNo) return;
  state.page = null;
  invalidateShare();
  const lens = $('study-lens').value;
  const requested = `${documentId}:${pageNo}:${lens}`;
  const query = new URLSearchParams({document_id:documentId, page_no:pageNo, lens});
  const page = await api(`/api/reader/page?${query}`);
  if (`${$('document-select').value}:${$('page-select').value}:${$('study-lens').value}` !== requested) return;
  state.page = page;
  $('reader-title').textContent = page.label;
  $('reader-meta').textContent = `${page.source_ref} · ${state.structure.document.title} · source SHA-256 ${state.structure.document.sha256.slice(0,16)}…${page.front_matter ? ' · front matter' : ''}`;
  $('reader-text').textContent = page.source_text;
  $('reader-challenge-prompt').textContent = page.challenge;
  $('reader-challenge-answer').value = '';
  const sequence = visibleChapters().flatMap(visiblePages);
  const index = sequence.findIndex(item => item.page_no === page.page_no);
  $('prev-page').disabled = index <= 0;
  $('next-page').disabled = index < 0 || index >= sequence.length - 1;
}

function stepPage(delta) {
  const sequence = visibleChapters().flatMap(visiblePages);
  const index = sequence.findIndex(item => String(item.page_no) === $('page-select').value);
  const next = sequence[index + delta];
  if (!next) return;
  invalidateStudy();
  if (String(next.chapter_no) !== $('chapter-select').value) {
    $('chapter-select').value = String(next.chapter_no);
    fillPageOptions(next.page_no);
  } else $('page-select').value = String(next.page_no);
  loadPage().catch(error => say(error.message, true));
  $('reader-panel').scrollIntoView({behavior:'smooth', block:'start'});
}

function studyRequest(action) {
  if (!state.structure || !state.page) throw new Error('Choose and open a document first.');
  if (Number($('page-select').value) !== state.page.page_no) throw new Error('Wait for the selected page to open.');
  const custom = action.startsWith('ask_');
  const question = custom ? $('ask-question').value.trim() : '';
  if (custom && (!question || question.length > 500)) throw new Error('Enter a question of 1–500 characters.');
  return {document_id:$('document-select').value, action, lens:$('study-lens').value,
    chapter_no:Number($('chapter-select').value), page_no:Number($('page-select').value),
    mode:custom ? 'ai' : $('study-mode').value, provider:$('ai-provider').value, question};
}

function appendMap(parent, item) {
  const li = node('li');
  if (Number.isInteger(item.page_no) && state.structure) {
    const button = node('button','map-link',item.label); button.type = 'button';
    button.addEventListener('click', () => jumpToPage(item.page_no));
    li.append(button);
  } else li.append(node('span','',item.label));
  if (item.source_ref) li.append(node('small','study-ref',` · ${item.source_ref}${state.study?.body?.coverage?.excerpt_only_refs?.includes(item.source_ref) ? ' · excerpt only' : ''}`));
  if (item.children?.length) {
    const children = node('ul');
    for (const child of item.children) appendMap(children, child);
    li.append(children);
  }
  parent.append(li);
}

function drawStudy(item) {
  state.study = item;
  const result = $('study-result');
  result.replaceChildren(node('h3','',item.body.title));
  result.append(node('p','note',`${item.body.disclosure} · ${item.provider}/${item.model} · ${item.created_at}${item.cached ? ' · local cache' : ''}`));
  if (item.body.question) result.append(node('p','study-question',`Question: ${item.body.question}`));
  if (item.body.items?.length) {
    const list = node('ol');
    for (const point of item.body.items) {
      const li = node('li','',point.text);
      if (point.source_ref) li.append(node('small','study-ref',` · ${point.source_ref}${item.body.coverage?.excerpt_only_refs?.includes(point.source_ref) ? ' · excerpt only' : ''}`));
      list.append(li);
    }
    result.append(list);
  }
  if (item.body.map) { const list=node('ul','mind-map'); appendMap(list,item.body.map); result.append(list); }
  result.hidden = false;
  $('study-exports').hidden = false;
  $('export-study-svg').hidden = !item.body.map;
  $('study-message').textContent = item.cached ? 'Loaded this result from your local cache.' : 'Study result saved locally with its source pin and scope.';
}

async function runStudy(action) {
  try {
    invalidateStudy();
    const request = studyRequest(action);
    const epoch = state.studyEpoch;
    const fingerprint = studyInputFingerprint(action);
    $('study-result').hidden = true;
    $('study-exports').hidden = true;
    if (request.mode === 'ai') {
      if (!request.provider || !state.ai?.configured.includes(request.provider)) {
        $('ai-settings').open = true;
        throw new Error('Choose a provider and use your own key in AI Settings first.');
      }
      const exact = await api('/api/study/preview', request);
      if (epoch !== state.studyEpoch || fingerprint !== studyInputFingerprint(action)) return;
      state.preview = {request, exact, fingerprint};
      $('ai-preview-meta').textContent = `${exact.provider} / ${exact.model} · ${exact.characters} total preview characters. ${exact.coverage_disclosure} Review the exact text before sending.`;
      $('ai-preview-text').textContent = exact.text;
      $('ai-preview-instruction').textContent = exact.instruction;
      $('ai-preview').hidden = false;
      $('ai-preview').scrollIntoView({behavior:'smooth', block:'nearest'});
      return;
    }
    $('ai-preview').hidden = true;
    $('study-message').textContent = 'Building a source-derived offline aid…';
    const result = await api('/api/study/generate', request);
    if (epoch === state.studyEpoch && fingerprint === studyInputFingerprint(action)) drawStudy(result);
  } catch (error) { $('study-message').textContent = error.message; }
}

async function sendStudyPreview() {
  if (!state.preview) return;
  const {request, exact, fingerprint} = state.preview;
  if (fingerprint !== studyInputFingerprint(request.action)) {
    invalidateStudy();
    $('study-message').textContent = 'Study inputs changed. Review a new exact preview before sending.';
    return;
  }
  state.preview = null;
  $('ai-preview').hidden = true;
  const button = $('ai-preview-send'); button.disabled = true;
  $('study-message').textContent = 'Waiting for provider…';
  try {
    const result = await api('/api/study/generate', {...request, approval_hash:exact.approval_hash});
    if (fingerprint === studyInputFingerprint(request.action)) drawStudy(result);
  } catch (error) { $('study-message').textContent = error.message; }
  finally { button.disabled = false; }
}

async function refreshCards() {
  const [all, filtered] = await Promise.all([
    api('/api/cards'), api(`/api/cards?${params()}`)
  ]);
  state.allCards = all.cards;
  state.filteredCount = filtered.count;
  state.shown = filtered.cards;
  $('shown-count').textContent = String(filtered.count);
  $('shown-label').textContent = 'source cards · optional';
  $('empty').hidden = state.shown.length !== 0;
  if (state.shown.length === 0 && state.documents.length) {
    $('empty').querySelector('h2').textContent = 'No cards match these filters';
    $('empty').querySelector('p').textContent = 'Try All depths or Clear filters. Your imported documents are still in the pinned library.';
  }
  drawCards();
}

function part(number, title, value, isLink = false) {
  const section = node('section','card-part');
  section.append(node('h3','',`${number}. ${title}`));
  if (isLink) {
    const anchor = node('a','card-link',value.label);
    anchor.href = value.href;
    anchor.target = navigator.userAgent.includes('FrameworkDistillerAndroid') ? '_self' : '_blank';
    anchor.rel = 'noopener noreferrer';
    section.append(anchor);
  } else section.append(node('p','',value));
  return section;
}

function action(label, callback, className = '') {
  const button = node('button',className,label);
  button.type = 'button'; button.addEventListener('click',callback);
  return button;
}

async function updateProgress(card, next, answer) {
  try {
    await api('/api/progress', {card_id:card.id, state:next, ...(answer === undefined ? {} : {answer})});
    say(`Saved ${card.ref} as ${next}. This is self-reported study progress.`);
    await refreshCards();
  } catch (error) { say(error.message,true); }
}

function challengeBox(card) {
  const box = node('div','challenge');
  box.append(node('strong','','Challenge me — open-ended'));
  box.append(node('p','',card.challenge));
  const input = node('textarea'); input.value = card.answer || '';
  input.setAttribute('aria-label',`Your answer to ${card.ref}`);
  box.append(input);
  box.append(action('Save my answer',() => updateProgress(card, card.state, input.value)));
  box.append(node('p','note','Private note only. The app does not grade or validate your reasoning.'));
  return box;
}

function editBox(card) {
  const box = node('div','edit-box');
  box.append(node('strong','','Edit the local draft'));
  const fields = {};
  for (const [key,label] of [['core_rule','Core rule (max 2 sentences)'],['risk','Risk if ignored'],['audit_test','What a senior auditor tests'],['trap','Classic auditee trap'],['challenge','Open-ended challenge']]) {
    const wrapper = node('label','',label);
    const input = node('textarea'); input.value = card[key]; input.maxLength = 1500;
    input.setAttribute('aria-label',label); wrapper.append(input); box.append(wrapper);
    fields[key] = input;
  }
  box.append(action('Save reviewed draft', async () => {
    try {
      const values = Object.fromEntries(Object.entries(fields).map(([key,input]) => [key,input.value]));
      await api('/api/card/edit',{card_id:card.id,fields:values});
      say(`Saved your edit for ${card.ref}; still verify the primary text.`);
      await refreshCards();
    } catch (error) { say(error.message,true); }
  }));
  return box;
}

function aiBox(card) {
  const box = node('div','ai-box');
  const provider = $('ai-provider').value;
  const company = {openai:'OpenAI',anthropic:'Anthropic',gemini:'Google'}[provider];
  if (!company || !state.ai?.configured.includes(provider)) {
    box.append(node('p','note','Choose a provider and add your own API key in Optional AI study assistant first.'));
    return box;
  }
  box.append(node('strong','',`Ask ${company} · ${state.ai.models[provider]}`));
  box.append(node('p','note','Review exactly what will leave this device. Your notes, progress, answers and other documents are excluded. Do not send confidential employer or personal data without authorisation. API charges go to your provider account. AI output is unverified.'));
  const excerpt = [...card.source_text].slice(0,4000).join('');
  box.append(node('p','small',`Source excerpt to send: ${[...excerpt].length} characters${[...card.source_text].length>4000?' (first 4,000 only)':''}.`));
  box.append(node('pre','',excerpt));
  const label = node('label','ai-label','Your question to send');
  const question = node('textarea'); question.maxLength=500;
  question.value='Explain the key audit decision in this excerpt, and what evidence I would test.';
  label.append(question); box.append(label);
  const reply = node('div','ai-reply'); reply.hidden=true;
  const send = action(`Send this excerpt to ${company}`, async () => {
    try {
      send.disabled=true; send.textContent='Waiting for provider…';
      const result = await api('/api/ai/ask',{provider,card_id:card.id,question:question.value});
      reply.textContent=`${result.answer}\n\nAI interpretation · ${company} / ${result.model} · ${new Date().toLocaleString()} · verify against the pinned primary source.`;
      reply.hidden=false;
    } catch (error) { reply.textContent=error.message; reply.hidden=false; }
    finally { send.disabled=false; send.textContent=`Send this excerpt to ${company}`; }
  });
  box.append(send,reply);
  return box;
}

function drawCards() {
  const container = $('cards'); container.replaceChildren();
  for (const card of state.shown) {
    const item = node('article','card'); item.id = `card-${card.id}`;
    const head = node('header','card-head');
    const topline = node('div','card-topline');
    topline.append(node('span','',card.theme),node('span','badge',card.state));
    head.append(topline,node('h2','',`${card.ref} · ${card.title}`));
    head.append(node('div','card-meta',`${card.document_title} · ${card.version} · ${card.publication_date}`));
    item.append(head);
    const body = node('div','card-body');
    body.append(part(1,'Plain-English core rule',card.core_rule));
    body.append(part(2,'Risk if ignored · auditor interpretation',card.risk));
    body.append(part(3,'What a senior auditor tests · interpretation',card.audit_test));
    body.append(part(4,'Classic auditee trap · scenario prompt',card.trap));
    body.append(card.source_link
      ? part(5,'Source link', {label:`${card.ref} · Open claimed source`,href:card.source_link},true)
      : part(5,'Local source', `${card.ref} · No public source link supplied; check the SHA-256 and extracted text below.`));
    body.append(node('p','note',`Source SHA-256 ${card.sha256.slice(0,16)}… · ${card.provenance.replaceAll('_',' ')}`));
    item.append(body);
    const details = node('details');
    details.append(node('summary','','Inspect verbatim extracted source text'));
    details.append(node('pre','',card.source_text));
    item.append(details);
    const footer = node('div','card-footer');
    footer.append(action('Mark read',() => updateProgress(card,'read'),card.state==='read'?'active':''));
    footer.append(action('Mark mastered',() => updateProgress(card,'mastered'),card.state==='mastered'?'active':''));
    footer.append(action('Challenge me',() => { const current=item.querySelector('.challenge'); if(current) current.remove(); else item.append(challengeBox(card)); }));
    footer.append(action('Edit draft',() => { const current=item.querySelector('.edit-box'); if(current) current.remove(); else item.append(editBox(card)); }));
    footer.append(action('Ask AI about this excerpt',() => { const current=item.querySelector('.ai-box'); if(current) current.remove(); else item.append(aiBox(card)); }));
    item.append(footer);
    const related = state.allCards.filter(x => x.id!==card.id && x.theme===card.theme && x.document_id!==card.document_id).slice(0,3);
    if (related.length) {
      const panel = node('div','related');
      panel.append(node('h3','','Related theme · not equivalent requirements'));
      for (const sibling of related) {
        const link = node('a','',`${sibling.document_title}: ${sibling.ref}`);
        link.href = '#';
        link.addEventListener('click',async (event) => {
          event.preventDefault();
          $('document-select').value=sibling.document_id;
          await openDocument(sibling.document_id);
          $('theme').value=''; $('depth').value=''; $('state').value=''; $('search').value=sibling.ref;
          await refreshCards(); $('results').scrollIntoView({behavior:'smooth'});
        });
        panel.append(link);
      }
      item.append(panel);
    }
    container.append(item);
  }
}

async function init() {
  $('open-faq').addEventListener('click', () => $('faq').showModal());
  $('close-faq').addEventListener('click', () => $('faq').close());
  $('close-install-help').addEventListener('click', () => $('install-help').close());
  $('faq').addEventListener('click', event => { if (event.target === $('faq')) $('faq').close(); });
  for (const id of ['share-thought','share-snippet']) $(id).addEventListener('input', invalidateShare);
  $('share-selection').addEventListener('click', () => {
    const selection = window.getSelection();
    const source = $('reader-text');
    const anchor = selection?.anchorNode, focus = selection?.focusNode;
    const selected = selection?.toString().trim() || '';
    if (!state.page || !selected || !anchor || !focus || !source.contains(anchor) || !source.contains(focus)
        || !state.page.source_text.includes(selected)) {
      $('share-status').textContent = 'Select exact text inside the open reading page first.';
      return;
    }
    if (selected.length > 900) { $('share-status').textContent = 'Select no more than 900 characters.'; return; }
    $('share-snippet').value = selected;
    invalidateShare();
    $('share-status').textContent = 'Source selection copied into the local draft; review before opening email.';
  });
  $('share-preview').addEventListener('click', () => {
    try {
      const draft = emailDraft();
      state.shareDraft = draft;
      $('share-draft').textContent = `Subject: ${draft.subject}\n\n${draft.body}`;
      $('share-review').hidden = false;
      $('share-status').textContent = 'Only this displayed text will be handed to your email app. Choose a recipient and send there yourself.';
    } catch (error) { invalidateShare(); $('share-status').textContent = error.message; }
  });
  $('share-cancel').addEventListener('click', invalidateShare);
  $('share-open-email').addEventListener('click', () => {
    try {
      const draft = emailDraft();
      if (!state.shareDraft || draft.fingerprint !== state.shareDraft.fingerprint) {
        throw new Error('The page or draft changed. Review the exact email draft again.');
      }
      window.location.href = draft.url;
      $('share-status').textContent = 'Email app opened if one is configured. Framework Distiller did not send a message.';
    } catch (error) { invalidateShare(); $('share-status').textContent = error.message; }
  });
  const installButton = $('install-app');
  const nativeAndroid = navigator.userAgent.includes('FrameworkDistillerAndroid');
  const isStandalone = () => Boolean(window.matchMedia?.('(display-mode: standalone)').matches);
  installButton.hidden = nativeAndroid;
  installButton.textContent = isStandalone() ? 'Manage installed app' : 'Install as app';
  const showInstallHelp = () => {
    const temporaryPreview = state.storage?.platform === 'desktop' && !state.storage.editable;
    $('install-help-state').textContent = temporaryPreview
      ? 'This is a temporary preview with a fixed test library. Do not install its short-lived address; open the normal Windows launcher first.'
      : !state.storage
        ? 'The local service is unavailable. Open the Windows launcher before trying to install this page.'
        : isStandalone()
          ? 'This page is already running in an installed Edge app window.'
          : 'Edge has not offered this page an in-app install prompt. The Edge menu may still allow installation, or this app may already be installed.';
    $('install-help').showModal();
  };
  let installPrompt = null;
  window.addEventListener('beforeinstallprompt', event => {
    event.preventDefault();
    installPrompt = event;
    installButton.textContent = 'Install as app';
  });
  window.addEventListener('appinstalled', () => {
    installPrompt = null;
    installButton.textContent = 'Manage installed app';
  });
  installButton.addEventListener('click', async () => {
    if (!state.storage || (state.storage.platform === 'desktop' && !state.storage.editable)) {
      showInstallHelp(); return;
    }
    if (!installPrompt) { showInstallHelp(); return; }
    const prompt = installPrompt;
    installPrompt = null;
    try {
      await prompt.prompt();
      const choice = await prompt.userChoice;
      if (choice?.outcome === 'accepted') installButton.textContent = 'Manage installed app';
    } catch (error) { showInstallHelp(); }
  });
  try {
    await startLocalSession(); await refreshStatus(); await refreshCards(); await refreshAiStatus();
    if (state.documents.length) await openDocument(state.documents[0].id);
    else drawOutline();
    if ('serviceWorker' in navigator && !navigator.userAgent.includes('FrameworkDistillerAndroid')) {
      navigator.serviceWorker.register('/sw.js').catch(() => {});
    }
  }
  catch (error) {
    say(error.message,true);
    if (/Open Framework Distiller from its app launcher|Local app launch required/.test(error.message)) {
      const launcher = node('a', '', ' Open the Windows launcher');
      launcher.href = 'frameworkdistiller://open';
      $('message').append(launcher);
    }
  }
  $('ai-provider').addEventListener('change',() => {
    invalidateStudy();
    const provider = $('ai-provider').value;
    $('ai-model').value = provider && state.ai ? state.ai.models[provider] : '';
    refreshAiStatus().catch(e=>say(e.message,true));
  });
  $('ai-use-key').addEventListener('click',async () => {
    invalidateStudy();
    const provider=$('ai-provider').value, key=$('ai-key').value.trim(), model=$('ai-model').value.trim();
    try {
      if (!provider) throw new Error('Choose a provider first.');
      await api('/api/ai/key',{provider,key,model}); $('ai-key').value='';
      await refreshAiStatus();
    } catch(error) { $('ai-key').value=''; $('ai-status').textContent=error.message; }
  });
  $('ai-forget').addEventListener('click',async () => {
    invalidateStudy();
    const provider=$('ai-provider').value;
    try { if (!provider) throw new Error('Choose a provider first.');
      await api('/api/ai/forget',{provider}); $('ai-key').value=''; await refreshAiStatus();
      $('ai-model').value = state.ai.models[provider];
    } catch(error) { $('ai-status').textContent=error.message; }
  });
  $('ai-test').addEventListener('click', async () => {
    const provider = $('ai-provider').value;
    if (!provider || !state.ai?.configured.includes(provider)) {
      $('ai-status').textContent = 'Add a key for this provider first.'; return;
    }
    if (!window.confirm('Make one small live connection check with this provider? It may incur an API charge. No document text or notes will be sent.')) return;
    const button = $('ai-test'); button.disabled = true;
    $('ai-status').textContent = 'Testing the selected provider and model…';
    try {
      const result = await api('/api/ai/test', {provider});
      $('ai-status').textContent = `Connection succeeded: ${result.provider} / ${result.model}. This confirms only this API call, not the study-output quality.`;
    } catch (error) { $('ai-status').textContent = error.message; }
    finally { button.disabled = false; }
  });
  $('document-select').addEventListener('change', async () => {
    try { await openDocument($('document-select').value); await refreshCards(); }
    catch (error) { say(error.message, true); }
  });
  $('chapter-select').addEventListener('change', () => {
    invalidateStudy();
    invalidateShare();
    fillPageOptions(); loadPage().catch(error => say(error.message, true));
  });
  $('page-select').addEventListener('change', () => { invalidateStudy(); invalidateShare(); loadPage().catch(error => say(error.message, true)); });
  $('show-front').addEventListener('change', () => {
    invalidateStudy();
    invalidateShare();
    fillChapterOptions($('chapter-select').value);
    drawOutline();
    loadPage().catch(error => say(error.message, true));
  });
  $('study-lens').addEventListener('change', () => { invalidateStudy(); loadPage().catch(error => say(error.message, true)); });
  $('study-mode').addEventListener('change', invalidateStudy);
  $('ask-scope').addEventListener('change', invalidateStudy);
  $('ask-question').addEventListener('input', invalidateStudy);
  $('ai-model').addEventListener('input', invalidateStudy);
  $('prev-page').addEventListener('click', () => stepPage(-1));
  $('next-page').addEventListener('click', () => stepPage(1));
  for (const button of document.querySelectorAll('[data-study-action]')) {
    button.addEventListener('click', () => runStudy(button.dataset.studyAction));
  }
  $('ask-ai').addEventListener('click', () => runStudy(`ask_${$('ask-scope').value}`));
  $('ai-preview-cancel').addEventListener('click', invalidateStudy);
  $('ai-preview-send').addEventListener('click', sendStudyPreview);
  for (const [id, format] of [['export-study-md','md'],['export-study-svg','svg']]) {
    $(id).addEventListener('click', () => {
      if (!state.study) return;
      window.location.href = `/api/study/export?${new URLSearchParams({key:state.study.cache_key,format})}`;
    });
  }
  $('upload-file').addEventListener('click',() => importNewSource('file'));
  $('pull-link').addEventListener('click',() => importNewSource('link'));
  $('import-shortcut').addEventListener('click', () => {
    $('add-document').scrollIntoView({behavior:'smooth', block:'start'});
  });
  $('change-inbox').addEventListener('click', () => {
    $('storage-settings').open = true;
    $('storage-settings').scrollIntoView({behavior:'smooth', block:'start'});
    $('inbox-setting').focus({preventScroll:true});
  });
  $('import-inbox-all').addEventListener('click', importInboxAll);
  $('folder-files').addEventListener('change', () => {
    const choice = folderCandidates($('folder-files').files);
    const valid = choice.accepted.length > 0 && choice.accepted.length <= MAX_FOLDER_FILES
      && choice.totalBytes <= MAX_FOLDER_BYTES;
    $('import-folder').disabled = !valid;
    $('folder-summary').textContent = choice.all.length
      ? `${choice.accepted.length} supported file(s) ready; ${choice.skipped} skipped (unsupported, empty or over 25 MB). ${valid ? 'Press Import chosen folder to begin.' : 'Choose a smaller folder; maximum 300 supported files and 1 GB per batch.'}`
      : 'No folder chosen.';
  });
  $('import-folder').addEventListener('click', importChosenFolder);
  $('android-choose-folder').addEventListener('click', () => {
    $('android-folder-summary').textContent = 'Opening the Android folder picker…';
    window.location.href = 'fd-folder://choose';
  });
  window.fdFolderImportStatus = message => { $('android-folder-summary').textContent = message; };
  window.fdFolderImportDone = async message => {
    $('android-folder-summary').textContent = message;
    try { await refreshStatus(); await refreshCards(); }
    catch (error) { $('android-folder-summary').textContent += ` Refresh failed: ${localNetworkError(error).message}`; }
  };
  $('refresh-inbox').addEventListener('click', async () => {
    try { await refreshStatus(); $('storage-message').textContent = 'Import folder refreshed.'; }
    catch (error) { $('storage-message').textContent = error.message; }
  });
  $('save-storage').addEventListener('click', async () => {
    const button = $('save-storage');
    try {
      button.disabled = true;
      const data_root = $('storage-path').value.trim();
      const inbox_root = $('inbox-setting').value.trim();
      if (!data_root) throw new Error('Enter an absolute study library folder.');
      if (!window.confirm('Use these folders now? Existing documents will not be moved or deleted.')) return;
      await api('/api/storage', {data_root, inbox_root});
      await refreshStatus(); await openDocument(state.documents[0]?.id || ''); await refreshCards();
      $('storage-message').textContent = 'Folder choice saved. The active library and inbox are shown above.';
    } catch (error) { $('storage-message').textContent = error.message; }
    finally { button.disabled = false; }
  });
  $('quit-app').addEventListener('click', async () => {
    if (!window.confirm('Quit Framework Distiller? The study page will stop until you open the app again.')) return;
    try { await api('/api/app/quit', {}); document.body.replaceChildren(node('main','empty','Framework Distiller has closed. You can close this browser tab.')); }
    catch (error) { say(error.message,true); }
  });
  let timer;
  for (const id of ['search','theme','depth','state','study-set']) {
    $(id).addEventListener(id==='search'?'input':'change', () => {
      clearTimeout(timer); timer=setTimeout(() => refreshCards().catch(e => say(e.message,true)),180);
    });
  }
  $('clear-filters').addEventListener('click',() => {
    $('search').value=''; $('theme').value=''; $('depth').value=''; $('state').value=''; $('study-set').value='all';
    refreshCards().catch(e=>say(e.message,true));
  });
  $('export').addEventListener('click',() => {
    if (!state.filteredCount) { say('No matching cards to export.',true); return; }
    window.location.href = `/api/export?${params()}`;
    say(`Exporting all ${state.filteredCount} matching cards as Markdown.`);
  });
}
init();
