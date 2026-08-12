import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from flask import current_app


ALLOWED_EXTENSIONS = {".html", ".css", ".js", ".json", ".md", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".mp3", ".wav", ".ogg"}
PROTECTED_FILES = {"nav.json"}
CHORD_MODEL_ASSETS = Path(__file__).parent / "default_assets" / "chord-model"
FORBIDDEN_JS = re.compile(
    r"(?:window\s*\.\s*(?:top|parent)|parent\s*\.|top\s*\.|localStorage|sessionStorage|\bdocument\b|eval\s*\(|new\s+Function)",
    re.IGNORECASE,
)
FORBIDDEN_HTML = re.compile(r"<(?:iframe|object|embed|base|form)\b|\son\w+\s*=", re.IGNORECASE)
SCRIPT_TAG = re.compile(r"<script\b([^>]*)>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL)


def storage_root():
    root = Path(current_app.config["STORAGE_ROOT"])
    if not root.is_absolute():
        root = Path(current_app.root_path).parent / root
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def user_root(username):
    return (storage_root() / username).resolve()


def rename_user_storage(old_username, new_username):
    source = user_root(old_username)
    destination = user_root(new_username)
    if source == destination:
        return
    if destination.exists():
        raise FileExistsError("The destination user workspace already exists")
    if source.exists():
        source.rename(destination)


def delete_user_storage(username):
    root = user_root(username)
    if root.exists():
        shutil.rmtree(root)


def safe_path(username, relative):
    clean = PurePosixPath(str(relative).replace("\\", "/"))
    if clean.is_absolute() or ".." in clean.parts or not clean.parts:
        raise ValueError("Unsafe storage path")
    candidate = (user_root(username) / Path(*clean.parts)).resolve()
    if user_root(username) not in candidate.parents:
        raise ValueError("Path leaves user storage")
    return candidate


def usage_bytes(username):
    root = user_root(username)
    if not root.exists():
        return 0
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file() and not path.is_symlink())


def ensure_chord_model_assets(username):
    destination = user_root(username) / "static" / "chord-model"
    missing = [source for source in CHORD_MODEL_ASSETS.iterdir() if source.is_file() and not (destination / source.name).is_file()]
    additional_bytes = sum(source.stat().st_size for source in missing)
    if usage_bytes(username) + additional_bytes > int(current_app.config["USER_QUOTA_BYTES"]):
        raise ValueError("The private chord model would exceed this user's storage quota")
    destination.mkdir(parents=True, exist_ok=True)
    for source in missing:
        shutil.copy2(source, destination / source.name)


def validate_content(relative, content):
    suffix = Path(relative).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError(f"File type {suffix or '(none)'} is not allowed")
    if "\x00" in content:
        raise ValueError("Binary content is not accepted here")
    if suffix == ".html":
        if FORBIDDEN_HTML.search(content):
            raise ValueError("Generated HTML cannot contain iframes, objects, embeds, base tags, forms, or inline event-handler attributes")
        script_openings = len(re.findall(r"<script\b", content, re.IGNORECASE))
        script_closings = len(re.findall(r"</script\s*>", content, re.IGNORECASE))
        scripts = SCRIPT_TAG.findall(content)
        if script_openings != script_closings or script_openings != len(scripts):
            raise ValueError("Every inline script must have a complete closing </script> tag")
        for attributes, _script_body in scripts:
            if re.search(r"\bsrc\s*=", attributes, re.IGNORECASE):
                raise ValueError("Page scripts must be inline; external script sources are not allowed in user HTML")
        lowered = content.strip().lower()
        if len(content.encode("utf-8")) < 200:
            raise ValueError("Generated pages must be complete HTML interfaces, not short text or placeholders")
        if not all(tag in lowered for tag in ("<html", "<body", "</html>")):
            raise ValueError("Generated pages must contain complete html and body elements")
        if not any(tag in lowered for tag in ("<main", "<section", "<article")):
            raise ValueError("Generated pages must contain a semantic interface container")
        if "rel=\"stylesheet\"" not in lowered and "rel='stylesheet'" not in lowered and "<style" not in lowered:
            raise ValueError("Generated pages must load a stylesheet or include a style block")
    if suffix == ".js" and FORBIDDEN_JS.search(content):
        raise ValueError("Generated JavaScript attempts to escape its sandbox")
    if suffix == ".json":
        json.loads(content)


def write_user_file(username, relative, content):
    validate_content(relative, content)
    path = safe_path(username, relative)
    old_size = path.stat().st_size if path.exists() else 0
    projected = usage_bytes(username) - old_size + len(content.encode("utf-8"))
    if projected > int(current_app.config["USER_QUOTA_BYTES"]):
        raise ValueError("This change would exceed the 1 GB user quota")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_user_bytes(username, relative, content):
    suffix = Path(relative).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".mp3", ".wav", ".ogg"}:
        raise ValueError("Binary skill output must be an allowed image or audio file")
    path = safe_path(username, relative)
    old_size = path.stat().st_size if path.exists() else 0
    projected = usage_bytes(username) - old_size + len(content)
    if projected > int(current_app.config["USER_QUOTA_BYTES"]):
        raise ValueError("This skill output would exceed the 1 GB user quota")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def validate_nav(nav):
    if not isinstance(nav, dict) or not isinstance(nav.get("nav_items"), list):
        raise ValueError("nav.json must contain a nav_items list")
    default_page = nav.get("default_page", "")
    safe_path("validation", default_page)
    ids = set()
    for item in nav["nav_items"]:
        if not isinstance(item, dict) or not all(item.get(k) for k in ("id", "label", "target_html")):
            raise ValueError("Each navigation item needs id, label, and target_html")
        if item["id"] in ids:
            raise ValueError("Navigation item ids must be unique")
        ids.add(item["id"])
        safe_path("validation", item["target_html"])
        if item.get("icon_path"):
            icon_path = item["icon_path"]
            if icon_path.startswith("/storage/"):
                icon_path = "/".join(icon_path.split("/")[3:])
            safe_path("validation", icon_path)
    return nav


def create_snapshot(username):
    root = user_root(username)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = root / "snapshots" / stamp
    destination.mkdir(parents=True, exist_ok=False)
    for item in root.iterdir():
        if item.name == "snapshots":
            continue
        if item.is_dir():
            shutil.copytree(item, destination / item.name, ignore=shutil.ignore_patterns("chord-model") if item.name == "static" else None)
        elif item.is_file():
            shutil.copy2(item, destination / item.name)
    return stamp


def restore_snapshot(username, snapshot_id):
    if not re.fullmatch(r"[0-9TZ]+", snapshot_id):
        raise ValueError("Invalid snapshot")
    root = user_root(username)
    source = root / "snapshots" / snapshot_id
    if not source.is_dir():
        raise FileNotFoundError(snapshot_id)
    for item in list(root.iterdir()):
        if item.name == "snapshots":
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()
    for item in source.iterdir():
        if item.is_dir():
            shutil.copytree(item, root / item.name)
        else:
            shutil.copy2(item, root / item.name)


