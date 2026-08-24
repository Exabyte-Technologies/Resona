import mimetypes
import posixpath
import re

from flask import Blueprint, abort, g, make_response, send_file

from .security import login_required
from .user_storage import safe_path


storage_bp = Blueprint("storage", __name__, url_prefix="/storage")


PAGE_BRIDGE = r'''<script data-resona-bridge>
(() => {
  const send = (type, payload = {}) => parent.postMessage({ type, ...payload }, '*');
  let fileRequestNumber = 0;
  const pendingFileRequests = new Map();
  const fileRequest = (action, payload = {}) => new Promise((resolve, reject) => {
    const requestId = `file-${Date.now()}-${++fileRequestNumber}`;
    const timer = setTimeout(() => { pendingFileRequests.delete(requestId); reject(new Error('Persistent file request timed out')); }, 30000);
    pendingFileRequests.set(requestId, { resolve, reject, timer });
    send('resona:files', { requestId, action, ...payload });
  });
  const blobToBase64 = async blob => {
    const bytes = new Uint8Array(await blob.arrayBuffer()); let binary = '';
    for (let offset = 0; offset < bytes.length; offset += 32768) binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
    return btoa(binary);
  };
  window.ResonaFiles = Object.freeze({
    list: (path = '') => fileRequest('list', { path }),
    read: (path, options = {}) => fileRequest('read', { path, encoding:options.encoding || 'text' }),
    write: (path, content) => fileRequest('write', { path, content }),
    upload: async (path, file) => fileRequest('upload', { path, content:await blobToBase64(file) }),
    mkdir: path => fileRequest('mkdir', { path }),
    move: (source, destination) => fileRequest('move', { source, destination }),
    delete: path => fileRequest('delete', { path })
  });
  const setRangeFromPointer = (control, clientX) => {
    const rect = control.getBoundingClientRect();
    const minimum = Number(control.min || 0);
    const maximum = Number(control.max || 100);
    const step = Number(control.step || 1);
    const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / Math.max(1, rect.width)));
    const rawValue = minimum + ratio * (maximum - minimum);
    const steppedValue = minimum + Math.round((rawValue - minimum) / step) * step;
    const precision = (String(step).split('.')[1] || '').length;
    control.value = String(Math.max(minimum, Math.min(maximum, Number(steppedValue.toFixed(precision)))));
    control.dispatchEvent(new Event('input', { bubbles:true }));
  };
  document.querySelectorAll('input[type="range"]').forEach(control => {
    control.style.touchAction = 'none';
    let activeTouchId = null;
    const matchingTouch = touchList => Array.from(touchList).find(touch => touch.identifier === activeTouchId);
    control.addEventListener('touchstart', event => {
      if (control.disabled || event.touches.length !== 1) return;
      event.preventDefault();
      activeTouchId = event.changedTouches[0].identifier;
      control.focus({ preventScroll:true });
      setRangeFromPointer(control, event.changedTouches[0].clientX);
    }, { passive:false });
    control.addEventListener('touchmove', event => {
      const touch = matchingTouch(event.touches);
      if (!touch || control.disabled) return;
      event.preventDefault();
      setRangeFromPointer(control, touch.clientX);
    }, { passive:false });
    const finishTouch = event => {
      if (matchingTouch(event.changedTouches)) activeTouchId = null;
    };
    control.addEventListener('touchend', finishTouch, { passive:true });
    control.addEventListener('touchcancel', finishTouch, { passive:true });
    control.addEventListener('pointerdown', event => {
      if (event.pointerType !== 'touch' || control.disabled || 'ontouchstart' in window) return;
      event.preventDefault();
      control.setPointerCapture?.(event.pointerId);
      setRangeFromPointer(control, event.clientX);
      const move = moveEvent => {
        if (moveEvent.pointerId !== event.pointerId) return;
        moveEvent.preventDefault();
        setRangeFromPointer(control, moveEvent.clientX);
      };
      const finish = endEvent => {
        if (endEvent.pointerId !== event.pointerId) return;
        control.removeEventListener('pointermove', move);
        control.removeEventListener('pointerup', finish);
        control.removeEventListener('pointercancel', finish);
      };
      control.addEventListener('pointermove', move);
      control.addEventListener('pointerup', finish);
      control.addEventListener('pointercancel', finish);
    });
  });
  const status = document.querySelector('[data-playback-status]');
  document.querySelectorAll('[data-mode]').forEach(button => button.addEventListener('click', () => send('resona:audio', { action:'applyMode', mode:button.dataset.mode })));
  document.querySelectorAll('[data-session-toggle]').forEach(button => button.addEventListener('click', () => send('resona:audio', { action:'toggleSession' })));
  document.querySelectorAll('[data-master-volume]').forEach(control => control.addEventListener('input', () => { const output = control.parentElement.querySelector('output'); if (output) output.textContent = control.value + '%'; send('resona:audio', { action:'setMaster', value:control.value }); }));
  document.querySelectorAll('[data-band]').forEach(button => button.addEventListener('click', () => {
    document.querySelectorAll('[data-band]').forEach(option => {
      const selected = option === button;
      option.classList.toggle('active', selected);
      option.setAttribute('aria-pressed', String(selected));
    });
    send('resona:audio', { action:'setBeat', value:button.dataset.band });
  }));
  document.querySelectorAll('[data-playback-toggle]:not([data-session-toggle])').forEach(button => button.addEventListener('click', () => send('resona:audio', { action:'toggle' })));
  const updateBinauralKnob = (control, value = control.value) => { const minimum = Number(control.min), maximum = Number(control.max), normalized = (Number(value) - minimum) / (maximum - minimum) * 100; control.parentElement.style.setProperty('--value', Math.max(0, Math.min(100, normalized))); const output = control.parentElement.querySelector('output'); if (output) output.textContent = `${Number(value).toFixed(Number(value) % 1 ? 1 : 0)} Hz`; };
  document.querySelectorAll('[data-binaural-mode]').forEach(button => button.addEventListener('click', () => send('resona:audio', { action:'setBinauralMode', value:button.dataset.binauralMode })));
  document.querySelectorAll('[data-ear-frequency]').forEach(control => { updateBinauralKnob(control); control.addEventListener('input', () => { updateBinauralKnob(control); send('resona:audio', { action:'setEarFrequency', ear:control.dataset.earFrequency, value:control.value }); }); });
  document.querySelectorAll('[data-binaural-difference]').forEach(control => { updateBinauralKnob(control); control.addEventListener('input', () => { updateBinauralKnob(control); send('resona:audio', { action:'setBeat', value:control.value }); }); });
  document.querySelectorAll('[data-noise]').forEach(button => button.addEventListener('click', () => { document.querySelectorAll('[data-noise]').forEach(option => { const selected = option === button; option.classList.toggle('active', selected); option.setAttribute('aria-pressed', String(selected)); }); send('resona:audio', { action:'setNoise', value:button.dataset.noise }); }));
  document.querySelectorAll('[data-noise-toggle]').forEach(button => button.addEventListener('click', () => send('resona:audio', { action:'toggleNoise' })));
  document.querySelectorAll('[data-audio]').forEach(control => control.addEventListener('input', () => send('resona:audio', { action:'setLayer', name:control.dataset.audio, value:control.value })));
  document.querySelectorAll('[data-volume]').forEach(control => control.addEventListener('input', () => { const output = control.parentElement.querySelector('output'); if (output) output.textContent = control.value; send('resona:audio', { action:'setVolume', name:control.dataset.volume, value:control.value }); }));
  document.querySelectorAll('[data-ambient]').forEach(control => control.addEventListener('input', () => { const output = control.parentElement.querySelector('output'); if (output) output.textContent = control.value; send('resona:audio', { action:'setAmbient', name:control.dataset.ambient, value:control.value }); }));
  document.querySelectorAll('[data-atmosphere]').forEach(button => button.addEventListener('click', () => send('resona:audio', { action:'setAtmosphere', value:button.dataset.atmosphere })));
  document.querySelectorAll('[data-tonal-source]').forEach(button => button.addEventListener('click', () => send('resona:audio', { action:'setTonalSource', value:button.dataset.tonalSource })));
  document.querySelectorAll('[data-tonal-centre]').forEach(button => button.addEventListener('click', () => send('resona:audio', { action:'setTonalCentre', value:button.dataset.tonalCentre })));
  document.querySelectorAll('[data-synth-parameter]').forEach(control => {
    const update = () => { control.parentElement.style.setProperty('--value', control.value); const output = control.parentElement.querySelector('output'); if (output) output.textContent = control.value; };
    update(); control.addEventListener('input', () => { update(); send('resona:audio', { action:'setAmbientParameter', name:control.dataset.synthParameter, value:control.value }); });
  });
  document.querySelectorAll('[data-drone-frequency]').forEach(control => control.addEventListener('input', () => { const output = control.parentElement.querySelector('output'); if (output) output.textContent = control.value + ' Hz'; send('resona:audio', { action:'setDroneFrequency', value:control.value }); }));
  const transitionLabel = value => Number(value) <= 0 ? 'Instant' : `${Number(value).toFixed(1)} s`;
  document.querySelectorAll('[data-chord-transition]').forEach(control => control.addEventListener('input', () => { const output = control.parentElement.querySelector('output'); if (output) output.textContent = transitionLabel(control.value); send('resona:audio', { action:'setChordTransition', value:control.value }); }));
  document.querySelectorAll('[data-binaural-chord-transition]').forEach(control => control.addEventListener('input', () => { const output = control.parentElement.querySelector('output'); if (output) output.textContent = transitionLabel(control.value); send('resona:audio', { action:'setBinauralChordTransition', value:control.value }); }));
  document.querySelectorAll('[data-chord-range]').forEach(control => control.addEventListener('input', () => { const output = control.parentElement.querySelector('output'); if (output) output.textContent = control.value; }));
  document.querySelectorAll('[data-chord-continuous]').forEach(control => control.addEventListener('change', () => send('resona:audio', { action:'setContinuousChordMode', enabled:control.checked })));
  document.querySelectorAll('[data-ambient-toggle]:not([data-session-toggle])').forEach(button => button.addEventListener('click', () => send('resona:audio', { action:'toggleAmbient' })));
  document.querySelectorAll('[data-chord-generate]').forEach(button => button.addEventListener('click', () => {
    const card = button.closest('[data-chord-card]'), seed = card.querySelector('[data-chord-seed]').value.split(/[|,\s]+/).filter(Boolean);
    button.disabled = true; const status = card.querySelector('[data-chord-status]'); status.textContent = 'Loading your private model…';
    send('resona:audio', { action:'generateChordProgression', seedChords:seed, length:card.querySelector('[data-chord-length]').value, duration:card.querySelector('[data-chord-duration]').value, temperature:card.querySelector('[data-chord-temperature]').value, topK:card.querySelector('[data-chord-top-k]').value, greedy:card.querySelector('[data-chord-greedy]').checked, continuous:Boolean(card.querySelector('[data-chord-continuous]')?.checked) });
  }));
  document.querySelectorAll('[data-chord-play]').forEach(button => button.addEventListener('click', () => send('resona:audio', { action:'toggleChordProgression' })));
  document.querySelectorAll('[data-chord-stop]').forEach(button => button.addEventListener('click', () => send('resona:audio', { action:'stopChordProgression' })));
  document.querySelectorAll('[data-chord-replay]').forEach(button => button.addEventListener('click', () => send('resona:audio', { action:'replayChordProgression' })));
  window.addEventListener('message', event => {
    const data = event.data;
    if (!data || typeof data !== 'object') return;
    if (data.type === 'resona:files-result') {
      const pending = pendingFileRequests.get(data.requestId); if (!pending) return;
      clearTimeout(pending.timer); pendingFileRequests.delete(data.requestId);
      if (data.ok) pending.resolve(data); else pending.reject(new Error(data.error || 'Persistent file request failed'));
    } else if (data.type === 'resona:audio-state') {
      const sessionPlaying = Boolean(data.playing || data.ambientPlaying);
      document.querySelectorAll('[data-session-toggle]').forEach(button => { button.classList.toggle('playing', sessionPlaying); button.setAttribute('aria-pressed', String(sessionPlaying)); const label = button.querySelector('strong'); if (label) label.textContent = sessionPlaying ? 'Pause' : 'Play'; });
      document.querySelectorAll('[data-mode]').forEach(button => { const selected = button.dataset.mode === data.config?.mode; button.classList.toggle('active', selected); button.setAttribute('aria-pressed', String(selected)); });
      document.querySelectorAll('[data-master-volume]').forEach(control => { const value = Number(data.config?.master); if (Number.isFinite(value)) { control.value = value; const output = control.parentElement.querySelector('output'); if (output) output.textContent = value + '%'; } });
      document.querySelectorAll('[data-band]').forEach(button => { const selected = Number(button.dataset.band) === Number(data.config?.beat); button.classList.toggle('active', selected); button.setAttribute('aria-pressed', String(selected)); });
      const binauralMode = data.config?.binauralMode === 'individual' ? 'individual' : 'difference';
      document.querySelectorAll('[data-binaural-mode-card]').forEach(card => { const selected = card.dataset.binauralModeCard === binauralMode; card.classList.toggle('active',selected); card.classList.toggle('inactive',!selected); card.setAttribute('aria-disabled',String(!selected)); card.querySelectorAll('input').forEach(control => { control.disabled = !selected; }); const button = card.querySelector('[data-binaural-mode]'); if (button) { button.classList.toggle('active',selected); button.setAttribute('aria-pressed',String(selected)); button.textContent = selected ? 'Active' : (button.dataset.binauralMode === 'individual' ? 'Use individual' : 'Use difference'); } });
      document.querySelectorAll('[data-ear-frequency]').forEach(control => { const value = data.config?.[control.dataset.earFrequency === 'left' ? 'leftFrequency' : 'rightFrequency']; if (Number.isFinite(Number(value))) { control.value = value; updateBinauralKnob(control,value); } });
      document.querySelectorAll('[data-binaural-difference]').forEach(control => { const value = data.config?.beat; if (Number.isFinite(Number(value))) { control.value = value; updateBinauralKnob(control,value); } });
      document.querySelectorAll('[data-ambient]').forEach(control => { const value = data.config?.ambient?.[control.dataset.ambient]; if (Number.isFinite(Number(value))) { control.value = value; const output = control.parentElement.querySelector('output'); if (output) output.textContent = value; } });
      document.querySelectorAll('[data-atmosphere]').forEach(button => { const selected = button.dataset.atmosphere === data.config?.ambient?.atmosphere; button.classList.toggle('active',selected); button.setAttribute('aria-pressed',String(selected)); });
      document.querySelectorAll('[data-tonal-source]').forEach(button => { const selected = button.dataset.tonalSource === data.config?.ambient?.tonalSource; button.classList.toggle('active',selected); button.setAttribute('aria-pressed',String(selected)); button.disabled = button.dataset.tonalSource === 'generated' && !(data.config?.ambient?.chordProgression || []).length; });
      document.querySelectorAll('[data-tonal-centre]').forEach(button => { const selected = Number(button.dataset.tonalCentre) === Number(data.config?.ambient?.manualRootMidi); button.classList.toggle('active',selected); button.setAttribute('aria-pressed',String(selected)); button.disabled = data.config?.ambient?.tonalSource === 'generated'; });
      document.querySelectorAll('[data-synth-parameter]').forEach(control => { const value = data.config?.ambient?.parameters?.[control.dataset.synthParameter]; if (Number.isFinite(Number(value))) { control.value = value; control.parentElement.style.setProperty('--value',value); const output = control.parentElement.querySelector('output'); if (output) output.textContent = value; } });
      document.querySelectorAll('[data-playback-toggle]').forEach(button => {
        button.classList.toggle('playing', data.playing);
        button.setAttribute('aria-pressed', String(data.playing));
        const label = button.querySelector('strong'); if (label) label.textContent = data.playing ? (button.closest('.binaural-card') ? 'Stop binaural' : 'Stop') : (button.closest('.binaural-card') ? 'Play binaural' : 'Play');
        const symbol = button.querySelector('.play-symbol'); if (symbol) symbol.textContent = data.playing ? '■' : '▶';
      });
      if (status) {
        const selected = binauralMode === 'difference' ? document.querySelector('[data-band].active') : null;
        const beat = Number(data.config?.beat || 6), carrier = Number(data.config?.carrier || data.config?.ambient?.droneFrequency || 200), left = binauralMode === 'individual' ? Number(data.config?.leftFrequency) : carrier - beat / 2, right = binauralMode === 'individual' ? Number(data.config?.rightFrequency) : carrier + beat / 2, displayFrequency = value => Number.isInteger(value) ? value : value.toFixed(1);
        status.textContent = `${data.playing ? 'Playing' : 'Ready'} · ${binauralMode === 'individual' ? 'Individual tuning' : `${selected?.querySelector('strong')?.textContent || 'Binaural'} ${beat} Hz`} · ${displayFrequency(left)} / ${displayFrequency(right)} Hz`;
      }
      document.querySelectorAll('[data-ambient-toggle]').forEach(button => {
        button.classList.toggle('playing', data.ambientPlaying);
        button.setAttribute('aria-pressed', String(data.ambientPlaying));
        const label = button.querySelector('strong'); if (label) label.textContent = data.ambientPlaying ? 'Stop ambient' : 'Generate ambient';
        const symbol = button.querySelector('.play-symbol'); if (symbol) symbol.textContent = data.ambientPlaying ? '■' : '✦';
      });
      const ambientStatus = document.querySelector('[data-ambient-status]'); if (ambientStatus) ambientStatus.textContent = data.ambientPlaying ? 'Evolving soundscape playing' : 'Soundscape ready';
      document.querySelectorAll('[data-drone-frequency]').forEach(control => { if (data.config?.ambient?.droneFrequency == null) return; control.value = data.config.ambient.droneFrequency; const output = control.parentElement.querySelector('output'); if (output) output.textContent = data.config.ambient.droneFrequency + ' Hz'; });
      document.querySelectorAll('[data-chord-transition]').forEach(control => { const value = data.config?.ambient?.chordTransition; if (!Number.isFinite(Number(value))) return; control.value = value; const output = control.parentElement.querySelector('output'); if (output) output.textContent = transitionLabel(value); });
      document.querySelectorAll('[data-binaural-chord-transition]').forEach(control => { const value = data.config?.ambient?.binauralChordTransition; if (!Number.isFinite(Number(value))) return; control.value = value; const output = control.parentElement.querySelector('output'); if (output) output.textContent = transitionLabel(value); });
      document.querySelectorAll('[data-noise-toggle]').forEach(button => { button.classList.toggle('playing', data.noisePlaying); button.setAttribute('aria-pressed', String(data.noisePlaying)); const label = button.querySelector('strong'); if (label) label.textContent = data.noisePlaying ? 'Stop noise' : 'Play noise'; const symbol = button.querySelector('.play-symbol'); if (symbol) symbol.textContent = data.noisePlaying ? '■' : '≈'; });
      const selectedNoise = document.querySelector('[data-noise].active'); const noiseStatus = document.querySelector('[data-noise-status]'); if (noiseStatus) noiseStatus.textContent = `${selectedNoise?.querySelector('strong')?.textContent || data.config?.noise || 'Pink'} noise ${data.noisePlaying ? 'playing' : 'ready'}`;
      document.querySelectorAll('[data-volume]').forEach(control => { const value = data.config?.volumes?.[control.dataset.volume]; if (Number.isFinite(Number(value))) { control.value = value; const output = control.parentElement.querySelector('output'); if (output) output.textContent = value; } });
      document.querySelectorAll('[data-chord-card]').forEach(card => {
        const chords = data.config?.ambient?.chordProgression || [], current = Number(data.chordProgressionIndex || 0);
        card.querySelectorAll('[data-chord-item]').forEach((item, index) => item.classList.toggle('active', Boolean(data.chordProgressionPlaying) && index === current));
        const play = card.querySelector('[data-chord-play]'); if (play) { play.disabled = !chords.length; play.textContent = data.chordProgressionPlaying && !data.chordProgressionPaused ? 'Pause' : 'Play'; }
      });
    } else if (data.type === 'resona:chord-pipeline') {
      document.querySelectorAll('[data-chord-card]').forEach(card => {
        const toggle = card.querySelector('[data-chord-continuous]'); if (toggle) toggle.checked = Boolean(data.enabled);
        const pipeline = card.querySelector('[data-chord-pipeline]'); if (!pipeline) return; pipeline.hidden = !data.enabled;
        const playing = pipeline.querySelector('[data-chord-pipeline-playing]'), ready = pipeline.querySelector('[data-chord-pipeline-ready]'), generating = pipeline.querySelector('[data-chord-pipeline-generating]');
        if (playing) playing.textContent = data.playing ? `Set ${data.playing}` : '—';
        if (ready) ready.textContent = data.ready?.length ? data.ready.map(label => `Set ${label}`).join(', ') : '—';
        if (generating) generating.textContent = data.generating ? `Set ${data.generating}` : '—';
        if (data.enabled) card.querySelector('[data-chord-status]').textContent = data.playing ? `Set ${data.playing} playing · ${data.ready?.length ? `Set ${data.ready.join(', ')} ready` : 'building ready set'}${data.generating ? ` · Set ${data.generating} generating` : ''}` : 'Continuous mode ready · generate to begin';
      });
    } else if (data.type === 'resona:chord-result' && Array.isArray(data.chords)) {
      document.querySelectorAll('[data-chord-card]').forEach(card => { const list = card.querySelector('[data-chord-results]'); list.replaceChildren(...data.chords.map((chord, index) => { const item = document.createElement('span'); item.dataset.chordItem = ''; item.textContent = chord.replace(/([A-G])s/g, '$1♯'); item.title = `Chord ${index + 1}`; return item; })); card.querySelector('[data-chord-status]').textContent = `${data.chords.length} chords generated locally · ${data.duration}s each`; card.querySelector('[data-chord-generate]').disabled = false; card.querySelector('[data-chord-play]').disabled = false; card.querySelector('[data-chord-replay]').disabled = false; card.querySelector('[data-chord-stop]').disabled = false; });
    } else if (data.type === 'resona:chord-status') {
      document.querySelectorAll('[data-chord-card]').forEach(card => { card.querySelector('[data-chord-status]').textContent = data.message || ''; if (data.status === 'error') card.querySelector('[data-chord-generate]').disabled = false; });
    } else if (data.type === 'resona:profile' && data.profile) {
      document.querySelectorAll('[data-profile="username"]').forEach(element => element.textContent = data.profile.username);
      document.querySelectorAll('[data-profile="storage"]').forEach(element => element.textContent = `${(data.profile.storage_used / 1048576).toFixed(1)} MB`);
    } else if (data.type === 'resona:history' && Array.isArray(data.history)) {
      const list = document.querySelector('#history-list');
      if (list && data.history.length) {
        list.replaceChildren(...data.history.map(item => {
          const card = document.createElement('div'); card.className = 'card';
          const title = document.createElement('strong'); title.textContent = item.title;
          const detail = document.createElement('span'); detail.textContent = `${item.created_at.slice(0,16).replace('T',' ')} · ${item.duration_seconds || 0}s`;
          card.append(title, detail); return card;
        }));
      }
    }
  });
  if (document.querySelector('[data-profile]')) send('resona:request', { resource:'profile' });
  if (document.querySelector('#history-list')) send('resona:request', { resource:'history' });
})();
</script>'''
LINK_TAG = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
HREF_ATTRIBUTE = re.compile(r"\bhref\s*=\s*(['\"])(.*?)\1", re.IGNORECASE)
REL_ATTRIBUTE = re.compile(r"\brel\s*=\s*(['\"])(.*?)\1", re.IGNORECASE)


