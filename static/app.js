const $ = (id) => document.getElementById(id);
const state = { allCards: [], shown: [], documents: [], official: [], themes: [], ai: null };

function node(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
}
function say(message, isError = false) {
  $('message').textContent = message;
  $('message').style.color = isError ? '#ffb6aa' : '';
}
function sourceSay(message, isError = false) {
  say(message, isError);
  $('source-message').textContent = message;
  $('source-message').style.color = isError ? '#ffb6aa' : '';
}
async function api(path, body) {
  const options = body === undefined ? {credentials:'same-origin'} : {
    method: 'POST', headers: {'Content-Type':'application/json', 'X-Local-Client':'FrameworkDistiller'},
    body: JSON.stringify(body), credentials:'same-origin'
  };
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
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
  for (const [field, key] of [['theme','theme'],['depth','depth'],['state','state'],['search','q']]) {
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
    ? `${provider} key ready for this app session · ${state.ai.models[provider]}. Nothing sent until you press Ask AI on a card.`
    : 'AI is off for this provider. No key is included or saved.';
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
      const query = new URLSearchParams({...meta, name:file.name});
      const response = await fetch(`/api/ingest/upload?${query}`, {
        method:'POST', headers:{'Content-Type':'application/octet-stream',
          'X-Local-Client':'FrameworkDistiller'}, credentials:'same-origin', body:file
      });
      result = await response.json();
      if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    } else result = await api('/api/ingest/url', meta);
    sourceSay(`${result.cards} cards imported; SHA-256 ${result.sha256.slice(0,16)}… · ${result.provenance.replaceAll('_',' ')}.`);
    await refreshStatus(); await refreshCards();
  } catch (error) { sourceSay(error.message, true); }
  finally { uploadButton.disabled = linkButton.disabled = false; }
}

async function refreshStatus() {
  const info = await api('/api/status');
  state.documents = info.documents;
  state.official = info.official;
  state.themes = info.themes;
  state.storage = info.storage;
  $('library-path').textContent = info.storage.data_root;
  $('desktop-storage-controls').hidden = !info.storage.editable;
  $('android-storage-info').hidden = info.storage.platform !== 'android';
  $('storage-path').value = info.storage.data_root;
  $('inbox-setting').value = info.storage.inbox_custom ? info.storage.inbox_root : '';
  $('inbox-path').textContent = info.inbox_path;
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
        await refreshStatus(); await refreshCards();
      } catch (error) { say(error.message, true); }
      finally { button.disabled = false; button.textContent = 'Import'; }
    });
    row.append(title, button); official.append(row);
  }
  const inbox = $('inbox-files'); inbox.replaceChildren();
  const blocked = info.inbox.filter(file => !file.importable).length;
  $('inbox-summary').textContent = `${info.inbox.length} supported file${info.inbox.length===1?'':'s'} found${blocked ? ` · ${blocked} over the 25 MB per-file limit` : ''}. Nothing imports until you choose it.`;
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
        await refreshStatus(); await refreshCards();
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

async function refreshCards() {
  const [all, filtered] = await Promise.all([
    api('/api/cards'), api(`/api/cards?${params()}`)
  ]);
  state.allCards = all.cards;
  state.filteredCount = filtered.count;
  if ($('study-set').value === 'focus') {
    const unread = filtered.cards.filter(card => card.state === 'unread');
    const grouped = new Map();
    for (const card of unread) {
      if (!grouped.has(card.theme)) grouped.set(card.theme, []);
      grouped.get(card.theme).push(card);
    }
    const picked = [];
    while (picked.length < 10 && [...grouped.values()].some(group => group.length)) {
      for (const group of grouped.values()) {
        if (group.length && picked.length < 10) picked.push(group.shift());
      }
    }
    state.shown = picked;
    $('shown-count').textContent = String(picked.length);
    $('shown-label').textContent = `of ${filtered.count} matching · next unread`;
  } else {
    state.shown = filtered.cards;
    $('shown-count').textContent = String(filtered.count);
    $('shown-label').textContent = 'matching cards';
  }
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
  try { await startLocalSession(); await refreshStatus(); await refreshCards(); await refreshAiStatus(); }
  catch (error) { say(error.message,true); }
  $('ai-provider').addEventListener('change',() => {
    const provider = $('ai-provider').value;
    $('ai-model').value = provider && state.ai ? state.ai.models[provider] : '';
    refreshAiStatus().catch(e=>say(e.message,true));
  });
  $('ai-use-key').addEventListener('click',async () => {
    const provider=$('ai-provider').value, key=$('ai-key').value.trim(), model=$('ai-model').value.trim();
    try {
      if (!provider) throw new Error('Choose a provider first.');
      await api('/api/ai/key',{provider,key,model}); $('ai-key').value='';
      await refreshAiStatus();
    } catch(error) { $('ai-key').value=''; $('ai-status').textContent=error.message; }
  });
  $('ai-forget').addEventListener('click',async () => {
    const provider=$('ai-provider').value;
    try { if (!provider) throw new Error('Choose a provider first.');
      await api('/api/ai/forget',{provider}); $('ai-key').value=''; await refreshAiStatus();
      $('ai-model').value = state.ai.models[provider];
    } catch(error) { $('ai-status').textContent=error.message; }
  });
  $('upload-file').addEventListener('click',() => importNewSource('file'));
  $('pull-link').addEventListener('click',() => importNewSource('link'));
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
      await refreshStatus(); await refreshCards();
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
    $('search').value=''; $('theme').value=''; $('depth').value=''; $('state').value=''; $('study-set').value='focus';
    refreshCards().catch(e=>say(e.message,true));
  });
  $('export').addEventListener('click',() => {
    if (!state.filteredCount) { say('No matching cards to export.',true); return; }
    window.location.href = `/api/export?${params()}`;
    say(`Exporting all ${state.filteredCount} matching cards as Markdown.`);
  });
}
init();