def default_page(title, eyebrow, body, controls=""):
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="../static/user.css"></head><body>
<main class="page"><p class="eyebrow">{eyebrow}</p><h1>{title}</h1><p class="intro">{body}</p>{controls}</main>
</body></html>'''


NAV_ICON_PATHS = {
    "home": "M3 12h3l2-6 4 12 3-9 2 3h4",
    "advanced": "M8 3v2m0 8v2M2 9h2m8 0h2M3.76 4.76l1.42 1.42m5.64 5.64 1.42 1.42m0-8.48-1.42 1.42m-5.64 5.64-1.42 1.42M8 6a3 3 0 1 0 0 6 3 3 0 0 0 0-6zm8 5v1.5m0 7V21m-5-5h1.5m7 0H21m-8.54-3.54 1.06 1.06m4.96 4.96 1.06 1.06m0-7.08-1.06 1.06m-4.96 4.96-1.06 1.06M16 13.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z",
}


def default_navigation():
    return {
        "default_page": "pages/home.html",
        "nav_items": [
            {"id": "home", "label": "Home", "icon": "waves", "icon_path": "static/icons/home.svg", "target_html": "pages/home.html"},
            {"id": "advanced", "label": "Advanced", "icon": "gears", "icon_path": "static/icons/advanced.svg", "target_html": "pages/advanced.html"},
        ],
    }


def _single_home_v2_page():
    controls = '''<section class="binaural-panel" aria-labelledby="band-title">
  <div class="panel-heading"><p>Choose a state</p><h2 id="band-title">Binaural beats</h2></div>
  <div class="band-grid">
    <button class="band-button" data-band="2" aria-pressed="false"><strong>Delta</strong><span>2 Hz · Deep sleep</span></button>
    <button class="band-button active" data-band="6" aria-pressed="true"><strong>Theta</strong><span>6 Hz · Meditation</span></button>
    <button class="band-button" data-band="10" aria-pressed="false"><strong>Alpha</strong><span>10 Hz · Calm focus</span></button>
    <button class="band-button" data-band="18" aria-pressed="false"><strong>Beta</strong><span>18 Hz · Clear energy</span></button>
    <button class="band-button" data-band="36" aria-pressed="false"><strong>Gamma</strong><span>36 Hz · Insight</span></button>
  </div>
</section>
<section class="playback-control" aria-label="Binaural playback">
  <p data-playback-status>Ready · Theta 6 Hz</p>
  <button class="play-toggle" data-playback-toggle aria-pressed="false"><span class="play-symbol">▶</span><strong>Play</strong></button>
  <small>Headphones recommended</small>
</section>'''
    return default_page("Find your frequency", "Binaural healing", "Select a mental state, then play a continuous binaural soundscape.", controls)


def _single_home_v3_page():
    controls = '''<div class="generator-grid">
<section class="sound-card binaural-card" aria-labelledby="band-title">
  <div class="card-heading"><div><p>Binaural engine</p><h2 id="band-title">Binaural beats</h2></div><span class="card-badge">Headphones</span></div>
  <p class="card-intro">Choose a neural rhythm and let a precise frequency difference settle around you.</p>
  <div class="band-grid">
    <button class="band-button" data-band="2" aria-pressed="false"><strong>Delta</strong><span>2 Hz · Deep sleep</span></button>
    <button class="band-button active" data-band="6" aria-pressed="true"><strong>Theta</strong><span>6 Hz · Meditation</span></button>
    <button class="band-button" data-band="10" aria-pressed="false"><strong>Alpha</strong><span>10 Hz · Calm focus</span></button>
    <button class="band-button" data-band="18" aria-pressed="false"><strong>Beta</strong><span>18 Hz · Clear energy</span></button>
    <button class="band-button" data-band="36" aria-pressed="false"><strong>Gamma</strong><span>36 Hz · Insight</span></button>
  </div>
  <div class="card-transport" aria-label="Binaural playback">
    <p data-playback-status>Ready · Theta 6 Hz</p>
    <button class="play-toggle" data-playback-toggle aria-pressed="false"><span class="play-symbol">▶</span><strong>Play binaural</strong></button>
  </div>
</section>
<section class="sound-card ambient-card" aria-labelledby="ambient-title">
  <div class="card-heading"><div><p>Procedural soundscape</p><h2 id="ambient-title">Ambient music generator</h2></div><span class="card-badge live-badge">Live</span></div>
  <p class="card-intro">Blend five evolving layers into a spacious composition that never loops the same way twice.</p>
  <div class="ambient-controls">
    <label><span><strong>Drone</strong><small>Low, grounding foundation</small></span><output>48</output><input type="range" min="0" max="100" value="48" data-ambient="drone"></label>
    <label><span><strong>Lush pads</strong><small>Warm harmonic bloom</small></span><output>64</output><input type="range" min="0" max="100" value="64" data-ambient="pads"></label>
    <label><span><strong>Textures &amp; foley</strong><small>Air, grain and organic detail</small></span><output>32</output><input type="range" min="0" max="100" value="32" data-ambient="textures"></label>
    <label><span><strong>Minimal melodic elements</strong><small>Sparse, glimmering tones</small></span><output>24</output><input type="range" min="0" max="100" value="24" data-ambient="melody"></label>
    <label><span><strong>Spatial effects</strong><small>Width, drift and echo</small></span><output>46</output><input type="range" min="0" max="100" value="46" data-ambient="spatial"></label>
  </div>
  <div class="card-transport ambient-transport" aria-label="Ambient playback">
    <p data-ambient-status>Soundscape ready</p>
    <button class="play-toggle ambient-toggle" data-ambient-toggle aria-pressed="false"><span class="play-symbol">✦</span><strong>Generate ambient</strong></button>
  </div>
</section>
</div>'''
    return default_page("Shape your soundscape", "Generative healing audio", "Tune focused binaural rhythms or build a living ambient composition from layered sound.", controls)


def _single_home_v4_page():
    volume_card = '''<section class="volume-mixer-card" aria-labelledby="volume-mixer-title">
  <div class="card-heading"><div><p>Output mixer</p><h2 id="volume-mixer-title">Master volume</h2></div><span class="card-badge">Live mix</span></div>
  <p class="card-intro">Balance the two generators independently while they play together or on their own.</p>
  <div class="master-volume-grid">
    <label><span><strong>Binaural beats</strong><small>Overall binaural output</small></span><output>50</output><input type="range" min="0" max="100" value="50" data-volume="binaural"></label>
    <label><span><strong>Ambient music</strong><small>Overall ambient output</small></span><output>50</output><input type="range" min="0" max="100" value="50" data-volume="ambient"></label>
  </div>