def inline_user_stylesheets(username, asset_path, content):
    def replace_link(match):
        tag = match.group(0)
        href_match = HREF_ATTRIBUTE.search(tag)
        rel_match = REL_ATTRIBUTE.search(tag)
        if not href_match or not rel_match or "stylesheet" not in rel_match.group(2).lower().split():
            return tag
        href = href_match.group(2).strip()
        if "://" in href or href.startswith(("//", "/")):
            return "<!-- External stylesheet omitted by Resona sandbox -->"
        relative = posixpath.normpath(posixpath.join(posixpath.dirname(asset_path), href))
        if relative.startswith("../") or relative == "..":
            return "<!-- Unsafe stylesheet path omitted by Resona sandbox -->"
        try:
            stylesheet = safe_path(username, relative)
        except ValueError:
            return "<!-- Unsafe stylesheet path omitted by Resona sandbox -->"
        if stylesheet.suffix.lower() != ".css" or not stylesheet.is_file() or stylesheet.is_symlink():
            return "<!-- Missing stylesheet omitted by Resona sandbox -->"
        css = stylesheet.read_text(encoding="utf-8").replace("</style", "<\\/style")
        return f'<style data-resona-stylesheet="{relative}">{css}</style>'

    return LINK_TAG.sub(replace_link, content)


@storage_bp.get("/<username>/<path:asset_path>")
@login_required
def asset(username, asset_path):
    if g.user["username"] != username and not g.user["is_admin"]:
        abort(403)
    try:
        path = safe_path(username, asset_path)
    except ValueError:
        abort(404)
    if not path.is_file() or path.is_symlink():
        abort(404)
    mime, _ = mimetypes.guess_type(path.name)
    if path.suffix.lower() == ".html":
        content = path.read_text(encoding="utf-8")
        content = inline_user_stylesheets(username, asset_path, content)
        if "data-resona-bridge" not in content:
            content = content.replace("</body>", PAGE_BRIDGE + "\n</body>") if "</body>" in content else content + PAGE_BRIDGE
        response = make_response(content)
        response.mimetype = "text/html"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'none'; frame-src 'none'; "
            "object-src 'none'; base-uri 'none'; form-action 'none'"
        )
    else:
        response = send_file(path, mimetype=mime or "application/octet-stream", conditional=True)
    response.headers["Cache-Control"] = "private, no-store"
    return response
