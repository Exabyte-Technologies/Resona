from datetime import datetime, timezone

from flask import Blueprint, abort, current_app, g, jsonify, request

from .agent_runtime import run_agent, trace_agent_debug
from .closeai import API_KEY_PLACEHOLDER
from .db import get_db
from .security import login_required, require_csrf
from .user_storage import create_snapshot, reset_user_ui, restore_snapshot, safe_path, write_user_file


agent_bp = Blueprint("agent", __name__, url_prefix="/agent")


def agent_context():
    db = get_db()
    base = db.execute("SELECT value FROM settings WHERE key = 'agent_system_prompt'").fetchone()["value"]
    skills = [dict(row) for row in db.execute(
        "SELECT name, description, endpoint FROM skills WHERE enabled = 1 AND (scope = 'global' OR user_id = ?)",
        (g.user["id"],),
    ).fetchall()]
    notes_path = safe_path(g.user["username"], "memory/notes.md")
    nav_path = safe_path(g.user["username"], "nav.json")
    step_row = db.execute("SELECT value FROM settings WHERE key = 'agent_max_steps'").fetchone()
    configured_steps = step_row["value"].strip() if step_row else ""
    max_steps = int(configured_steps or current_app.config["AGENT_MAX_STEPS"])
    return {
        "base_prompt": base,
        "skills": skills,
        "notes": notes_path.read_text(encoding="utf-8"),
        "navigation": nav_path.read_text(encoding="utf-8"),
        "max_steps": max(1, min(max_steps, 200)),
    }


@agent_bp.post("/modify")
@login_required
def modify():
    require_csrf()
    data = request.get_json(silent=True) or {}
    prompt = str(data.get("prompt", "")).strip()
    credential = data.get("credential")
    if not prompt or len(prompt) > 4000:
        abort(400, "Enter a prompt between 1 and 4,000 characters")
    if credential != API_KEY_PLACEHOLDER:
        abort(400, "Invalid Resona credential placeholder")
    db = get_db()
    run = db.execute("INSERT INTO agent_runs(user_id, prompt, status) VALUES (?, ?, 'running')", (g.user["id"], prompt))
    db.commit()
    trace_agent_debug("request_received", run_id=run.lastrowid, username=g.user["username"], user_prompt=prompt)
    snapshot = None
    try:
        context = agent_context()
        snapshot = create_snapshot(g.user["username"])
        result = run_agent(
            username=g.user["username"],
            user_id=g.user["id"],
            user_prompt=prompt,
            credential=credential,
            **context,
        )
        plan_path = safe_path(g.user["username"], "memory/plan.md")
        existing_plan = plan_path.read_text(encoding="utf-8") if plan_path.exists() else "# Vibe modification log\n"
        stamp = datetime.now(timezone.utc).isoformat()
        summary = str(result["summary"])[:1000]
        tool_names = ", ".join(result["tools"]) or "none"
        write_user_file(
            g.user["username"],
            "memory/plan.md",
            existing_plan + f"\n## {stamp}\n{summary}\nSteps: {result['steps']}\nTools: {tool_names}\nSnapshot: `{snapshot}`\n",
        )
        db.execute("UPDATE agent_runs SET summary = ?, status = 'complete' WHERE id = ?", (summary[:500], run.lastrowid))
        db.commit()
        trace_agent_debug("request_completed", run_id=run.lastrowid, username=g.user["username"], snapshot=snapshot, summary=summary, steps=result["steps"], tools=result["tools"])
        return jsonify({
            "ok": True,
            "summary": summary,
            "snapshot": snapshot,
            "steps": result["steps"],
            "tools": result["tools"],
        })
    except Exception as exc:
        db.execute("UPDATE agent_runs SET summary = ?, status = 'failed' WHERE id = ?", (str(exc)[:500], run.lastrowid))
        db.commit()
        trace_agent_debug("request_failed", run_id=run.lastrowid, username=g.user["username"], snapshot=snapshot, error=f"{type(exc).__name__}: {exc}")
        current_app.logger.exception("Autonomous agent run failed")
        return jsonify({"ok": False, "error": str(exc), "snapshot": snapshot}), 422


@agent_bp.post("/rollback/<snapshot_id>")
@login_required
def rollback(snapshot_id):
    require_csrf()
    try:
        restore_snapshot(g.user["username"], snapshot_id)
    except (ValueError, FileNotFoundError):
        abort(404)
    return jsonify({"ok": True})


@agent_bp.post("/reset-ui")
@login_required
def reset_ui():
    require_csrf()
    snapshot = create_snapshot(g.user["username"])
    reset_user_ui(g.user["username"])
    trace_agent_debug("original_ui_restored", username=g.user["username"], snapshot=snapshot)
    return jsonify({"ok": True, "snapshot": snapshot})