</section>'''
    return _single_home_v3_page().replace("</main>", volume_card + "</main>")


def _single_home_v5_page():
    noise_card = '''<section class="noise-generator-card" aria-labelledby="noise-generator-title">
  <div class="card-heading"><div><p>Continuous texture</p><h2 id="noise-generator-title">Noise generator</h2></div><span class="card-badge noise-badge">6 textures</span></div>
  <p class="card-intro">Choose a steady noise colour or an environmental texture to soften distractions and fill the room.</p>
  <div class="noise-card-layout">
    <div class="noise-type-grid" role="group" aria-label="Noise type">
      <button data-noise="white" aria-pressed="false"><strong>White</strong><span>Bright · Full spectrum</span></button>
      <button class="active" data-noise="pink" aria-pressed="true"><strong>Pink</strong><span>Balanced · Soft</span></button>
      <button data-noise="brown" aria-pressed="false"><strong>Brown</strong><span>Deep · Grounding</span></button>
      <button data-noise="rain" aria-pressed="false"><strong>Rain</strong><span>Fine · Restorative</span></button>
      <button data-noise="ocean" aria-pressed="false"><strong>Ocean</strong><span>Slow · Rhythmic</span></button>
      <button data-noise="forest" aria-pressed="false"><strong>Forest</strong><span>Organic · Airy</span></button>
    </div>
    <div class="noise-transport" aria-label="Noise playback">
      <p data-noise-status>Pink noise ready</p>
      <button class="play-toggle noise-toggle" data-noise-toggle aria-pressed="false"><span class="play-symbol">≈</span><strong>Play noise</strong></button>
    </div>
  </div>
</section>'''
    page = _single_home_v4_page().replace('<section class="volume-mixer-card"', noise_card + '<section class="volume-mixer-card"')
    noise_volume = '    <label><span><strong>Noise generator</strong><small>Overall noise output</small></span><output>50</output><input type="range" min="0" max="100" value="50" data-volume="noise"></label>\n'
    return page.replace("  </div>\n</section></main>", noise_volume + "  </div>\n</section></main>")


def _single_home_v6_page():
    frequency_control = '    <label class="drone-frequency-control"><span><strong>Drone frequency</strong><small>Harmonic root for drone, pads and melody</small></span><output>200 Hz</output><input type="range" min="40" max="400" step="1" value="200" data-drone-frequency></label>\n'
    page = _single_home_v5_page().replace('  <div class="ambient-controls">\n', '  <div class="ambient-controls">\n' + frequency_control, 1)
    return page.replace(
        "Blend five evolving layers into a spacious composition that never loops the same way twice.",
        "Set a tonal anchor, then blend five evolving layers. Pads and melody follow the root harmonically; textures stay broadband and spatial effects stay pitchless.",
    )


def _single_home_v7_page():
    return _single_home_v6_page().replace(
        "Harmonic root for drone, pads and melody",
        "Shared harmonic root and binaural carrier",
    ).replace(
        "Set a tonal anchor, then blend five evolving layers. Pads and melody follow the root harmonically; textures stay broadband and spatial effects stay pitchless.",
        "Set the shared tonal anchor for the ambient harmony and binaural carrier. Pads and melody follow it; textures stay broadband and spatial effects stay pitchless.",
    )


def _single_home_v8_page():
    chord_card = '''  <section class="chord-subcard" data-chord-card aria-labelledby="chord-progression-title">
    <div class="chord-subcard-heading"><div><p>Private on-device model</p><h3 id="chord-progression-title">Chord progression</h3></div><span>Browser AI</span></div>
    <p class="chord-help">Generate a progression that retunes the lush pads. Model inference stays in this browser; results can vary.</p>
    <label class="chord-seed"><span>Starting chords <small>Optional · C, Amin, F, G</small></span><input data-chord-seed placeholder="C Amin F G" autocomplete="off"></label>
    <div class="chord-settings">
      <label><span>Progression length</span><output>8</output><input type="range" min="2" max="32" step="1" value="8" data-chord-length data-chord-range></label>
      <label><span>Seconds per chord</span><output>4</output><input type="range" min="1" max="12" step="0.5" value="4" data-chord-duration data-chord-range></label>
      <label><span>Creativity</span><output>1</output><input type="range" min="0.1" max="2" step="0.1" value="1" data-chord-temperature data-chord-range></label>
      <label><span>Top choices</span><output>10</output><input type="range" min="1" max="100" step="1" value="10" data-chord-top-k data-chord-range></label>
    </div>
    <label class="chord-greedy"><input type="checkbox" data-chord-greedy><span>Predictable mode <small>Always choose the highest-scoring chord</small></span></label>
    <div class="chord-results" data-chord-results aria-live="polite"><span class="empty">Your generated progression will appear here.</span></div>
    <div class="chord-actions"><button class="chord-generate" data-chord-generate>Generate &amp; apply</button><button data-chord-play disabled>Play</button><button data-chord-stop disabled>Stop</button><button data-chord-replay disabled>Replay</button></div>
    <p class="chord-status" data-chord-status>Model loads only when you generate.</p>
  </section>
'''
    marker = '  <div class="card-transport ambient-transport" aria-label="Ambient playback">'
    page = _single_home_v7_page()
    return page.replace(marker, chord_card + marker, 1)


def _single_home_v9_page():
    return _single_home_v8_page().replace(
        "Generate a progression that retunes the lush pads. Model inference stays in this browser; results can vary.",
        "Generate a harmonized progression for the lush pads and melodic tones. Complex predictions are simplified into one coherent key.",
    )


def _single_home_v10_page():
    continuous_control = '''    <label class="chord-continuous"><input type="checkbox" data-chord-continuous><span>Continuous sets <small>Keep one set ready while the following set generates</small></span></label>
    <div class="chord-pipeline" data-chord-pipeline hidden aria-live="polite">
      <span><small>Playing</small><strong data-chord-pipeline-playing>—</strong></span>
      <span><small>Ready</small><strong data-chord-pipeline-ready>—</strong></span>
      <span><small>Generating</small><strong data-chord-pipeline-generating>—</strong></span>
    </div>
'''
    marker = '    <div class="chord-results" data-chord-results aria-live="polite">'
    return _single_home_v9_page().replace(marker, continuous_control + marker, 1)


def _single_home_v11_page():
    return _single_home_v10_page().replace(
        "Shared harmonic root and binaural carrier",
        "Base root for harmony and binaural chords",
    ).replace(
        "Set the shared tonal anchor for the ambient harmony and binaural carrier. Pads and melody follow it; textures stay broadband and spatial effects stay pitchless.",
        "Set the base tonal anchor. Pads, melody, and the binaural carrier follow each chord; textures stay broadband and spatial effects stay pitchless.",
    ).replace(
        "Generate a harmonized progression for the lush pads and melodic tones. Complex predictions are simplified into one coherent key.",
        "Generate a harmonized progression for the lush pads, melodic tones, and binaural carrier. Complex predictions are simplified into one coherent key.",
    )


def _single_home_v12_page():
    transition_card = '''  <section class="binaural-transition-subcard" aria-labelledby="binaural-transition-title">
    <div class="transition-subcard-heading"><div><p>Progression response</p><h3 id="binaural-transition-title">Chord transition timing</h3></div><span>Per chord</span></div>
    <p>Shape how quickly the pitched layers move when the chord progression advances. Manual drone tuning keeps its own smooth response.</p>
    <div class="transition-controls">
      <label><span><strong>Ambient chords</strong><small>Lush pads and melodic tones</small></span><output>Instant</output><input type="range" min="0" max="4" step="0.1" value="0" data-chord-transition></label>
      <label><span><strong>Binaural beats</strong><small>Left and right carrier frequencies</small></span><output>Instant</output><input type="range" min="0" max="4" step="0.1" value="0" data-binaural-chord-transition></label>
    </div>
  </section>
