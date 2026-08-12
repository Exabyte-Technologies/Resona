(() => {
  const app = document.querySelector('#app'); if (!app) return;
  const csrf = app.dataset.csrf, username = app.dataset.username, frame = document.querySelector('#dynamic-page'), loader = document.querySelector('#frame-loading');
  const sheet = document.querySelector('#agent-sheet'), backdrop = document.querySelector('#sheet-backdrop'), prompt = document.querySelector('#agent-prompt'), status = document.querySelector('#agent-status');
  const chordModel = new window.ResonaChordModel(username);
  const PROVIDER_CREDENTIAL_PLACEHOLDER = '{{RESONA_SERVER_API_KEY}}';
  const api = (url, options = {}) => fetch(url, { ...options, headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf, ...(options.headers || {}) } });
  function icon(name) { const paths = { sparkles:'M12 2l1.4 5.1L18 9l-4.6 1.8L12 16l-1.4-5.2L6 9l4.6-1.9L12 2zm6 12l.8 2.4L21 18l-2.2.8L18 21l-.8-2.2L15 18l2.2-1.6L18 14z',sliders:'M4 7h10m4 0h2M4 17h2m4 0h10M14 4v6M6 14v6',waves:'M3 12h3l2-6 4 12 3-9 2 3h4', 'cloud-rain':'M7 18h10a4 4 0 0 0 0-8 6 6 0 0 0-11.5 1.5A3.5 3.5 0 0 0 7 18zm2 2-1 2m5-2-1 2m5-2-1 2',history:'M4 12a8 8 0 1 0 2-5.3L4 9m0-5v5h5m3-3v6l4 2',user:'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm-7 9a7 7 0 0 1 14 0',circle:'M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16z' }; return `<svg viewBox="0 0 24 24"><path d="${paths[name] || paths.circle}"/></svg>`; }
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
  frame.addEventListener('load', () => { loader.classList.remove('show'); sendAudioState(); });
  window.addEventListener('resona:audio-state-change', sendAudioState);
  window.addEventListener('message', async event => {
    if (event.source !== frame.contentWindow || !event.data || typeof event.data !== 'object') return;
    const data = event.data;
    if (data.type === 'resona:audio') {
      if (data.action === 'toggle') window.resonaAudio.toggle();
      else if (data.action === 'setBeat' && Number.isFinite(Number(data.value))) window.resonaAudio.setBeat(Math.max(.1, Math.min(100, Number(data.value))));
      else if (data.action === 'setNoise' && ['white','pink','brown','rain','ocean','forest'].includes(data.value)) { const restartsNoise = window.resonaAudio.noisePlaying; window.resonaAudio.setNoise(data.value); if (restartsNoise) setTimeout(sendAudioState, 950); }
      else if (data.action === 'setLayer' && typeof data.name === 'string' && Number.isFinite(Number(data.value))) window.resonaAudio.setLayer(data.name, Math.max(0, Math.min(100, Number(data.value))));
      else if (data.action === 'setVolume' && ['binaural','ambient','noise'].includes(data.name || data.volumeType) && Number.isFinite(Number(data.value ?? data.volumeValue))) window.resonaAudio.setVolume(data.name || data.volumeType, Math.max(0, Math.min(100, Number(data.value ?? data.volumeValue))));
      else if (data.action === 'toggleAmbient') window.resonaAudio.toggleAmbient();
      else if (data.action === 'setDroneFrequency' && Number.isFinite(Number(data.value))) window.resonaAudio.setDroneFrequency(Math.max(40, Math.min(400, Number(data.value))));
      else if (data.action === 'setAmbient' && ['drone','pads','textures','melody','spatial'].includes(data.name) && Number.isFinite(Number(data.value))) window.resonaAudio.setAmbient(data.name, Math.max(0, Math.min(100, Number(data.value))));
      else if (data.action === 'generateChordProgression') {
        sendToPage('resona:chord-status', { status:'loading', message:'Loading your private model…' });
        try {
          const chords = await chordModel.generate({ seedChords:Array.isArray(data.seedChords) ? data.seedChords : [], length:data.length, temperature:data.temperature, topK:data.topK, greedy:data.greedy });
          window.resonaAudio.setChordProgression(chords, data.duration);
          sendToPage('resona:chord-result', { chords, duration:window.resonaAudio.config.ambient.chordDuration });
        } catch (error) { sendToPage('resona:chord-status', { status:'error', message:error.message }); }
      }
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
  document.querySelectorAll('#prompt-examples button').forEach(button => button.addEventListener('click', () => { prompt.value = button.textContent.replace(/^“|”$/g,''); prompt.focus(); }));
  const recognition = window.SpeechRecognition || window.webkitSpeechRecognition; const voice = document.querySelector('#voice-button');
  if (recognition) { const listener = new recognition(); listener.interimResults = true; listener.continuous = false; listener.onstart = () => { voice.classList.add('listening'); document.querySelector('#agent-orb').classList.add('listening'); status.textContent = 'Listening…'; }; listener.onresult = e => { prompt.value = Array.from(e.results).map(r => r[0].transcript).join(''); }; listener.onend = () => { voice.classList.remove('listening'); document.querySelector('#agent-orb').classList.remove('listening'); status.textContent = ''; }; voice.addEventListener('click', () => listener.start()); } else voice.hidden = true;
  document.querySelector('#agent-form').addEventListener('submit', async event => { event.preventDefault(); const value = prompt.value.trim(); if (!value) return; status.innerHTML = '<span class="thinking"></span> Inspecting files and working until your request is complete…'; event.currentTarget.classList.add('busy'); try { const response = await api('/agent/modify', {method:'POST', body:JSON.stringify({prompt:value,credential:PROVIDER_CREDENTIAL_PLACEHOLDER})}); const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Modification failed'); status.textContent = `${data.summary} · ${data.steps} agent steps`; setTimeout(() => location.reload(), 1500); } catch (error) { status.textContent = error.message; event.currentTarget.classList.remove('busy'); } });
  document.querySelector('#reset-original-ui').addEventListener('click', async event => { if (!window.confirm('Restore the original Resona UI? Your account, memories, history, and a recovery snapshot will be kept.')) return; const button = event.currentTarget; button.disabled = true; try { const response = await api('/agent/reset-ui', {method:'POST', body:'{}'}); const data = await response.json(); if (!response.ok) throw new Error(data.error || 'The original UI could not be restored'); button.querySelector('span').textContent = 'Restored'; location.reload(); } catch (error) { button.disabled = false; toast(error.message); } });
  function showFallback(){ document.querySelector('#fallback-alert').classList.add('open'); backdrop.classList.add('open'); }
  document.querySelector('#dismiss-fallback').addEventListener('click', () => { document.querySelector('#fallback-alert').classList.remove('open'); backdrop.classList.remove('open'); });
  document.querySelector('#reset-last').addEventListener('click', async () => { const snapshots = await fetch('/player/api/snapshots').then(r=>r.json()); if (!snapshots.length) return toast('No earlier preset is available'); const response = await api(`/agent/rollback/${snapshots[0]}`, {method:'POST', body:'{}'}); if(response.ok) location.reload(); else toast('Reset failed'); });
  function toast(message){ const el = document.createElement('div'); el.className='toast'; el.textContent=message; document.body.append(el); setTimeout(()=>el.remove(),2600); }
})();
