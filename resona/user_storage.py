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
FORBIDDEN_HTML = re.compile(r"<(?:script|iframe|object|embed|base|form)\b|\son\w+\s*=", re.IGNORECASE)


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
            raise ValueError("Generated HTML contains an unsafe element or event handler")
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


def default_home_page():
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


SINGLE_HOME_STYLES = '''.binaural-panel{margin-top:clamp(34px,6vw,68px)}.panel-heading p{margin:0;color:#858b81;font-size:10px;font-weight:800;letter-spacing:.16em;text-transform:uppercase}.panel-heading h2{margin:5px 0 18px;font:400 clamp(25px,4vw,36px) Georgia,serif}.band-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}.band-button{min-height:116px;padding:18px;border:1px solid rgba(255,255,255,.1);border-radius:20px;background:#1b2019;color:#f5f1e8;text-align:left;cursor:pointer;transition:.22s}.band-button strong,.band-button span{display:block}.band-button strong{font-size:18px}.band-button span{margin-top:8px;color:#8e948a;font-size:11px}.band-button:hover,.band-button.active{transform:translateY(-2px);border-color:#8daf6e;background:#24301f;box-shadow:0 14px 34px #0005}.band-button.active strong{color:#c4ef99}.playback-control{display:flex;flex-direction:column;align-items:center;margin-top:clamp(36px,7vw,72px)}.playback-control>p{color:#9ca396;font-size:12px}.play-toggle{display:flex;align-items:center;justify-content:center;gap:12px;width:160px;height:64px;border:0;border-radius:999px;background:#b9e68c;color:#15200f;box-shadow:0 0 0 9px rgba(185,230,140,.07),0 18px 45px #0007;cursor:pointer}.play-toggle:hover{transform:scale(1.03)}.play-toggle.playing{background:#f1eee5}.play-symbol{font-size:14px}.playback-control small{margin-top:15px;color:#696e66;font-size:10px;text-transform:uppercase;letter-spacing:.12em}@media(max-width:760px){.band-grid{grid-template-columns:repeat(2,1fr)}.band-button:last-child{grid-column:1/-1}.page{padding-top:28px}}@media(max-width:430px){.page{padding:18px 18px 88px}h1{font-size:40px}.band-button{min-height:74px;padding:12px}.binaural-panel{margin-top:20px}.panel-heading h2{margin-bottom:12px}.playback-control{margin-top:20px}.play-toggle{height:58px}}'''


def _ensure_single_home_styles(root):
    css_path = root / "static/user.css"
    if not css_path.is_file():
        return
    css = css_path.read_text(encoding="utf-8")
    if "single-home-v2" not in css:
        css_path.write_text(css.rstrip() + "\n/* single-home-v2 */\n" + SINGLE_HOME_STYLES + "\n", encoding="utf-8")


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
    if actual == [("home", "pages/home.html")] and home_path.read_text(encoding="utf-8") == default_home_page():
        _ensure_single_home_styles(root)
        return True
    if actual != expected or home_path.read_text(encoding="utf-8") != _legacy_home_page():
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
    css = '''*{box-sizing:border-box}html,body{min-height:100%;background:#121611}body{margin:0;color:#f5f1e8;font:500 16px/1.5 Inter,system-ui,sans-serif}.page{width:min(1040px,100%);margin:auto;padding:clamp(28px,6vw,72px) clamp(20px,5vw,54px) 150px}.eyebrow{margin:0;color:#b9e68c;text-transform:uppercase;letter-spacing:.18em;font-size:11px;font-weight:800}h1{font:600 clamp(42px,8vw,82px)/.98 Georgia,serif;letter-spacing:-.05em;margin:10px 0 14px}.intro{max-width:610px;margin:0;color:#b7b5ae}.binaural-panel{margin-top:clamp(34px,6vw,68px)}.panel-heading p{margin:0;color:#858b81;font-size:10px;font-weight:800;letter-spacing:.16em;text-transform:uppercase}.panel-heading h2{margin:5px 0 18px;font:400 clamp(25px,4vw,36px) Georgia,serif}.band-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}.band-button{min-height:116px;padding:18px;border:1px solid rgba(255,255,255,.1);border-radius:20px;background:#1b2019;color:#f5f1e8;text-align:left;cursor:pointer;transition:.22s}.band-button strong,.band-button span{display:block}.band-button strong{font-size:18px}.band-button span{margin-top:8px;color:#8e948a;font-size:11px}.band-button:hover,.band-button.active{transform:translateY(-2px);border-color:#8daf6e;background:#24301f;box-shadow:0 14px 34px #0005}.band-button.active strong{color:#c4ef99}.playback-control{display:flex;flex-direction:column;align-items:center;margin-top:clamp(36px,7vw,72px)}.playback-control>p{color:#9ca396;font-size:12px}.play-toggle{display:flex;align-items:center;justify-content:center;gap:12px;width:160px;height:64px;border:0;border-radius:999px;background:#b9e68c;color:#15200f;box-shadow:0 0 0 9px rgba(185,230,140,.07),0 18px 45px #0007;cursor:pointer}.play-toggle:hover{transform:scale(1.03)}.play-toggle.playing{background:#f1eee5}.play-symbol{font-size:14px}.playback-control small{margin-top:15px;color:#696e66;font-size:10px;text-transform:uppercase;letter-spacing:.12em}@media(max-width:760px){.band-grid{grid-template-columns:repeat(2,1fr)}.band-button:last-child{grid-column:1/-1}.page{padding-top:28px}}@media(max-width:430px){h1{font-size:44px}.band-button{min-height:92px;padding:14px}.binaural-panel{margin-top:30px}.playback-control{margin-top:34px}}'''
    (root / "static/user.css").write_text(css.rstrip() + "\n/* single-home-v2 */\n" + SINGLE_HOME_STYLES + "\n", encoding="utf-8")
    (root / "static/custom_synth.js").write_text("window.ResonaCustomSynth = { version: 1, configure(engine) { engine.config.carrier = 216; } };\n", encoding="utf-8")
    icon_paths = {"home": "M3 12h3l2-6 4 12 3-9 2 3h4"}
    for icon_name, path_data in icon_paths.items():
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="{path_data}"/></svg>'
        (root / "static/icons" / f"{icon_name}.svg").write_text(svg, encoding="utf-8")
    pages = {"home.html": default_home_page()}
    for name, content in pages.items():
        (root / "pages" / name).write_text(content, encoding="utf-8")