'''
    marker = '  <div class="card-transport" aria-label="Binaural playback">'
    return _single_home_v11_page().replace(marker, transition_card + marker, 1)


def default_advanced_page():
    return _single_home_v12_page().replace(
        'min="1" max="12" step="0.5" value="4" data-chord-duration',
        'min="2" max="120" step="1" value="4" data-chord-duration',
    )


def default_home_page():
    controls = '''<div class="simple-home-grid">
<section class="session-card" aria-labelledby="session-title">
  <div class="session-copy"><p>Now playing</p><h2 id="session-title">Your soundscape</h2><span>Headphones recommended</span></div>
  <button class="session-toggle" data-session-toggle aria-pressed="false"><span class="session-button-icon" aria-hidden="true"></span><strong>Play</strong></button>
  <section class="master-volume-subcard" aria-labelledby="simple-volume-title">
    <div><span><strong id="simple-volume-title">Master volume</strong><small>All sound layers</small></span><output>70%</output></div>
    <input type="range" min="0" max="100" value="70" aria-label="Master volume" data-master-volume>
  </section>
</section>
<section class="mode-card" aria-labelledby="mode-title">
  <div class="simple-card-heading"><p>Choose a mode</p><h2 id="mode-title">How do you want to feel?</h2></div>
  <div class="mode-grid" role="group" aria-label="Soundscape mode">
    <button data-mode="sleep" aria-pressed="false"><span class="mode-icon">☾</span><span><strong>Sleep</strong><small>Deep &amp; quiet</small></span></button>
    <button class="active" data-mode="meditation" aria-pressed="true"><span class="mode-icon">◌</span><span><strong>Meditation</strong><small>Slow &amp; spacious</small></span></button>
    <button data-mode="focus" aria-pressed="false"><span class="mode-icon">◇</span><span><strong>Focus</strong><small>Calm &amp; clear</small></span></button>
    <button data-mode="awake" aria-pressed="false"><span class="mode-icon">✦</span><span><strong>Awake</strong><small>Bright &amp; active</small></span></button>
  </div>
  <p class="mode-note">Each mode tunes your binaural beat and ambient layers. Fine-tune everything in Advanced.</p>
