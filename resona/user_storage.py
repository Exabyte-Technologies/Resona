import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from flask import current_app


ALLOWED_EXTENSIONS = {".html", ".css", ".js", ".json", ".md", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".mp3", ".wav", ".ogg"}
PROTECTED_FILES = {"nav.json"}
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
            shutil.copytree(item, destination / item.name)
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


def default_navigation():
    return {
        "default_page": "pages/home.html",
        "nav_items": [
            {"id": "home", "label": "Home", "icon": "waves", "icon_path": "static/icons/home.svg", "target_html": "pages/home.html"},
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


def default_home_page():
    return _single_home_v6_page().replace(
        "Harmonic root for drone, pads and melody",
        "Shared harmonic root and binaural carrier",
    ).replace(
        "Set a tonal anchor, then blend five evolving layers. Pads and melody follow the root harmonically; textures stay broadband and spatial effects stay pitchless.",
        "Set the shared tonal anchor for the ambient harmony and binaural carrier. Pads and melody follow it; textures stay broadband and spatial effects stay pitchless.",
    )


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


def _ensure_single_home_styles(root):
    css_path = root / "static/user.css"
    if not css_path.is_file():
        return
    css = css_path.read_text(encoding="utf-8")
    if "single-home-v6" not in css:
        css_path.write_text(css.rstrip() + "\n/* single-home-v6 */\n" + SINGLE_HOME_STYLES + "\n", encoding="utf-8")


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
    if actual == [("home", "pages/home.html")] and current_home == default_home_page():
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
    _ensure_single_home_styles(root)
    for name in ("mixer.html", "binaural.html", "noise.html", "history.html", "profile.html"):
        path = root / "pages" / name
        if path.exists():
            path.unlink()
    return True


def initialize_user_storage(username):
    root = user_root(username)
    for folder in ("memory", "pages", "static/icons", "snapshots"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    nav = default_navigation()
    (root / "nav.json").write_text(json.dumps(nav, indent=2), encoding="utf-8")
    (root / "memory/notes.md").write_text("# Resona preferences\n\n", encoding="utf-8")
    (root / "memory/plan.md").write_text("# Vibe modification log\n\n", encoding="utf-8")
    (root / "memory/changelog.md").write_text("# Resona changelog\n\n", encoding="utf-8")
    css = '''*{box-sizing:border-box}html,body{min-height:100%;background:#121611}body{margin:0;color:#f5f1e8;font:500 16px/1.5 Inter,system-ui,sans-serif}.page{width:min(1040px,100%);margin:auto;padding:clamp(28px,6vw,72px) clamp(20px,5vw,54px) 150px}.eyebrow{margin:0;color:#b9e68c;text-transform:uppercase;letter-spacing:.18em;font-size:11px;font-weight:800}h1{font:600 clamp(42px,8vw,82px)/.98 Georgia,serif;letter-spacing:-.05em;margin:10px 0 14px}.intro{max-width:610px;margin:0;color:#b7b5ae}.binaural-panel{margin-top:clamp(34px,6vw,68px)}.panel-heading p{margin:0;color:#858b81;font-size:10px;font-weight:800;letter-spacing:.16em;text-transform:uppercase}.panel-heading h2{margin:5px 0 18px;font:400 clamp(25px,4vw,36px) Georgia,serif}.band-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}.band-button{min-height:116px;padding:18px;border:1px solid rgba(255,255,255,.1);border-radius:20px;background:#1b2019;color:#f5f1e8;text-align:left;cursor:pointer;transition:.22s}.band-button strong,.band-button span{display:block}.band-button strong{font-size:18px}.band-button span{margin-top:8px;color:#8e948a;font-size:11px}.band-button:hover,.band-button.active{transform:translateY(-2px);border-color:#8daf6e;background:#24301f;box-shadow:0 14px 34px #0005}.band-button.active strong{color:#c4ef99}.playback-control{display:flex;flex-direction:column;align-items:center;margin-top:clamp(36px,7vw,72px)}.playback-control>p{color:#9ca396;font-size:12px}.play-toggle{display:flex;align-items:center;justify-content:center;gap:12px;width:160px;height:64px;border:0;border-radius:999px;background:#b9e68c;color:#15200f;box-shadow:0 0 0 9px rgba(185,230,140,.07),0 18px 45px #0007;cursor:pointer}.play-toggle:hover{transform:scale(1.03)}.play-toggle.playing{background:#f1eee5}.play-symbol{font-size:14px}.playback-control small{margin-top:15px;color:#696e66;font-size:10px;text-transform:uppercase;letter-spacing:.12em}@media(max-width:760px){.band-grid{grid-template-columns:repeat(2,1fr)}.band-button:last-child{grid-column:1/-1}.page{padding-top:28px}}@media(max-width:430px){h1{font-size:44px}.band-button{min-height:92px;padding:14px}.binaural-panel{margin-top:30px}.playback-control{margin-top:34px}}'''
    (root / "static/user.css").write_text(css.rstrip() + "\n/* single-home-v6 */\n" + SINGLE_HOME_STYLES + "\n", encoding="utf-8")
    (root / "static/custom_synth.js").write_text("window.ResonaCustomSynth = { version: 1, configure(engine) { engine.config.carrier = engine.config.ambient.droneFrequency; } };\n", encoding="utf-8")
    icon_paths = {"home": "M3 12h3l2-6 4 12 3-9 2 3h4"}
    for icon_name, path_data in icon_paths.items():
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="{path_data}"/></svg>'
        (root / "static/icons" / f"{icon_name}.svg").write_text(svg, encoding="utf-8")
    pages = {"home.html": default_home_page()}
    for name, content in pages.items():
        (root / "pages" / name).write_text(content, encoding="utf-8")


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
