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
  const status = document.querySelector('[data-playback-status]');
  document.querySelectorAll('[data-band]').forEach(button => button.addEventListener('click', () => {
    document.querySelectorAll('[data-band]').forEach(option => {
      const selected = option === button;
      option.classList.toggle('active', selected);
      option.setAttribute('aria-pressed', String(selected));
    });
    send('resona:audio', { action:'setBeat', value:button.dataset.band });
  }));
  document.querySelectorAll('[data-playback-toggle]').forEach(button => button.addEventListener('click', () => send('resona:audio', { action:'toggle' })));
  document.querySelectorAll('[data-noise]').forEach(button => button.addEventListener('click', () => { document.querySelectorAll('[data-noise]').forEach(option => { const selected = option === button; option.classList.toggle('active', selected); option.setAttribute('aria-pressed', String(selected)); }); send('resona:audio', { action:'setNoise', value:button.dataset.noise }); }));
  document.querySelectorAll('[data-noise-toggle]').forEach(button => button.addEventListener('click', () => send('resona:audio', { action:'toggleNoise' })));
  document.querySelectorAll('[data-audio]').forEach(control => control.addEventListener('input', () => send('resona:audio', { action:'setLayer', name:control.dataset.audio, value:control.value })));
  document.querySelectorAll('[data-volume]').forEach(control => control.addEventListener('input', () => { const output = control.parentElement.querySelector('output'); if (output) output.textContent = control.value; send('resona:audio', { action:'setVolume', name:control.dataset.volume, value:control.value }); }));
  document.querySelectorAll('[data-ambient]').forEach(control => control.addEventListener('input', () => { const output = control.parentElement.querySelector('output'); if (output) output.textContent = control.value; send('resona:audio', { action:'setAmbient', name:control.dataset.ambient, value:control.value }); }));
  document.querySelectorAll('[data-drone-frequency]').forEach(control => control.addEventListener('input', () => { const output = control.parentElement.querySelector('output'); if (output) output.textContent = control.value + ' Hz'; send('resona:audio', { action:'setDroneFrequency', value:control.value }); }));
  const transitionLabel = value => Number(value) <= 0 ? 'Instant' : `${Number(value).toFixed(1)} s`;
  document.querySelectorAll('[data-chord-transition]').forEach(control => control.addEventListener('input', () => { const output = control.parentElement.querySelector('output'); if (output) output.textContent = transitionLabel(control.value); send('resona:audio', { action:'setChordTransition', value:control.value }); }));
  document.querySelectorAll('[data-binaural-chord-transition]').forEach(control => control.addEventListener('input', () => { const output = control.parentElement.querySelector('output'); if (output) output.textContent = transitionLabel(control.value); send('resona:audio', { action:'setBinauralChordTransition', value:control.value }); }));
  document.querySelectorAll('[data-chord-range]').forEach(control => control.addEventListener('input', () => { const output = control.parentElement.querySelector('output'); if (output) output.textContent = control.value; }));
  document.querySelectorAll('[data-chord-continuous]').forEach(control => control.addEventListener('change', () => send('resona:audio', { action:'setContinuousChordMode', enabled:control.checked })));
  document.querySelectorAll('[data-ambient-toggle]').forEach(button => button.addEventListener('click', () => send('resona:audio', { action:'toggleAmbient' })));
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
    if (data.type === 'resona:audio-state') {
      document.querySelectorAll('[data-playback-toggle]').forEach(button => {
        button.classList.toggle('playing', data.playing);
        button.setAttribute('aria-pressed', String(data.playing));
        const label = button.querySelector('strong'); if (label) label.textContent = data.playing ? (button.closest('.binaural-card') ? 'Stop binaural' : 'Stop') : (button.closest('.binaural-card') ? 'Play binaural' : 'Play');
        const symbol = button.querySelector('.play-symbol'); if (symbol) symbol.textContent = data.playing ? '■' : '▶';
      });
      if (status) {
        const selected = document.querySelector('[data-band].active');
        const beat = Number(selected?.dataset.band || data.config?.beat || 6), carrier = Number(data.config?.carrier || data.config?.ambient?.droneFrequency || 200), left = carrier - beat / 2, right = carrier + beat / 2, displayFrequency = value => Number.isInteger(value) ? value : value.toFixed(1);
        status.textContent = `${data.playing ? 'Playing' : 'Ready'} · ${selected?.querySelector('strong')?.textContent || 'Binaural'} ${beat} Hz · ${displayFrequency(left)} / ${displayFrequency(right)} Hz`;
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