</section>
</div>'''
    return default_page("A calmer way to listen", "Resona", "Choose a mode, press play, and let the sound adapt around you.", controls)


SIMPLE_HOME_STYLES = '''
/* simplified-home-v1 */
.simple-home-grid{display:grid;grid-template-columns:minmax(0,1.08fr) minmax(320px,.92fr);gap:18px;margin-top:clamp(34px,6vw,64px);align-items:stretch}.session-card,.mode-card{position:relative;overflow:hidden;border:1px solid rgba(255,255,255,.09);border-radius:32px;box-shadow:0 26px 74px rgba(0,0,0,.24)}.session-card{display:grid;grid-template-columns:1fr auto;grid-template-rows:1fr auto;gap:28px;min-height:460px;padding:clamp(26px,4vw,42px);background:radial-gradient(circle at 82% 17%,rgba(185,230,140,.13),transparent 32%),linear-gradient(145deg,#20271d,#141914)}.session-copy p,.simple-card-heading p{margin:0;color:#91a084;font-size:9px;font-weight:800;letter-spacing:.17em;text-transform:uppercase}.session-copy h2,.simple-card-heading h2{margin:8px 0 0;font:400 clamp(29px,4vw,43px)/1.05 Georgia,serif;letter-spacing:-.035em}.session-copy span{display:block;margin-top:13px;color:#7e857a;font-size:10px}.session-toggle{align-self:center;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;width:150px;height:150px;border:0;border-radius:50%;background:#b9e68c;color:#15200f;box-shadow:0 0 0 14px rgba(185,230,140,.055),0 24px 55px rgba(0,0,0,.34);cursor:pointer;transition:transform .22s,background .22s}.session-toggle:hover{transform:scale(1.025)}.session-toggle.playing{background:#f2efe6}.session-toggle strong{font-size:11px;letter-spacing:.05em}.session-button-icon{width:0;height:0;margin-left:5px;border-top:12px solid transparent;border-bottom:12px solid transparent;border-left:18px solid currentColor}.session-toggle.playing .session-button-icon{width:18px;height:22px;margin:0;border:0;border-left:6px solid currentColor;border-right:6px solid currentColor}.master-volume-subcard{grid-column:1/-1;padding:18px 20px;border:1px solid rgba(255,255,255,.07);border-radius:21px;background:rgba(5,8,5,.28)}.master-volume-subcard>div{display:flex;align-items:center;justify-content:space-between;gap:16px}.master-volume-subcard span strong,.master-volume-subcard span small{display:block}.master-volume-subcard span strong{font-size:12px}.master-volume-subcard span small{margin-top:2px;color:#747c70;font-size:8px}.master-volume-subcard output{color:#c8eea3;font-size:11px;font-variant-numeric:tabular-nums}.master-volume-subcard input{width:100%;height:4px;margin-top:15px;accent-color:#b9e68c;cursor:pointer}.mode-card{min-height:460px;padding:clamp(26px,4vw,38px);background:radial-gradient(circle at 100% 0,rgba(184,216,244,.1),transparent 35%),linear-gradient(145deg,#1c2320,#141918)}.simple-card-heading h2{font-size:clamp(27px,3vw,36px)}.mode-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:27px}.mode-grid button{display:flex;align-items:center;gap:11px;min-height:84px;padding:14px;border:1px solid rgba(255,255,255,.07);border-radius:18px;background:rgba(5,8,7,.25);color:#f5f1e8;text-align:left;cursor:pointer;transition:.2s}.mode-grid button:hover,.mode-grid button.active{transform:translateY(-1px);border-color:#789763;background:#273121}.mode-grid button.active{box-shadow:inset 0 0 0 1px rgba(185,230,140,.16)}.mode-icon{display:grid;place-items:center;flex:none;width:32px;height:32px;border-radius:50%;background:rgba(255,255,255,.06);color:#b9e68c;font-size:15px}.mode-grid strong,.mode-grid small{display:block}.mode-grid strong{font-size:12px}.mode-grid small{margin-top:3px;color:#7d8579;font-size:8px}.mode-note{margin:21px 2px 0;color:#737d78;font-size:9px;line-height:1.6}@media(max-width:840px){.simple-home-grid{grid-template-columns:1fr}.session-card,.mode-card{min-height:auto}}@media(max-width:520px){.simple-home-grid{margin-top:28px}.session-card{grid-template-columns:1fr;gap:25px;padding:23px;border-radius:25px}.session-toggle{justify-self:center;width:132px;height:132px}.mode-card{padding:23px;border-radius:25px}.mode-grid{grid-template-columns:1fr;margin-top:22px}.mode-grid button{min-height:68px}}
'''


SINGLE_HOME_STYLES = '''
.page{width:min(1220px,100%);padding-top:clamp(28px,5vw,58px)}
.page>h1{font-size:clamp(44px,7vw,76px)}
.generator-grid{display:grid;grid-template-columns:minmax(0,.92fr) minmax(0,1.08fr);gap:18px;margin-top:clamp(32px,5vw,54px);align-items:stretch}
.sound-card{position:relative;overflow:hidden;display:flex;flex-direction:column;min-height:590px;padding:clamp(22px,3vw,34px);border:1px solid rgba(255,255,255,.09);border-radius:30px;background:linear-gradient(145deg,rgba(32,39,29,.96),rgba(18,22,17,.96));box-shadow:0 24px 70px rgba(0,0,0,.26)}
.sound-card:before{content:"";position:absolute;inset:0;pointer-events:none;background:radial-gradient(circle at 85% 0,rgba(185,230,140,.09),transparent 34%)}
.ambient-card:before{background:radial-gradient(circle at 90% 5%,rgba(151,190,228,.12),transparent 38%)}
.card-heading{position:relative;display:flex;align-items:flex-start;justify-content:space-between;gap:16px}.card-heading p{margin:0;color:#8f958a;font-size:9px;font-weight:800;letter-spacing:.17em;text-transform:uppercase}.card-heading h2{margin:6px 0 0;font:400 clamp(26px,3vw,38px)/1.05 Georgia,serif;letter-spacing:-.025em}.card-badge{flex:none;padding:5px 9px;border:1px solid rgba(185,230,140,.2);border-radius:999px;color:#b9e68c;font-size:8px;font-weight:800;letter-spacing:.11em;text-transform:uppercase}.live-badge{color:#b8d8f4;border-color:rgba(184,216,244,.22)}
.card-intro{position:relative;max-width:470px;margin:13px 0 24px;color:#92988e;font-size:12px}
.band-grid{position:relative;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.band-button{min-height:76px;padding:13px 15px;border:1px solid rgba(255,255,255,.08);border-radius:17px;background:rgba(8,11,8,.3);color:#f5f1e8;text-align:left;cursor:pointer}.band-button:last-child{grid-column:1/-1}.band-button strong,.band-button span{display:block}.band-button strong{font-size:15px}.band-button span{margin-top:4px;color:#858b81;font-size:9px}.band-button:hover,.band-button.active{transform:translateY(-1px);border-color:#76965f;background:#263120}.band-button.active strong{color:#c4ef99}
.card-transport{position:relative;display:flex;align-items:center;justify-content:space-between;gap:14px;margin-top:auto;padding-top:24px}.card-transport>p{margin:0;color:#8f958a;font-size:10px}.play-toggle{display:flex;align-items:center;justify-content:center;gap:9px;min-width:152px;height:48px;padding:0 18px;border:0;border-radius:999px;background:#b9e68c;color:#15200f;box-shadow:0 12px 30px rgba(0,0,0,.3);cursor:pointer}.play-toggle:hover{transform:translateY(-1px)}.play-toggle.playing{background:#f1eee5}.play-toggle strong{font-size:11px}.play-symbol{font-size:11px}
.ambient-controls{position:relative;display:grid;gap:9px}.ambient-controls label{display:grid;grid-template-columns:1fr auto;align-items:center;gap:2px 12px;padding:10px 13px;border:1px solid rgba(255,255,255,.065);border-radius:16px;background:rgba(8,11,8,.23)}.ambient-controls label>span strong,.ambient-controls label>span small{display:block}.ambient-controls label>span strong{font-size:12px}.ambient-controls label>span small{margin-top:1px;color:#737a71;font-size:8px}.ambient-controls output{color:#b8d8f4;font-size:9px;font-variant-numeric:tabular-nums}.ambient-controls input{grid-column:1/-1;width:100%;height:3px;margin:7px 0 2px;accent-color:#a9ccec;cursor:pointer}.ambient-toggle{background:#b8d8f4;color:#10202c}.ambient-toggle.playing{background:#edf5fb}.ambient-transport>p{color:#8da5b7}
.volume-mixer-card{position:relative;overflow:hidden;margin-top:18px;padding:clamp(22px,3vw,32px);border:1px solid rgba(255,255,255,.09);border-radius:30px;background:linear-gradient(110deg,rgba(30,37,27,.97),rgba(19,25,22,.97) 55%,rgba(23,31,35,.97));box-shadow:0 24px 70px rgba(0,0,0,.22)}.volume-mixer-card:before{content:"";position:absolute;inset:0;pointer-events:none;background:radial-gradient(circle at 15% 120%,rgba(185,230,140,.08),transparent 34%),radial-gradient(circle at 90% -20%,rgba(184,216,244,.09),transparent 35%)}.volume-mixer-card .card-intro{margin-bottom:19px}.master-volume-grid{position:relative;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.master-volume-grid label{display:grid;grid-template-columns:1fr auto;align-items:center;gap:3px 14px;padding:16px 18px;border:1px solid rgba(255,255,255,.07);border-radius:18px;background:rgba(7,10,8,.24)}.master-volume-grid label>span strong,.master-volume-grid label>span small{display:block}.master-volume-grid label>span strong{font-size:13px}.master-volume-grid label>span small{margin-top:2px;color:#777e75;font-size:9px}.master-volume-grid output{color:#c7e9a6;font-size:11px;font-variant-numeric:tabular-nums}.master-volume-grid label:last-child output{color:#b8d8f4}.master-volume-grid input{grid-column:1/-1;width:100%;height:4px;margin-top:9px;accent-color:#b9e68c;cursor:pointer}.master-volume-grid label:last-child input{accent-color:#b8d8f4}
.noise-generator-card{position:relative;overflow:hidden;margin-top:18px;padding:clamp(22px,3vw,32px);border:1px solid rgba(255,255,255,.09);border-radius:30px;background:linear-gradient(135deg,rgba(31,36,30,.97),rgba(18,23,20,.97));box-shadow:0 24px 70px rgba(0,0,0,.22)}.noise-generator-card:before{content:"";position:absolute;inset:0;pointer-events:none;background:radial-gradient(circle at 90% 0,rgba(206,198,173,.1),transparent 36%)}.noise-badge{color:#d8d0b8;border-color:rgba(216,208,184,.22)}.noise-card-layout{position:relative;display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:end;gap:22px}.noise-type-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px}.noise-type-grid button{min-height:64px;padding:11px 14px;border:1px solid rgba(255,255,255,.07);border-radius:16px;background:rgba(7,10,8,.25);color:#f5f1e8;text-align:left}.noise-type-grid button strong,.noise-type-grid button span{display:block}.noise-type-grid button strong{font-size:13px}.noise-type-grid button span{margin-top:3px;color:#7d837b;font-size:8px}.noise-type-grid button:hover,.noise-type-grid button.active{transform:translateY(-1px);border-color:#9a9278;background:#302e26}.noise-type-grid button.active strong{color:#e1d8bc}.noise-transport{display:flex;flex-direction:column;align-items:flex-end;gap:10px;min-width:170px}.noise-transport p{margin:0;color:#98917d;font-size:10px}.noise-toggle{background:#ddd3b5;color:#282216}.noise-toggle.playing{background:#f5f0e2}.master-volume-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.master-volume-grid label:last-child output{color:#ddd3b5}.master-volume-grid label:last-child input{accent-color:#cfc3a2}
@media(max-width:900px){.generator-grid{grid-template-columns:1fr}.sound-card{min-height:auto}.card-transport{margin-top:18px}}
@media(max-width:760px){.noise-card-layout{grid-template-columns:1fr}.noise-transport{align-items:stretch;min-width:0}.noise-transport .play-toggle{width:100%}.master-volume-grid{grid-template-columns:1fr}}
@media(max-width:520px){.page{padding:24px 16px 112px}.page>h1{font-size:42px}.generator-grid{margin-top:25px}.sound-card,.noise-generator-card,.volume-mixer-card{padding:20px;border-radius:24px}.card-heading h2{font-size:27px}.card-intro{margin-bottom:18px}.band-button{min-height:68px;padding:11px}.card-transport{align-items:stretch;flex-direction:column}.play-toggle{width:100%}.ambient-controls label{padding:9px 11px}.noise-type-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.master-volume-grid label{padding:13px 14px}}
'''

CHORD_PROGRESSION_STYLES = '''
.chord-subcard{position:relative;margin-top:18px;padding:16px;border:1px solid rgba(184,216,244,.13);border-radius:20px;background:rgba(5,10,13,.34)}
.chord-subcard-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.chord-subcard-heading p{margin:0;color:#8196a7;font-size:8px;font-weight:800;letter-spacing:.14em;text-transform:uppercase}.chord-subcard-heading h3{margin:3px 0 0;font:400 22px/1.1 Georgia,serif}.chord-subcard-heading>span{padding:4px 7px;border-radius:999px;background:#1d303d;color:#b8d8f4;font-size:7px;font-weight:800;letter-spacing:.1em;text-transform:uppercase}.chord-help{margin:8px 0 13px;color:#7f898e;font-size:9px}.chord-seed{display:grid;grid-template-columns:1fr 1.35fr;align-items:center;gap:10px;color:#bec5c8;font-size:9px}.chord-seed span small{display:block;color:#69747a;font-size:7px}.chord-seed input{min-width:0;width:100%;padding:9px 11px;border:1px solid rgba(255,255,255,.08);border-radius:10px;background:#101719;color:#f5f1e8;outline:0}.chord-seed input:focus{border-color:#7ca5c3}.chord-settings{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:10px}.chord-settings label{display:grid;grid-template-columns:1fr auto;gap:5px 8px;padding:8px 10px;border:1px solid rgba(255,255,255,.055);border-radius:12px;color:#9ba5a9;font-size:8px}.chord-settings output{color:#b8d8f4;font-variant-numeric:tabular-nums}.chord-settings input{grid-column:1/-1;width:100%;height:3px;accent-color:#a9ccec}.chord-greedy{display:flex;align-items:center;gap:8px;margin:10px 1px;color:#aab3b5;font-size:9px}.chord-greedy input{accent-color:#a9ccec}.chord-greedy small{display:block;color:#657176;font-size:7px}.chord-results{display:flex;gap:6px;overflow-x:auto;min-height:36px;padding:7px;border-radius:12px;background:#0e1416}.chord-results span{flex:none;padding:5px 8px;border:1px solid rgba(184,216,244,.12);border-radius:8px;color:#aebec8;font-size:9px;transition:.2s}.chord-results span.active{border-color:#a9ccec;background:#263d4c;color:#eef7fd;transform:translateY(-1px)}.chord-results .empty{border:0;color:#657176}.chord-actions{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}.chord-actions button{padding:8px 11px;border:1px solid rgba(255,255,255,.08);border-radius:999px;background:#192226;color:#b7c2c7;font-size:8px;font-weight:700}.chord-actions .chord-generate{border:0;background:#b8d8f4;color:#10202c}.chord-actions button:disabled{cursor:not-allowed;opacity:.38}.chord-status{min-height:14px;margin:8px 2px 0;color:#718089;font-size:8px}
@media(max-width:520px){.chord-seed{grid-template-columns:1fr}.chord-settings{grid-template-columns:1fr}.chord-actions button{flex:1}.chord-actions .chord-generate{flex-basis:100%}}
'''

CONTINUOUS_CHORD_STYLES = '''
.chord-continuous{display:flex;align-items:center;gap:8px;margin:10px 1px;color:#aab3b5;font-size:9px}.chord-continuous input{accent-color:#a9ccec}.chord-continuous small{display:block;color:#657176;font-size:7px}.chord-pipeline{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;margin:9px 0}.chord-pipeline[hidden]{display:none}.chord-pipeline>span{display:flex;align-items:center;justify-content:space-between;gap:5px;padding:7px 8px;border:1px solid rgba(184,216,244,.1);border-radius:10px;background:#111a1e}.chord-pipeline small{color:#657780;font-size:7px;text-transform:uppercase;letter-spacing:.08em}.chord-pipeline strong{color:#c8e5fa;font-size:9px}.chord-pipeline>span:last-child strong{animation:pipeline-pulse 1.15s ease-in-out infinite}@keyframes pipeline-pulse{50%{opacity:.42}}@media(max-width:520px){.chord-pipeline{grid-template-columns:1fr}.chord-pipeline>span{padding:8px 10px}}
'''

CHORD_TRANSITION_STYLES = '''
.binaural-transition-subcard{position:relative;margin-top:18px;padding:16px;border:1px solid rgba(185,230,140,.12);border-radius:20px;background:rgba(8,13,8,.3)}.transition-subcard-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.transition-subcard-heading p{margin:0;color:#78896c;font-size:8px;font-weight:800;letter-spacing:.14em;text-transform:uppercase}.transition-subcard-heading h3{margin:3px 0 0;font:400 20px/1.1 Georgia,serif}.transition-subcard-heading>span{padding:4px 7px;border-radius:999px;background:#20301b;color:#c4ef99;font-size:7px;font-weight:800;letter-spacing:.1em;text-transform:uppercase}.binaural-transition-subcard>p{margin:8px 0 12px;color:#747e72;font-size:8px}.transition-controls{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.transition-controls label{display:grid;grid-template-columns:1fr auto;gap:4px 8px;padding:9px 10px;border:1px solid rgba(255,255,255,.055);border-radius:12px;background:rgba(8,11,8,.2)}.transition-controls span strong,.transition-controls span small{display:block}.transition-controls span strong{font-size:9px}.transition-controls span small{color:#697267;font-size:7px}.transition-controls output{color:#c4ef99;font-size:8px;font-variant-numeric:tabular-nums}.transition-controls input{grid-column:1/-1;width:100%;height:3px;accent-color:#a9cf83}@media(max-width:520px){.transition-controls{grid-template-columns:1fr}}
'''

MOBILE_READABILITY_STYLES = '''
/* mobile-readability-v1 */
@media(max-width:600px){
html{-webkit-text-size-adjust:100%;overflow-x:hidden}body{overflow-x:hidden;font-size:16px;line-height:1.55}.page{width:100%;padding:28px max(16px,env(safe-area-inset-right)) calc(118px + env(safe-area-inset-bottom)) max(16px,env(safe-area-inset-left))}.page>h1{font-size:clamp(38px,12vw,48px);line-height:1}.intro,.card-intro,.mode-note,.binaural-transition-subcard>p,.chord-help{font-size:14px;line-height:1.55}.eyebrow,.panel-heading p,.session-copy p,.simple-card-heading p,.card-heading p,.transition-subcard-heading p,.chord-subcard-heading p{font-size:12px;line-height:1.35}.session-copy span,.master-volume-subcard span small,.mode-grid small,.band-button span,.ambient-controls label>span small,.master-volume-grid label>span small,.noise-type-grid button span,.transition-controls span small,.chord-seed span small,.chord-greedy small,.chord-continuous small{font-size:12px;line-height:1.4}.master-volume-subcard span strong,.mode-grid strong,.card-transport>p,.play-toggle strong,.ambient-controls label>span strong,.master-volume-grid label>span strong,.noise-type-grid button strong,.transition-controls span strong,.chord-seed,.chord-settings label,.chord-greedy,.chord-continuous,.chord-status{font-size:14px}.master-volume-subcard output,.ambient-controls output,.master-volume-grid output,.transition-controls output,.chord-settings output{font-size:13px}.simple-home-grid{gap:16px}.session-card,.mode-card,.sound-card,.noise-generator-card,.volume-mixer-card{border-radius:22px}.session-card,.mode-card{padding:20px}.mode-grid{grid-template-columns:1fr}.mode-grid button,.band-button,.noise-type-grid button,.chord-actions button{min-height:48px}.mode-grid button{padding:14px}.mode-icon{width:38px;height:38px;font-size:18px}.session-toggle{width:132px;height:132px}.master-volume-subcard{padding:16px}.master-volume-subcard input,.ambient-controls input,.master-volume-grid input,.transition-controls input,.chord-settings input{height:28px;margin-block:6px;touch-action:pan-y}.play-toggle{min-height:48px}.ambient-controls label,.master-volume-grid label,.transition-controls label{padding:13px}.noise-type-grid{grid-template-columns:1fr}.card-heading,.transition-subcard-heading,.chord-subcard-heading{flex-wrap:wrap}.chord-seed input{min-height:44px;font-size:16px}.chord-actions{display:grid;grid-template-columns:1fr 1fr}.chord-actions button{font-size:13px}.chord-actions .chord-generate{grid-column:1/-1}.chord-results span{font-size:13px}.card-badge,.transition-subcard-heading>span,.chord-subcard-heading>span{font-size:11px}
}
'''


def _ensure_single_home_styles(root):
    css_path = root / "static/user.css"
    if not css_path.is_file():
        return
    css = css_path.read_text(encoding="utf-8")
    additions = ""
    if "simplified-home-v1" not in css:
        additions += "\n" + SIMPLE_HOME_STYLES
    if "single-home-v6" not in css:
        additions += "\n/* single-home-v6 */\n" + SINGLE_HOME_STYLES
    if "single-home-v7" not in css:
        additions += "\n/* single-home-v7 */\n" + CHORD_PROGRESSION_STYLES
    if "single-home-v10" not in css:
        additions += "\n/* single-home-v10 */\n" + CONTINUOUS_CHORD_STYLES
    if "single-home-v12" not in css:
        additions += "\n/* single-home-v12 */\n" + CHORD_TRANSITION_STYLES
    if "mobile-readability-v1" not in css:
        additions += "\n" + MOBILE_READABILITY_STYLES
    if additions:
        css_path.write_text(css.rstrip() + additions + "\n", encoding="utf-8")


def _ensure_default_navigation_icons(root):
    icons = root / "static/icons"
    icons.mkdir(parents=True, exist_ok=True)
    for icon_name, path_data in NAV_ICON_PATHS.items():
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="{path_data}"/></svg>'
        (icons / f"{icon_name}.svg").write_text(svg, encoding="utf-8")


def _legacy_home_page():
    cards = '<div class="grid"><div class="card"><strong>Deep restore</strong><span>432 Hz · Theta · Rain</span></div><div class="card"><strong>Open focus</strong><span>528 Hz · Alpha · Forest</span></div><div class="card"><strong>Quiet sleep</strong><span>396 Hz · Delta · Brown noise</span></div></div>'
    return default_page("Your healing frequency", "Live composition", "Shape a continuous soundscape that moves with your breath.", cards)


def migrate_legacy_default_workspace(username):
    root = user_root(username)
    nav_path = root / "nav.json"
    home_path = root / "pages/home.html"
    if not nav_path.is_file() or not home_path.is_file():
        return False
    try:
        navigation = json.loads(nav_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    expected = [
        ("home", "pages/home.html"),
        ("mixer", "pages/mixer.html"),
        ("binaural", "pages/binaural.html"),
        ("noise", "pages/noise.html"),
        ("history", "pages/history.html"),
        ("profile", "pages/profile.html"),
    ]
    items = navigation.get("nav_items", [])
    actual = [(item.get("id"), item.get("target_html")) for item in items if isinstance(item, dict)]
    current_home = home_path.read_text(encoding="utf-8")
    advanced_defaults = (
        default_advanced_page(), _single_home_v2_page(), _single_home_v3_page(),
        _single_home_v4_page(), _single_home_v5_page(), _single_home_v6_page(), _single_home_v7_page(),
        _single_home_v8_page(), _single_home_v9_page(), _single_home_v10_page(), _single_home_v11_page(),
        _single_home_v12_page(),
    )
    if actual == [("home", "pages/home.html")] and current_home in (default_home_page(), *advanced_defaults):
        home_path.write_text(default_home_page(), encoding="utf-8")
        (root / "pages/advanced.html").write_text(default_advanced_page(), encoding="utf-8")
        nav_path.write_text(json.dumps(default_navigation(), indent=2), encoding="utf-8")
        _ensure_single_home_styles(root)
        _ensure_default_navigation_icons(root)
        return True
    if actual == [("home", "pages/home.html"), ("advanced", "pages/advanced.html")]:
        if current_home in advanced_defaults:
            home_path.write_text(default_home_page(), encoding="utf-8")
            (root / "pages/advanced.html").write_text(default_advanced_page(), encoding="utf-8")
        _ensure_single_home_styles(root)
        _ensure_default_navigation_icons(root)
        return True
    if actual == [("home", "pages/home.html")] and current_home == default_home_page():
        _ensure_single_home_styles(root)
        return True
    if actual == [("home", "pages/home.html")] and current_home == _single_home_v12_page():
        home_path.write_text(default_home_page(), encoding="utf-8")
        _ensure_single_home_styles(root)
        return True
    if actual == [("home", "pages/home.html")] and current_home == _single_home_v11_page():
        home_path.write_text(default_home_page(), encoding="utf-8")
        _ensure_single_home_styles(root)
        return True
    if actual == [("home", "pages/home.html")] and current_home == _single_home_v10_page():
        home_path.write_text(default_home_page(), encoding="utf-8")
        _ensure_single_home_styles(root)
        return True
    if actual == [("home", "pages/home.html")] and current_home == _single_home_v9_page():
        home_path.write_text(default_home_page(), encoding="utf-8")
        _ensure_single_home_styles(root)
        return True
    if actual == [("home", "pages/home.html")] and current_home == _single_home_v8_page():
        home_path.write_text(default_home_page(), encoding="utf-8")
        _ensure_single_home_styles(root)
        return True
    if actual == [("home", "pages/home.html")] and current_home == _single_home_v5_page():
        home_path.write_text(default_home_page(), encoding="utf-8")
        _ensure_single_home_styles(root)
        return True
    if actual == [("home", "pages/home.html")] and current_home == _single_home_v6_page():
        home_path.write_text(default_home_page(), encoding="utf-8")
        _ensure_single_home_styles(root)
        return True
    if actual == [("home", "pages/home.html")] and current_home == _single_home_v7_page():
        home_path.write_text(default_home_page(), encoding="utf-8")
        _ensure_single_home_styles(root)
        return True
    if actual == [("home", "pages/home.html")] and current_home == _single_home_v2_page():
        home_path.write_text(default_home_page(), encoding="utf-8")
        _ensure_single_home_styles(root)
        return True
    if actual == [("home", "pages/home.html")] and current_home == _single_home_v3_page():
        home_path.write_text(default_home_page(), encoding="utf-8")
        _ensure_single_home_styles(root)
        return True
    if actual == [("home", "pages/home.html")] and current_home == _single_home_v4_page():
        home_path.write_text(default_home_page(), encoding="utf-8")
        _ensure_single_home_styles(root)
        return True
    if actual != expected or current_home != _legacy_home_page():
        return False
    nav_path.write_text(json.dumps(default_navigation(), indent=2), encoding="utf-8")
    home_path.write_text(default_home_page(), encoding="utf-8")
    (root / "pages/advanced.html").write_text(default_advanced_page(), encoding="utf-8")
    _ensure_single_home_styles(root)
    _ensure_default_navigation_icons(root)
    for name in ("mixer.html", "binaural.html", "noise.html", "history.html", "profile.html"):
        path = root / "pages" / name
        if path.exists():
            path.unlink()
    return True


def initialize_user_storage(username):
    root = user_root(username)
    for folder in ("memory", "pages", "static/icons", "data", "snapshots"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    nav = default_navigation()
    (root / "nav.json").write_text(json.dumps(nav, indent=2), encoding="utf-8")
    (root / "memory/notes.md").write_text("# Resona preferences\n\n", encoding="utf-8")
    (root / "memory/plan.md").write_text("# Vibe modification log\n\n", encoding="utf-8")
    (root / "memory/changelog.md").write_text("# Resona changelog\n\n", encoding="utf-8")
    css = '''*{box-sizing:border-box}html,body{min-height:100%;background:#121611}body{margin:0;color:#f5f1e8;font:500 16px/1.5 Inter,system-ui,sans-serif}.page{width:min(1040px,100%);margin:auto;padding:clamp(28px,6vw,72px) clamp(20px,5vw,54px) 150px}.eyebrow{margin:0;color:#b9e68c;text-transform:uppercase;letter-spacing:.18em;font-size:11px;font-weight:800}h1{font:600 clamp(42px,8vw,82px)/.98 Georgia,serif;letter-spacing:-.05em;margin:10px 0 14px}.intro{max-width:610px;margin:0;color:#b7b5ae}.binaural-panel{margin-top:clamp(34px,6vw,68px)}.panel-heading p{margin:0;color:#858b81;font-size:10px;font-weight:800;letter-spacing:.16em;text-transform:uppercase}.panel-heading h2{margin:5px 0 18px;font:400 clamp(25px,4vw,36px) Georgia,serif}.band-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}.band-button{min-height:116px;padding:18px;border:1px solid rgba(255,255,255,.1);border-radius:20px;background:#1b2019;color:#f5f1e8;text-align:left;cursor:pointer;transition:.22s}.band-button strong,.band-button span{display:block}.band-button strong{font-size:18px}.band-button span{margin-top:8px;color:#8e948a;font-size:11px}.band-button:hover,.band-button.active{transform:translateY(-2px);border-color:#8daf6e;background:#24301f;box-shadow:0 14px 34px #0005}.band-button.active strong{color:#c4ef99}.playback-control{display:flex;flex-direction:column;align-items:center;margin-top:clamp(36px,7vw,72px)}.playback-control>p{color:#9ca396;font-size:12px}.play-toggle{display:flex;align-items:center;justify-content:center;gap:12px;width:160px;height:64px;border:0;border-radius:999px;background:#b9e68c;color:#15200f;box-shadow:0 0 0 9px rgba(185,230,140,.07),0 18px 45px #0007;cursor:pointer}.play-toggle:hover{transform:scale(1.03)}.play-toggle.playing{background:#f1eee5}.play-symbol{font-size:14px}.playback-control small{margin-top:15px;color:#696e66;font-size:10px;text-transform:uppercase;letter-spacing:.12em}@media(max-width:760px){.band-grid{grid-template-columns:repeat(2,1fr)}.band-button:last-child{grid-column:1/-1}.page{padding-top:28px}}@media(max-width:430px){h1{font-size:44px}.band-button{min-height:92px;padding:14px}.binaural-panel{margin-top:30px}.playback-control{margin-top:34px}}'''
    (root / "static/user.css").write_text(css.rstrip() + "\n" + SIMPLE_HOME_STYLES + "\n/* single-home-v6 */\n" + SINGLE_HOME_STYLES + "\n/* single-home-v7 */\n" + CHORD_PROGRESSION_STYLES + "\n/* single-home-v10 */\n" + CONTINUOUS_CHORD_STYLES + "\n/* single-home-v12 */\n" + CHORD_TRANSITION_STYLES + "\n" + MOBILE_READABILITY_STYLES + "\n", encoding="utf-8")
    (root / "static/custom_synth.js").write_text("window.ResonaCustomSynth = { version: 1, configure(engine) { engine.config.carrier = engine.config.ambient.droneFrequency; } };\n", encoding="utf-8")
    _ensure_default_navigation_icons(root)
    pages = {"home.html": default_home_page(), "advanced.html": default_advanced_page()}
    for name, content in pages.items():
        (root / "pages" / name).write_text(content, encoding="utf-8")
    ensure_chord_model_assets(username)


def reset_user_ui(username):
    root = user_root(username)
    preserved_memory = {}
    memory_root = root / "memory"
    if memory_root.is_dir():
        for path in memory_root.rglob("*"):
            if path.is_file() and not path.is_symlink():
                preserved_memory[path.relative_to(memory_root)] = path.read_bytes()

    for relative in ("pages", "static"):
        target = root / relative
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
    nav_path = root / "nav.json"
    if nav_path.exists() or nav_path.is_symlink():
        nav_path.unlink()

    initialize_user_storage(username)
    for relative, content in preserved_memory.items():
        target = memory_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
