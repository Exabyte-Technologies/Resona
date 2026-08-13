(() => {
  const app = document.querySelector('#app'); if (!app) return;
  const csrf = app.dataset.csrf, username = app.dataset.username, frame = document.querySelector('#dynamic-page'), loader = document.querySelector('#frame-loading');
  const sheet = document.querySelector('#agent-sheet'), backdrop = document.querySelector('#sheet-backdrop'), prompt = document.querySelector('#agent-prompt'), status = document.querySelector('#agent-status');
  const accountDialog = document.querySelector('#account-dialog'), agentCaptchaDialog = document.querySelector('#agent-captcha-dialog'), agentCapWidget = document.querySelector('#agent-cap-widget'), continueAgentRequest = document.querySelector('#continue-agent-request');
  const chordModel = new window.ResonaChordModel(username);
  const PROVIDER_CREDENTIAL_PLACEHOLDER = '{{RESONA_SERVER_API_KEY}}';
  const api = (url, options = {}) => fetch(url, { ...options, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf, ...(options.headers || {}) } });
  function icon(name) { const paths = { sparkles:'M12 2l1.4 5.1L18 9l-4.6 1.8L12 16l-1.4-5.2L6 9l4.6-1.9L12 2zm6 12l.8 2.4L21 18l-2.2.8L18 21l-.8-2.2L15 18l2.2-1.6L18 14z',sliders:'M4 7h10m4 0h2M4 17h2m4 0h10M14 4v6M6 14v6',gears:'M8 3v2m0 8v2M2 9h2m8 0h2M3.76 4.76l1.42 1.42m5.64 5.64 1.42 1.42m0-8.48-1.42 1.42m-5.64 5.64-1.42 1.42M8 6a3 3 0 1 0 0 6 3 3 0 0 0 0-6zm8 5v1.5m0 7V21m-5-5h1.5m7 0H21m-8.54-3.54 1.06 1.06m4.96 4.96 1.06 1.06m0-7.08-1.06 1.06m-4.96 4.96-1.06 1.06M16 13.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z',waves:'M3 12h3l2-6 4 12 3-9 2 3h4', 'cloud-rain':'M7 18h10a4 4 0 0 0 0-8 6 6 0 0 0-11.5 1.5A3.5 3.5 0 0 0 7 18zm2 2-1 2m5-2-1 2m5-2-1 2',history:'M4 12a8 8 0 1 0 2-5.3L4 9m0-5v5h5m3-3v6l4 2',user:'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm-7 9a7 7 0 0 1 14 0',circle:'M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16z' }; return `<svg viewBox="0 0 24 24"><path d="${paths[name] || paths.circle}"/></svg>`; }
  document.querySelectorAll('.nav-icon').forEach(el => {
    if (el.dataset.iconPath) {
      const img = document.createElement('img');
      img.src = `/storage/${encodeURIComponent(username)}/${el.dataset.iconPath.split('/').map(encodeURIComponent).join('/')}`;
      img.alt = ''; el.replaceChildren(img);
    } else el.innerHTML = icon(el.dataset.icon);
  });
  document.querySelectorAll('.nav-item').forEach(button => button.addEventListener('click', () => { document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('active')); button.classList.add('active'); loader.classList.add('show'); frame.src = `/storage/${encodeURIComponent(username)}/${button.dataset.page.split('/').map(encodeURIComponent).join('/')}`; }));
  const sendToPage = (type, payload = {}) => frame.contentWindow?.postMessage({ type, ...payload }, '*');
  const sendAudioState = () => sendToPage('resona:audio-state', { playing:window.resonaAudio.playing, ambientPlaying:window.resonaAudio.ambientPlaying, noisePlaying:window.resonaAudio.noisePlaying, chordProgressionPlaying:window.resonaAudio.chordProgressionPlaying, chordProgressionPaused:window.resonaAudio.chordProgressionPaused, chordProgressionIndex:window.resonaAudio.chordProgressionIndex, config:window.resonaAudio.config });
  const modePresets = {
    sleep:{ beat:2, atmosphere:'deep', rootMidi:57 },
    meditation:{ beat:6, atmosphere:'deep', rootMidi:53 },
    focus:{ beat:10, atmosphere:'restore', rootMidi:57 },
    awake:{ beat:18, atmosphere:'restore', rootMidi:48 }
  };
  function applyMode(name) {
    const preset = modePresets[name]; if (!preset) return;
    window.resonaAudio.config.mode = name;
    window.resonaAudio.setBeat(preset.beat); window.resonaAudio.setAtmosphere(preset.atmosphere); window.resonaAudio.setTonalCentre(preset.rootMidi);
    window.resonaAudio.notifyState();
  }
  function toggleSession() {
    const active = window.resonaAudio.playing || window.resonaAudio.ambientPlaying;
    if (active) { if (window.resonaAudio.playing) window.resonaAudio.stop(); if (window.resonaAudio.ambientPlaying) window.resonaAudio.stopAmbient(); }
    else { window.resonaAudio.start(); window.resonaAudio.startAmbient(); }
    window.resonaAudio.notifyState();
  }
  const chordPipeline = { enabled:false, token:0, options:null, playing:null, ready:[], generating:null, nextSetNumber:0 };
  const setLabel = number => { let value = number + 1, label = ''; while (value > 0) { value -= 1; label = String.fromCharCode(65 + value % 26) + label; value = Math.floor(value / 26); } return label; };
  const sendChordPipeline = () => sendToPage('resona:chord-pipeline', { enabled:chordPipeline.enabled, playing:chordPipeline.playing?.label || null, ready:chordPipeline.ready.map(set => set.label), generating:chordPipeline.generating?.label || null });
  const resetChordPipeline = enabled => { chordPipeline.token += 1; chordPipeline.enabled = enabled; chordPipeline.options = null; chordPipeline.playing = null; chordPipeline.ready = []; chordPipeline.generating = null; chordPipeline.nextSetNumber = 0; sendChordPipeline(); };
  const makeChordSet = chords => ({ label:setLabel(chordPipeline.nextSetNumber++), chords });
  async function generateChordSet(options, previous = null) {
    if (!previous?.length) return chordModel.generate(options);
    const generated = await chordModel.generate({ ...options, seedChords:[previous[previous.length - 1]], length:options.length + 1 });
    return generated.slice(1, options.length + 1);
  }
  function generateAhead(previous) {
    if (!chordPipeline.enabled || chordPipeline.generating || !chordPipeline.options) return;
    const token = chordPipeline.token, label = setLabel(chordPipeline.nextSetNumber);
    chordPipeline.generating = { label }; sendChordPipeline();
    setTimeout(async () => {
      try {
        const chords = await generateChordSet(chordPipeline.options, previous);
        if (!chordPipeline.enabled || token !== chordPipeline.token) return;
        chordPipeline.ready.push(makeChordSet(chords)); chordPipeline.generating = null; sendChordPipeline();
      } catch (error) {
        if (token !== chordPipeline.token) return;
        chordPipeline.generating = null; sendChordPipeline(); sendToPage('resona:chord-status', { status:'error', message:`Continuous generation paused: ${error.message}` });
      }
    }, 0);
  }
  async function generateProgression(data) {
    const continuous = Boolean(data.continuous), token = chordPipeline.token + 1;
    resetChordPipeline(continuous); chordPipeline.token = token;
    const options = { seedChords:Array.isArray(data.seedChords) ? data.seedChords : [], length:Math.max(2, Math.min(32, Number(data.length) || 8)), temperature:data.temperature, topK:data.topK, greedy:data.greedy };
    chordPipeline.options = options;
    sendToPage('resona:chord-status', { status:'loading', message:continuous ? 'Preparing playing and ready chord sets…' : 'Loading your private model…' });
    try {
      const firstChords = await generateChordSet(options);
      if (token !== chordPipeline.token) return;
      if (continuous) {
        const secondChords = await generateChordSet(options, firstChords);
        if (token !== chordPipeline.token) return;
        chordPipeline.playing = makeChordSet(firstChords); chordPipeline.ready = [makeChordSet(secondChords)];
      }
      window.resonaAudio.setChordProgression(firstChords, data.duration);
      sendToPage('resona:chord-result', { chords:window.resonaAudio.config.ambient.chordProgression, duration:window.resonaAudio.config.ambient.chordDuration });
      if (continuous) { sendChordPipeline(); generateAhead(chordPipeline.ready[0].chords); }
    } catch (error) {
      if (token !== chordPipeline.token) return;
      if (continuous) resetChordPipeline(false);
      sendToPage('resona:chord-status', { status:'error', message:error.message });
    }
  }
  window.addEventListener('resona:chord-set-ended', () => {
    if (!chordPipeline.enabled || !chordPipeline.ready.length) return;
    chordPipeline.playing = chordPipeline.ready.shift();
    window.resonaAudio.setChordProgression(chordPipeline.playing.chords, window.resonaAudio.config.ambient.chordDuration);
    sendToPage('resona:chord-result', { chords:window.resonaAudio.config.ambient.chordProgression, duration:window.resonaAudio.config.ambient.chordDuration });
    sendChordPipeline();
    if (!chordPipeline.generating) generateAhead((chordPipeline.ready[chordPipeline.ready.length - 1] || chordPipeline.playing).chords);
  });
  frame.addEventListener('load', () => { loader.classList.remove('show'); sendAudioState(); sendChordPipeline(); });
  window.addEventListener('resona:audio-state-change', sendAudioState);
  window.addEventListener('message', async event => {
    if (event.source !== frame.contentWindow || !event.data || typeof event.data !== 'object') return;
    const data = event.data;
    if (data.type === 'resona:files') {
      const { requestId, type, ...payload } = data;
      try {
        const response = await api('/player/api/files', { method:'POST', body:JSON.stringify(payload) });
        const result = await response.json();
        sendToPage('resona:files-result', { requestId, ...result, ok:response.ok && result.ok });
      } catch (error) { sendToPage('resona:files-result', { requestId, ok:false, error:error.message }); }
    } else if (data.type === 'resona:audio') {
      if (data.action === 'toggle') window.resonaAudio.toggle();
      else if (data.action === 'toggleSession') toggleSession();
      else if (data.action === 'applyMode') applyMode(data.mode);
      else if (data.action === 'setMaster' && Number.isFinite(Number(data.value))) window.resonaAudio.setMaster(data.value);
      else if (data.action === 'setBeat' && Number.isFinite(Number(data.value))) window.resonaAudio.setBeat(Math.max(.1, Math.min(100, Number(data.value))));
      else if (data.action === 'setBinauralMode' && ['individual','difference'].includes(data.value)) window.resonaAudio.setBinauralMode(data.value);
      else if (data.action === 'setEarFrequency' && ['left','right'].includes(data.ear) && Number.isFinite(Number(data.value))) window.resonaAudio.setEarFrequency(data.ear, Math.max(40, Math.min(400, Number(data.value))));
      else if (data.action === 'setNoise' && ['white','pink','brown','rain','ocean','forest'].includes(data.value)) { const restartsNoise = window.resonaAudio.noisePlaying; window.resonaAudio.setNoise(data.value); if (restartsNoise) setTimeout(sendAudioState, 950); }
      else if (data.action === 'setLayer' && typeof data.name === 'string' && Number.isFinite(Number(data.value))) window.resonaAudio.setLayer(data.name, Math.max(0, Math.min(100, Number(data.value))));
      else if (data.action === 'setVolume' && ['binaural','ambient','noise'].includes(data.name || data.volumeType) && Number.isFinite(Number(data.value ?? data.volumeValue))) window.resonaAudio.setVolume(data.name || data.volumeType, Math.max(0, Math.min(100, Number(data.value ?? data.volumeValue))));
      else if (data.action === 'toggleAmbient') window.resonaAudio.toggleAmbient();
      else if (data.action === 'setDroneFrequency' && Number.isFinite(Number(data.value))) window.resonaAudio.setDroneFrequency(Math.max(40, Math.min(400, Number(data.value))));
      else if (data.action === 'setAtmosphere' && ['restore','melancholy','deep'].includes(data.value)) window.resonaAudio.setAtmosphere(data.value);
      else if (data.action === 'setAmbientParameter' && ['warmth','movement','space','texture','shimmer','output'].includes(data.name) && Number.isFinite(Number(data.value))) window.resonaAudio.setAmbientParameter(data.name, Math.max(0, Math.min(100, Number(data.value))));
      else if (data.action === 'setTonalSource' && ['manual','generated'].includes(data.value)) window.resonaAudio.setTonalSource(data.value);
      else if (data.action === 'setTonalCentre' && [48,50,51,53,55,57].includes(Number(data.value))) window.resonaAudio.setTonalCentre(Number(data.value));
      else if (data.action === 'setChordTransition' && Number.isFinite(Number(data.value))) window.resonaAudio.setChordTransition(data.value);
      else if (data.action === 'setBinauralChordTransition' && Number.isFinite(Number(data.value))) window.resonaAudio.setBinauralChordTransition(data.value);
      else if (data.action === 'setAmbient' && ['drone','pads','textures','melody','spatial'].includes(data.name) && Number.isFinite(Number(data.value))) window.resonaAudio.setAmbient(data.name, Math.max(0, Math.min(100, Number(data.value))));
      else if (data.action === 'setContinuousChordMode') { if (!data.enabled) resetChordPipeline(false); else { chordPipeline.enabled = true; sendChordPipeline(); } }
      else if (data.action === 'generateChordProgression') await generateProgression(data);
      else if (data.action === 'toggleChordProgression') window.resonaAudio.toggleChordProgression();
      else if (data.action === 'stopChordProgression') window.resonaAudio.stopChordProgression();
      else if (data.action === 'replayChordProgression') window.resonaAudio.replayChordProgression();
      else if (data.action === 'toggleNoise') window.resonaAudio.toggleNoise();
      sendAudioState();
    } else if (data.type === 'resona:request' && data.resource === 'profile') {
      const profile = await fetch('/player/api/profile').then(response => response.json());
      sendToPage('resona:profile', { profile });
    } else if (data.type === 'resona:request' && data.resource === 'history') {
      const history = await fetch('/player/api/history').then(response => response.json());
      sendToPage('resona:history', { history });
    }
  });
  frame.addEventListener('error', showFallback);
  function openSheet(){ sheet.classList.add('open'); backdrop.classList.add('open'); setTimeout(() => prompt.focus(), 350); }
  function closeSheet(){ sheet.classList.remove('open'); backdrop.classList.remove('open'); }
  document.querySelector('#mic-button').addEventListener('click', openSheet); document.querySelector('#sheet-close').addEventListener('click', closeSheet); backdrop.addEventListener('click', closeSheet); document.addEventListener('keydown', e => { if(e.key === 'Escape') closeSheet(); });
  document.querySelector('#account-button').addEventListener('click', () => accountDialog.showModal());
  document.querySelectorAll('[data-close-dialog]').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
  document.querySelectorAll('.secure-dialog').forEach(dialog => dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); }));
  const accountForm = document.querySelector('[data-account-form]');
  accountForm.addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget, formStatus = form.querySelector('[data-account-status]'), submit = form.querySelector('[type="submit"]'), cap = form.querySelector('cap-widget');
    submit.disabled = true; formStatus.textContent = 'Saving securely…';
    try {
      const body = new FormData(form); if (event.submitter?.name) body.set(event.submitter.name, event.submitter.value);
      const response = await fetch(form.action, { method:'POST', headers:{'Accept':'application/json','X-CSRF-Token':csrf}, body });
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || 'Account update failed');
      formStatus.textContent = data.message;
      document.querySelector('#account-button span').textContent = form.elements.display_name.value.trim();
      form.elements.current_password.value = ''; form.elements.new_password.value = '';
    } catch (error) { formStatus.textContent = error.message; }
    finally { submit.disabled = false; cap.reset(); }
  });
  document.querySelectorAll('#prompt-examples button').forEach(button => button.addEventListener('click', () => { prompt.value = button.textContent.replace(/^“|”$/g,''); prompt.focus(); }));
  const recognition = window.SpeechRecognition || window.webkitSpeechRecognition; const voice = document.querySelector('#voice-button');
  if (recognition) { const listener = new recognition(); listener.interimResults = true; listener.continuous = false; listener.onstart = () => { voice.classList.add('listening'); document.querySelector('#agent-orb').classList.add('listening'); status.textContent = 'Listening…'; }; listener.onresult = e => { prompt.value = Array.from(e.results).map(r => r[0].transcript).join(''); }; listener.onend = () => { voice.classList.remove('listening'); document.querySelector('#agent-orb').classList.remove('listening'); status.textContent = ''; }; voice.addEventListener('click', () => listener.start()); } else voice.hidden = true;
  const ACTIVE_AGENT_REQUEST = 'resonaActiveAgentRequest';
  const newRequestId = () => window.crypto?.randomUUID?.() || `${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}_${Math.random().toString(36).slice(2)}`;
  const rememberAgentRequest = (requestId, value) => sessionStorage.setItem(ACTIVE_AGENT_REQUEST, JSON.stringify({requestId,value}));
  const forgetAgentRequest = () => sessionStorage.removeItem(ACTIVE_AGENT_REQUEST);
  const wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
  let pendingAgentPrompt = '', pendingAgentRequestId = '', agentCaptchaToken = '';
  function completeAgentRequest(data) {
    forgetAgentRequest(); pendingAgentPrompt = ''; pendingAgentRequestId = '';
    status.textContent = `${data.summary} · ${data.steps || 0} agent steps`;
    setTimeout(() => location.reload(), 900);
  }
  async function recoverAgentRequest(requestId, value) {
    const form = document.querySelector('#agent-form');
    rememberAgentRequest(requestId, value); form.classList.add('busy');
    let missingChecks = 0, connectionInterrupted = false;
    for (let attempt = 0; attempt < 1800; attempt += 1) {
      try {
        const response = await fetch(`/agent/status/${encodeURIComponent(requestId)}`, {cache:'no-store'});
        if (response.status === 404) {
          missingChecks += 1;
          if (missingChecks >= 5) { forgetAgentRequest(); status.textContent = 'The connection recovered, but the request did not reach Resona. Please send it again.'; form.classList.remove('busy'); return; }
        } else {
          missingChecks = 0;
          const data = await response.json();
          if (data.status === 'complete') { completeAgentRequest(data); return; }
          if (data.status === 'failed' || data.status === 'rejected') { forgetAgentRequest(); status.textContent = data.error || 'The agent request did not complete.'; form.classList.remove('busy'); return; }
          status.innerHTML = connectionInterrupted
            ? '<span class="thinking"></span> Connection restored. Resona is still applying your changes…'
            : '<span class="thinking"></span> Resona is still applying your changes…';
          connectionInterrupted = false;
        }
      } catch (_error) {
        connectionInterrupted = true;
        status.innerHTML = '<span class="thinking"></span> Connection interrupted. Waiting for Resona to finish safely…';
      }
      await wait(2000);
    }
    status.textContent = 'The request is still running. Refreshing later will reconnect to its status.'; form.classList.remove('busy');
  }
  async function sendAgentRequest(value, capToken = null, requestId = pendingAgentRequestId || newRequestId()) {
    const form = document.querySelector('#agent-form');
    pendingAgentPrompt = value; pendingAgentRequestId = requestId; rememberAgentRequest(requestId, value); status.innerHTML = '<span class="thinking"></span> Inspecting files and working until your request is complete…'; form.classList.add('busy');
    try {
      const response = await api('/agent/modify', {method:'POST', body:JSON.stringify({prompt:value,credential:PROVIDER_CREDENTIAL_PLACEHOLDER,cap_token:capToken,request_id:requestId})});
      const data = await response.json();
      if (data.captcha_required) { forgetAgentRequest(); status.textContent = 'Complete the security check to continue.'; agentCaptchaToken = ''; agentCapWidget.reset(); continueAgentRequest.disabled = true; agentCaptchaDialog.showModal(); return; }
      if (response.status === 202 || data.status === 'running') { recoverAgentRequest(requestId, value); return; }
      if (!response.ok) throw new Error(data.error || 'Modification failed');
      completeAgentRequest(data);
    } catch (error) {
      if (error instanceof TypeError || /fetch|network|load failed/i.test(error.message)) { recoverAgentRequest(requestId, value); return; }
      forgetAgentRequest(); status.textContent = error.message; form.classList.remove('busy');
    }
  }
  document.querySelector('#agent-form').addEventListener('submit', event => { event.preventDefault(); const value = prompt.value.trim(); if (value) sendAgentRequest(value); });
  agentCapWidget.addEventListener('solve', event => { agentCaptchaToken = event.detail?.token || ''; continueAgentRequest.disabled = !agentCaptchaToken; });
  agentCapWidget.addEventListener('reset', () => { agentCaptchaToken = ''; continueAgentRequest.disabled = true; });
  continueAgentRequest.addEventListener('click', () => { if (!pendingAgentPrompt || !agentCaptchaToken) return; const token = agentCaptchaToken; agentCaptchaToken = ''; agentCaptchaDialog.close(); sendAgentRequest(pendingAgentPrompt, token); });
  agentCaptchaDialog.addEventListener('close', () => { document.querySelector('#agent-form').classList.remove('busy'); });
  try { const active = JSON.parse(sessionStorage.getItem(ACTIVE_AGENT_REQUEST) || 'null'); if (active?.requestId && active?.value) recoverAgentRequest(active.requestId, active.value); } catch (_error) { forgetAgentRequest(); }
  document.querySelector('#reset-original-ui').addEventListener('click', async event => { if (!window.confirm('Restore the original Resona UI? Your account, memories, history, and a recovery snapshot will be kept.')) return; const button = event.currentTarget; button.disabled = true; try { const response = await api('/agent/reset-ui', {method:'POST', body:'{}'}); const data = await response.json(); if (!response.ok) throw new Error(data.error || 'The original UI could not be restored'); button.querySelector('span').textContent = 'Restored'; location.reload(); } catch (error) { button.disabled = false; toast(error.message); } });
  function showFallback(){ document.querySelector('#fallback-alert').classList.add('open'); backdrop.classList.add('open'); }
  document.querySelector('#dismiss-fallback').addEventListener('click', () => { document.querySelector('#fallback-alert').classList.remove('open'); backdrop.classList.remove('open'); });
  document.querySelector('#reset-last').addEventListener('click', async () => { const snapshots = await fetch('/player/api/snapshots').then(r=>r.json()); if (!snapshots.length) return toast('No earlier preset is available'); const response = await api(`/agent/rollback/${snapshots[0]}`, {method:'POST', body:'{}'}); if(response.ok) location.reload(); else toast('Reset failed'); });
  function toast(message){ const el = document.createElement('div'); el.className='toast'; el.textContent=message; document.body.append(el); setTimeout(()=>el.remove(),2600); }
})();
