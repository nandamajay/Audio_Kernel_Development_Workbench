from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

from app.agents.dual_agent_orchestrator import DB_PATH, build_akdw_graph, create_orchestrator
from app.agents.email_notifier import EmailNotifier
from app.agents.project_plan_manager import ProjectPlanManager
from app.agents.shared_state import AgentStateDB

bp = Blueprint("dual_agent", __name__)
_sessions = {}


def _db_write(session_id: str, task_id: str, status: str, verdict: str | None = None):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        INSERT OR IGNORE INTO dual_agent_sessions
        (session_id, task_id, status, started_at)
        VALUES (?,?,?,?)
        """,
        (session_id, task_id, status, datetime.utcnow().isoformat()),
    )
    if verdict is not None or status in ("complete", "paused"):
        conn.execute(
            """
            UPDATE dual_agent_sessions
            SET status=?, completed_at=?, final_verdict=COALESCE(?, final_verdict)
            WHERE session_id=?
            """,
            (status, datetime.utcnow().isoformat(), verdict, session_id),
        )
    conn.commit()
    conn.close()


def _insert_history(session_id: str, round_num: int, role: str, content: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        INSERT INTO dual_agent_history
          (session_id, round_num, role, content, timestamp)
        VALUES (?,?,?,?,?)
        """,
        (session_id, round_num, role, content, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def _read_history(session_id: str, limit: int = 100):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        """
        SELECT round_num, role, content, timestamp
        FROM dual_agent_history
        WHERE session_id=?
        ORDER BY id ASC
        LIMIT ?
        """,
        (session_id, limit),
    ).fetchall()
    conn.close()
    return [
        {
            "round_num": r[0],
            "role": r[1],
            "content": r[2],
            "timestamp": r[3],
        }
        for r in rows
    ]


@bp.route("/dual-agent/", methods=["GET"])
def index():
    plan = ProjectPlanManager().load()
    return render_template("dual_agent.html", plan=plan)


@bp.route("/api/dual-agent/start", methods=["POST"])
def start():
    plan_mgr = ProjectPlanManager()
    task, _ = plan_mgr.get_next_pending_task()
    if not task:
        auto = plan_mgr.auto_create_phase_from_enhancements(
            phase_id=5,
            phase_name="Enhancement Execution Sprint",
        )
        task, _ = plan_mgr.get_next_pending_task()
        if not task:
            return (
                jsonify(
                    {
                        "error": "No pending tasks in plan",
                        "phase_autocreate": auto,
                    }
                ),
                400,
            )
    sid = f"da-{uuid.uuid4().hex[:8]}"
    graph, on_finish = create_orchestrator()
    init_state = {
        "task_description": task["description"],
        "current_task_id": task["id"],
        "designer_output": None,
        "parallel_think_result": None,
        "review_result": None,
        "round_num": 0,
        "verdict": None,
        "session_id": sid,
        "history": [],
    }

    _sessions[sid] = {
        "status": "running",
        "task": task["name"],
        "task_id": task["id"],
        "round": 0,
        "verdict": None,
        "started_at": datetime.utcnow().isoformat(),
    }
    _db_write(sid, task["id"], "running")
    _insert_history(sid, 0, "orchestrator", f"Session started for task {task['id']} - {task['name']}")

    def _run():
        final = graph.invoke(init_state)
        on_finish(final)
        verdict = final.get("verdict", "")
        status = "paused" if verdict == "HUMAN_REVIEW_REQUIRED" else "complete"
        _sessions[sid]["status"] = status
        _sessions[sid]["verdict"] = verdict
        _sessions[sid]["round"] = final.get("round_num", 0)
        _sessions[sid]["completed_at"] = datetime.utcnow().isoformat()
        _db_write(sid, _sessions[sid]["task_id"], status, verdict=verdict)

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"session_id": sid, "task": task["name"]})


@bp.route("/api/dual-agent/status/<sid>")
def status(sid):
    row = _sessions.get(sid)
    if not row:
        return jsonify({"error": "not found"}), 404
    plan = ProjectPlanManager().load()
    return jsonify(
        {
            **row,
            "session_id": sid,
            "history": _read_history(sid),
            "plan": plan,
            "email_log": EmailNotifier().get_recent_logs(5),
        }
    )


@bp.route("/api/dual-agent/plan")
def get_plan():
    return jsonify(ProjectPlanManager().load())


@bp.route("/api/dual-agent/approve/<task_id>", methods=["POST"])
def human_approve(task_id):
    """Human-in-the-loop: Ajay approves after manual review."""
    ProjectPlanManager().mark_task_complete(task_id)
    return jsonify({"status": "approved", "task_id": task_id})


@bp.route("/api/dual-agent/phase5/auto-create", methods=["POST"])
def auto_create_phase5():
    data = request.get_json(silent=True) or {}
    plan_mgr = ProjectPlanManager()
    phase_name = (data.get("phase_name") or "Enhancement Execution Sprint").strip()
    result = plan_mgr.auto_create_phase_from_enhancements(
        phase_id=5,
        phase_name=phase_name,
    )
    if bool(data.get("activate", False)):
        activation = plan_mgr.activate_phase(5, force=bool(data.get("force", False)))
        result["activation"] = activation
    return jsonify(result)


@bp.route("/api/dual-agent/phase/activate/<int:phase_id>", methods=["POST"])
def activate_phase(phase_id: int):
    data = request.get_json(silent=True) or {}
    result = ProjectPlanManager().activate_phase(phase_id, force=bool(data.get("force", False)))
    if not result.get("ok", False):
        return jsonify(result), 409
    return jsonify(result)


@bp.route("/api/dual-agent/run", methods=["POST"])
def run_dual_agent():
    data = request.get_json(silent=True) or {}
    phase = data.get("phase")
    task = data.get("task")
    if phase is None or not task:
        return jsonify({"error": "phase and task are required"}), 400

    graph = build_akdw_graph()
    state_db = AgentStateDB()
    init_state = {
        "current_phase": phase,
        "designer_task": task,
        "architect_task": data.get("architect_hint", ""),
        "project_plan": state_db.get_project_plan(),
        "enhancement_log": [],
        "email_queue": [],
        "review_status": "pending",
        "auto_continue": bool(data.get("auto_continue", True)),
        "max_rounds": int(data.get("max_rounds", 5)),
    }
    result = asyncio.run(graph.ainvoke(init_state))

    sid = result.get("session_id")
    if sid:
        _sessions[sid] = {
            "status": "paused" if result.get("final_verdict") == "HUMAN_REVIEW_REQUIRED" else "complete",
            "task": task,
            "task_id": result.get("latest_review", {}).get("next_task", {}).get("id", str(task)),
            "round": result.get("rounds_executed", 0),
            "verdict": result.get("final_verdict"),
            "started_at": datetime.utcnow().isoformat(),
            "completed_at": datetime.utcnow().isoformat(),
        }

    return jsonify(result)
