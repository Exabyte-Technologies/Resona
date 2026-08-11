import ipaddress
import json
import shutil
from pathlib import PurePosixPath
from urllib.parse import urlparse

import requests
from flask import current_app

from .closeai import ALLOWED_PROVIDER_HOSTS, agent_completion, get_provider_settings
from .db import get_db
from .user_storage import ALLOWED_EXTENSIONS, restore_snapshot, safe_path, user_root, validate_content, validate_nav, write_user_bytes, write_user_file


MAX_FILE_READ = 120_000
MAX_FILE_WRITE = 500_000
PROTECTED_PATHS = {"nav.json", "snapshots"}


TOOL_DEFINITIONS = [
    {"type": "function", "function": {"name": "list_files", "description": "List files and directories in the user's isolated Resona workspace. Use this before making changes.", "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Relative directory, or empty for workspace root"}, "recursive": {"type": "boolean"}}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file from the user's workspace.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "write_file", "description": "Create or fully rewrite an allowed user file. HTML, JS, JSON and nav.json are validated before writing.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "replace_in_file", "description": "Make a targeted exact-text replacement in a user file. Fails if the old text is absent.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}, "replace_all": {"type": "boolean"}}, "required": ["path", "old_text", "new_text"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "make_directory", "description": "Create a directory inside the user's workspace.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "move_path", "description": "Move or rename a file or directory inside the user's workspace. nav.json and snapshots cannot be moved.", "parameters": {"type": "object", "properties": {"source": {"type": "string"}, "destination": {"type": "string"}}, "required": ["source", "destination"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "delete_path", "description": "Delete a file or directory inside the user's workspace. A run-level snapshot permits rollback. nav.json and snapshots cannot be deleted.", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "list_snapshots", "description": "List available rollback snapshots from oldest to newest. Use this when the user asks to revert or restore.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "restore_snapshot", "description": "Restore the entire user workspace from an exact snapshot id returned by list_snapshots.", "parameters": {"type": "object", "properties": {"snapshot_id": {"type": "string"}}, "required": ["snapshot_id"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "update_navigation", "description": "Validate and replace nav.json with a complete navigation object.", "parameters": {"type": "object", "properties": {"navigation": {"type": "object"}}, "required": ["navigation"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "invoke_skill", "description": "Invoke an enabled admin-registered skill by name. Pass the provider request body as input. Optionally save direct image or audio responses into the workspace.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "input": {"type": "object", "additionalProperties": True}, "save_to": {"type": "string", "description": "Optional relative .png/.jpg/.webp/.mp3/.wav/.ogg output path"}}, "required": ["name", "input"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "validate_workspace", "description": "Validate nav.json, referenced pages and icons, and generated text files. Run this after changes and before finishing.", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "finish", "description": "Finish only after the request is satisfied and the resulting files are internally consistent. Provide a concise user-facing summary.", "parameters": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"], "additionalProperties": False}}},
]


def _clean_relative(relative, allow_root=False):
    text = str(relative or "").replace("\\", "/").strip("/")
    if not text and allow_root:
        return ""
    clean = PurePosixPath(text)
    if not text or clean.is_absolute() or ".." in clean.parts:
        raise ValueError("Unsafe tool path")
    if clean.parts[0] == "snapshots":
        raise ValueError("Snapshots are protected from the agent")
    return str(clean)


def _path(username, relative):
    return safe_path(username, _clean_relative(relative))


def _result(**values):
    return json.dumps(values, ensure_ascii=False)


class WorkspaceTools:
    def __init__(self, username, user_id):
        self.username = username
        self.user_id = user_id

    def execute(self, name, arguments):
        handler = getattr(self, f"tool_{name}", None)
        if handler is None:
            raise ValueError(f"Unknown agent tool: {name}")
        return handler(**arguments)

    def tool_list_files(self, path="", recursive=False):
        relative = _clean_relative(path, allow_root=True)
        root = user_root(self.username) if not relative else _path(self.username, relative)
        if not root.exists() or not root.is_dir():
            raise FileNotFoundError(path)
        iterator = root.rglob("*") if recursive else root.iterdir()
        items = []
        for item in iterator:
            if "snapshots" in item.relative_to(user_root(self.username)).parts or item.is_symlink():
                continue
            kind = "directory" if item.is_dir() else "file"
            size = item.stat().st_size if item.is_file() else None
            items.append({"path": item.relative_to(user_root(self.username)).as_posix(), "type": kind, "size": size})
            if len(items) >= 300:
                break
        return _result(items=items, truncated=len(items) >= 300)

    def tool_read_file(self, path):
        relative = _clean_relative(path)
        target = _path(self.username, relative)
        if not target.is_file() or target.is_symlink():
            raise FileNotFoundError(path)
        if target.stat().st_size > MAX_FILE_READ:
            raise ValueError(f"File exceeds the {MAX_FILE_READ}-byte read limit")
        return _result(path=relative, content=target.read_text(encoding="utf-8"))

    def tool_write_file(self, path, content):
        relative = _clean_relative(path)
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_FILE_WRITE:
            raise ValueError(f"File content must be text below {MAX_FILE_WRITE} bytes")
        if relative == "nav.json":
            validate_nav(json.loads(content))
        write_user_file(self.username, relative, content)
        return _result(ok=True, path=relative, bytes=len(content.encode("utf-8")))

    def tool_replace_in_file(self, path, old_text, new_text, replace_all=False):
        if not old_text:
            raise ValueError("old_text cannot be empty")
        target = _path(self.username, path)
        if not target.is_file() or target.is_symlink():
            raise FileNotFoundError(path)
        content = target.read_text(encoding="utf-8")
        occurrences = content.count(old_text)
        if occurrences == 0:
            raise ValueError("old_text was not found")
        updated = content.replace(old_text, new_text, -1 if replace_all else 1)
        self.tool_write_file(path, updated)
        return _result(ok=True, path=_clean_relative(path), replacements=occurrences if replace_all else 1)

    def tool_make_directory(self, path):
        relative = _clean_relative(path)
        target = _path(self.username, relative)
        target.mkdir(parents=True, exist_ok=True)
        return _result(ok=True, path=relative)

    def tool_move_path(self, source, destination):
        source_rel = _clean_relative(source)
        destination_rel = _clean_relative(destination)
        if source_rel in PROTECTED_PATHS or destination_rel in PROTECTED_PATHS:
            raise ValueError("That path is protected")
        source_path = _path(self.username, source_rel)
        destination_path = _path(self.username, destination_rel)
        if not source_path.exists() or source_path.is_symlink():
            raise FileNotFoundError(source)
        if destination_path.exists():
            raise FileExistsError(destination)
        if source_path.is_file() and destination_path.suffix.lower() not in ALLOWED_EXTENSIONS:
            raise ValueError("Destination file type is not allowed")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_path), str(destination_path))
        return _result(ok=True, source=source_rel, destination=destination_rel)

    def tool_delete_path(self, path):
        relative = _clean_relative(path)
        if relative in PROTECTED_PATHS:
            raise ValueError("That path is protected")
        target = _path(self.username, relative)
        if not target.exists() and not target.is_symlink():
            raise FileNotFoundError(path)
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
        return _result(ok=True, deleted=relative)

    def tool_list_snapshots(self):
        snapshot_root = user_root(self.username) / "snapshots"
        items = sorted(path.name for path in snapshot_root.iterdir() if path.is_dir() and not path.is_symlink())
        return _result(snapshots=items)

    def tool_restore_snapshot(self, snapshot_id):
        restore_snapshot(self.username, str(snapshot_id))
        return _result(ok=True, restored=str(snapshot_id))

    def tool_update_navigation(self, navigation):
        validate_nav(navigation)
        return self.tool_write_file("nav.json", json.dumps(navigation, indent=2, ensure_ascii=False))

    def tool_invoke_skill(self, name, input, save_to=None):
        row = get_db().execute(
            "SELECT name, endpoint FROM skills WHERE name = ? AND enabled = 1 AND (scope = 'global' OR user_id = ?)",
            (name, self.user_id),
        ).fetchone()
        if not row:
            raise ValueError("That skill is not available to this user")
        if not row["endpoint"]:
            raise ValueError("That skill has no callable endpoint")
        parsed = urlparse(row["endpoint"])
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Skill endpoints must use HTTPS")
        if parsed.hostname == "localhost" or parsed.hostname.endswith((".local", ".internal")):
            raise ValueError("Local network skill endpoints are not allowed")
        try:
            address = ipaddress.ip_address(parsed.hostname)
            if address.is_private or address.is_loopback or address.is_link_local:
                raise ValueError("Private network skill endpoints are not allowed")
        except ValueError as exc:
            if "Private network" in str(exc):
                raise
        headers = {"Content-Type": "application/json"}
        provider_secret = ""
        if parsed.hostname in ALLOWED_PROVIDER_HOSTS:
            provider = get_provider_settings()
            if not provider["api_key"]:
                raise RuntimeError("No server-side CloseAI API key is configured")
            provider_secret = provider["api_key"]
            headers["Authorization"] = f"Bearer {provider_secret}"
        payload = input if isinstance(input, dict) else {"input": input}
        response = requests.post(row["endpoint"], headers=headers, json=payload, timeout=90)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
        if save_to:
            if not (content_type.startswith("image/") or content_type.startswith("audio/")):
                raise ValueError("Only direct image or audio skill responses can be saved")
            relative = _clean_relative(save_to)
            write_user_bytes(self.username, relative, response.content)
            return _result(skill=name, status=response.status_code, saved_to=relative, bytes=len(response.content))
        text = response.text[:80_000]
        if provider_secret:
            text = text.replace(provider_secret, "[REDACTED_SERVER_KEY]")
        return _result(skill=name, status=response.status_code, response=text, truncated=len(response.text) > len(text))

    def tool_validate_workspace(self):
        nav_path = _path(self.username, "nav.json")
        navigation = validate_nav(json.loads(nav_path.read_text(encoding="utf-8")))
        checked = ["nav.json"]
        references = [navigation["default_page"], "static/user.css", "static/custom_synth.js"]
        for item in navigation["nav_items"]:
            references.append(item["target_html"])
            if item.get("icon_path"):
                icon_path = item["icon_path"]
                if icon_path.startswith("/storage/"):
                    icon_path = "/".join(icon_path.split("/")[3:])
                references.append(icon_path)
        for relative in set(references):
            target = _path(self.username, relative)
            if not target.is_file() or target.is_symlink():
                raise ValueError(f"Workspace references missing file: {relative}")
            checked.append(relative)
        for target in user_root(self.username).rglob("*"):
            if not target.is_file() or target.is_symlink() or "snapshots" in target.relative_to(user_root(self.username)).parts:
                continue
            relative = target.relative_to(user_root(self.username)).as_posix()
            if target.suffix.lower() in {".html", ".css", ".js", ".json", ".md", ".svg"}:
                if target.stat().st_size > MAX_FILE_WRITE:
                    raise ValueError(f"Workspace file exceeds validation limit: {relative}")
                validate_content(relative, target.read_text(encoding="utf-8"))
                checked.append(relative)
        return _result(ok=True, checked=sorted(set(checked)))

    def tool_finish(self, summary):
        return _result(ok=True, summary=str(summary)[:1000])


