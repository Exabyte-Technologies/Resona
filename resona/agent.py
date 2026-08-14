from datetime import datetime, timezone
import re
import secrets

from flask import Blueprint, abort, current_app, g, jsonify, request

from .agent_runtime import run_agent, trace_agent_debug
from .closeai import API_KEY_PLACEHOLDER
from .captcha import validate_captcha
from .db import get_db
from .demo import install_demo_response
from .prompt_safety import review_agent_prompt
from .security import login_required, require_csrf
from .user_storage import create_snapshot, reset_user_ui, restore_snapshot, safe_path, write_user_file


agent_bp = Blueprint("agent", __name__, url_prefix="/agent")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,80}$")


def run_status_payload(run):
    payload = {
        "ok": run["status"] == "complete",
        "request_id": run["client_request_id"],
        "status": run["status"],
        "summary": run["summary"] or "",
        "steps": run["steps"] or 0,
    }
    if run["status"] in {"failed", "rejected"}:
        payload["error"] = run["summary"] or "The agent request did not complete."
    return payload


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
    rapid = data.get("rapid") is True
    request_id = str(data.get("request_id", "")).strip() or secrets.token_urlsafe(24)
    if not prompt or len(prompt) > 4000:
        abort(400, "Enter a prompt between 1 and 4,000 characters")
    if credential != API_KEY_PLACEHOLDER:
        abort(400, "Invalid Resona credential placeholder")
    if not REQUEST_ID_RE.fullmatch(request_id):
        abort(400, "Invalid agent request identifier")
    db = get_db()
    existing = db.execute(
        "SELECT * FROM agent_runs WHERE user_id = ? AND client_request_id = ?",
        (g.user["id"], request_id),
    ).fetchone()
    if existing:
        return jsonify(run_status_payload(existing)), (200 if existing["status"] != "running" else 202)
    recent_runs = db.execute(
        "SELECT COUNT(*) AS count FROM agent_runs WHERE user_id = ? AND created_at >= datetime('now', '-1 hour')",
        (g.user["id"],),
    ).fetchone()["count"]
    if (recent_runs + 1) % 3 == 0 and not validate_captcha(data):
        return jsonify({
            "ok": False,
            "captcha_required": True,
            "error": "Complete the CAPTCHA to continue this AI request.",
        }), 403
    run = db.execute(
        "INSERT INTO agent_runs(user_id, client_request_id, prompt, status) VALUES (?, ?, ?, 'running')",
        (g.user["id"], request_id, prompt),
    )
    db.commit()
    if g.user["is_demo"]:
        try:
            result = install_demo_response(prompt)
            summary = result["summary"]
            db.execute("UPDATE agent_runs SET summary = ?, steps = 1, status = 'complete' WHERE id = ?", (summary, run.lastrowid))
            db.commit()
            return jsonify({
                "ok": True,
                "request_id": request_id,
                "status": "complete",
                "summary": summary,
                "snapshot": None,
                "steps": 1,
                "tools": result["tools"],
            })
        except ValueError as exc:
            db.execute("UPDATE agent_runs SET summary = ?, status = 'failed' WHERE id = ?", (str(exc), run.lastrowid))
            db.commit()
            return jsonify({"ok": False, "error": str(exc), "snapshot": None}), 422
    try:
        safety = review_agent_prompt(prompt, credential, 8 if rapid else 45)
    except Exception as exc:
        message = "The safety review is unavailable, so this request was not executed. Please try again later."
        db.execute("UPDATE agent_runs SET summary = ?, status = 'failed' WHERE id = ?", (message, run.lastrowid))
        db.commit()
        trace_agent_debug("safety_review_failed", run_id=run.lastrowid, username=g.user["username"], error=f"{type(exc).__name__}: {exc}")
        current_app.logger.warning("Agent safety review failed: %s", exc)
        return jsonify({"ok": False, "rejected": True, "error": message, "snapshot": None}), 503
    if not safety.allowed:
        db.execute("UPDATE agent_runs SET summary = ?, status = 'rejected' WHERE id = ?", (safety.message, run.lastrowid))
        db.commit()
        trace_agent_debug("request_rejected", run_id=run.lastrowid, username=g.user["username"], category=safety.category)
        return jsonify({"ok": False, "rejected": True, "category": safety.category, "error": safety.message, "snapshot": None}), 422
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
            rapid=rapid,
            **context,
        )
        summary = str(result["summary"])[:1000]
        if not rapid:
            plan_path = safe_path(g.user["username"], "memory/plan.md")
            existing_plan = plan_path.read_text(encoding="utf-8") if plan_path.exists() else "# Vibe modification log\n"
            stamp = datetime.now(timezone.utc).isoformat()
            tool_names = ", ".join(result["tools"]) or "none"
            write_user_file(
                g.user["username"],
                "memory/plan.md",
                existing_plan + f"\n## {stamp}\n{summary}\nSteps: {result['steps']}\nTools: {tool_names}\nSnapshot: `{snapshot}`\n",
            )
        db.execute("UPDATE agent_runs SET summary = ?, steps = ?, status = 'complete' WHERE id = ?", (summary[:500], result["steps"], run.lastrowid))
        db.commit()
        trace_agent_debug("request_completed", run_id=run.lastrowid, username=g.user["username"], snapshot=snapshot, summary=summary, steps=result["steps"], tools=result["tools"])
        return jsonify({
            "ok": True,
            "request_id": request_id,
            "status": "complete",
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


@agent_bp.get("/status/<request_id>")
@login_required
def status(request_id):
    if not REQUEST_ID_RE.fullmatch(request_id):
        abort(404)
    run = get_db().execute(
        "SELECT * FROM agent_runs WHERE user_id = ? AND client_request_id = ?",
        (g.user["id"], request_id),
    ).fetchone()
    if not run:
        abort(404)
    return jsonify(run_status_payload(run))


@agent_bp.post("/rollback/<snapshot_id>")
@login_required
def rollback(snapshot_id):
    require_csrf()
    if g.user["is_demo"]:
        abort(403, "The Demo workspace can only be reset by an administrator")
    try:
        restore_snapshot(g.user["username"], snapshot_id)
    except (ValueError, FileNotFoundError):
        abort(404)
    return jsonify({"ok": True})


@agent_bp.post("/reset-ui")
@login_required
def reset_ui():
    require_csrf()
    if g.user["is_demo"]:
        abort(403, "The Demo workspace can only be reset by an administrator")
    snapshot = create_snapshot(g.user["username"])
    reset_user_ui(g.user["username"])
    trace_agent_debug("original_ui_restored", username=g.user["username"], snapshot=snapshot)
    return jsonify({"ok": True, "snapshot": snapshot})