def build_agent_messages(base_prompt, user_prompt, skills, notes, navigation, max_steps):
    skill_text = "\n".join(f"- {skill['name']}: {skill['description']} ({skill['endpoint'] or 'no endpoint'})" for skill in skills) or "- No extra skills are installed."
    system = f"""{base_prompt}

You are an autonomous Resona workspace agent. Work like a coding agent: inspect the workspace, use tools, observe their results, and continue for as many turns as necessary until the user's request is genuinely satisfied. Do not stop after proposing code. Use finish only after changes are written and coherent.

This run has a hard ceiling of {max_steps} agent steps. You will receive your current step and remaining budget before every turn. Work deliberately and efficiently: group independent tool calls when useful, avoid rereading unchanged files or repeating an action without new evidence, and finish promptly once the request is implemented and validated. Do not rush, omit required work, or finish merely to conserve steps.

Your entire file authority is the current user's isolated workspace. You cannot access the Resona server source, other users, system paths, or protected snapshots. The immutable player shell and microphone live outside this workspace. HTML cannot contain scripts, iframes, forms, inline event handlers, or embedded objects. Generated JavaScript cannot access document, parent/top, browser storage, cookies, eval, or Function. Dynamic page controls may use data-audio, data-band, data-noise, and data-playback-toggle attributes.

Begin by listing and reading relevant files. For any visual or UI request, inspect both the relevant HTML pages and their CSS. The default workspace intentionally begins with one Home page containing binaural selection and a `data-playback-toggle` play/stop control; add pages or navigation only when the user asks. Produce complete, styled, usable interfaces—not prose, summaries, placeholder words such as "content", or descriptions of intended changes. Preserve existing functionality unless the user asks to remove it. Theme requests should normally update the shared stylesheet rather than replace page markup. Prefer targeted replacements when practical. Keep nav.json valid, mobile layouts usable, and notes or plan memory updated when the request reveals a durable preference. A snapshot already exists, so destructive workspace edits remain recoverable. If the user asks to revert, use list_snapshots and restore_snapshot rather than manually deleting or reconstructing files.

Available registered skills:
{skill_text}

Current navigation:
{navigation[:12000]}

User memory:
{notes[:8000]}
"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user_prompt}]


def run_agent(username, user_id, user_prompt, credential, base_prompt, skills, notes, navigation, max_steps):
    messages = build_agent_messages(base_prompt, user_prompt, skills, notes, navigation, max_steps)
    workspace = WorkspaceTools(username, user_id)
    tools_used = []
    for step in range(1, max_steps + 1):
        remaining = max_steps - step
        messages.append({
            "role": "system",
            "content": (
                f"Run progress: this is step {step} of {max_steps}; {remaining} steps remain after this turn. "
                "Continue efficiently and without redundant work. If the request is fully implemented, run validation and finish; "
                "otherwise keep working and do not rush because of the counter."
            ),
        })
        message = agent_completion(messages, TOOL_DEFINITIONS, credential)
        assistant_message = {"role": "assistant", "content": message.get("content")}
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            assistant_message["tool_calls"] = tool_calls
        messages.append(assistant_message)
        if not tool_calls:
            messages.append({"role": "user", "content": "Continue working with the available tools. Do not answer conversationally; call finish only after the workspace is validated and the request is satisfied."})
            continue
        finished_summary = None
        batch_failed = False
        for call in tool_calls:
            function = call.get("function") or {}
            name = function.get("name", "")
            try:
                arguments = function.get("arguments") or "{}"
                if isinstance(arguments, str):
                    arguments = json.loads(arguments)
                if not isinstance(arguments, dict):
                    raise ValueError("Tool arguments must be an object")
                output = workspace.execute(name, arguments)
                if name == "finish":
                    finished_summary = str(arguments.get("summary") or "Resona update completed")
            except Exception as exc:
                output = _result(ok=False, error=f"{type(exc).__name__}: {exc}")
                batch_failed = True
            tools_used.append(name)
            messages.append({"role": "tool", "tool_call_id": call.get("id", f"step-{step}-{name}"), "content": output})
        if finished_summary is not None and not batch_failed:
            try:
                workspace.tool_validate_workspace()
            except Exception as exc:
                messages.append({"role": "user", "content": f"Do not finish yet. Workspace validation failed: {exc}. Fix the problem, validate again, then finish."})
                continue
            return {"summary": finished_summary[:1000], "steps": step, "tools": tools_used}
    raise RuntimeError(f"Agent reached the configured {max_steps}-step safety limit before finishing")
