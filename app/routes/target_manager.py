"""Target Manager routes for connect, validation, and replay."""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from flask import Blueprint, Response, jsonify, render_template, request, stream_with_context

from app.models import Target, ValidationRun, db
from app.services.target_validation_service import TargetValidationService

target_manager_bp = Blueprint("target_manager", __name__, url_prefix="/target-manager")
_service = TargetValidationService()


def _normalize_status(raw_status: str) -> str:
    value = (raw_status or "").strip().lower()
    if value in {"connected", "online"}:
        return "online"
    if value in {"busy", "connecting"}:
        return "connecting"
    if value in {"disconnected", "offline"}:
        return "offline"
    return "unknown"


def _target_runs_meta(target_id: int) -> tuple[int, datetime | None]:
    rows = (
        ValidationRun.query.filter_by(target_id=target_id)
        .order_by(ValidationRun.timestamp.desc())
        .all()
    )
    if not rows:
        return 0, None
    return len(rows), rows[0].timestamp


def _target_payload(row: Target) -> dict:
    run_count, last_run_at = _target_runs_meta(row.id)
    return {
        "id": row.id,
        "serial": row.serial,
        "platform": row.platform or "",
        "nickname": row.name or "",
        "status": _normalize_status(row.status),
        "run_count": run_count,
        "last_run_at": last_run_at.isoformat() if last_run_at else None,
        "last_seen": row.last_seen.isoformat() if row.last_seen else None,
    }


@target_manager_bp.route("/")
def index():
    return render_template("target_manager.html")


@target_manager_bp.get("/api/targets")
def list_targets():
    rows = Target.query.order_by(Target.id.desc()).all()
    return jsonify({"targets": [_target_payload(row) for row in rows]})


@target_manager_bp.post("/api/targets/connect")
def connect_target():
    payload = request.get_json(silent=True) or {}
    serial = (payload.get("serial") or "").strip()
    nickname = (payload.get("nickname") or payload.get("name") or "").strip() or serial
    platform = (payload.get("platform") or "").strip() or "custom"
    if not serial:
        return jsonify({"error": "Serial is required"}), 400

    row = Target.query.filter_by(serial=serial).first()
    if not row:
        row = Target(name=nickname, serial=serial, platform=platform, status="connecting")
        db.session.add(row)
    else:
        row.name = nickname
        row.platform = platform
        row.status = "connecting"
    db.session.commit()

    ok, message = _service.check_target_connected(serial)
    row.status = "online" if ok else "offline"
    row.last_seen = datetime.utcnow()
    db.session.commit()
    return jsonify(
        {
            "target": _target_payload(row),
            "ok": ok,
            "message": message,
        }
    )


@target_manager_bp.post("/api/targets/<int:target_id>/refresh")
def refresh_target(target_id: int):
    row = Target.query.get(target_id)
    if not row:
        return jsonify({"error": "Target not found"}), 404
    row.status = "connecting"
    db.session.commit()
    ok, message = _service.check_target_connected(row.serial)
    row.status = "online" if ok else "offline"
    row.last_seen = datetime.utcnow()
    db.session.commit()
    return jsonify({"target": _target_payload(row), "message": message})


@target_manager_bp.delete("/api/targets/<int:target_id>")
def disconnect_target(target_id: int):
    row = Target.query.get(target_id)
    if not row:
        return jsonify({"error": "Target not found"}), 404
    ValidationRun.query.filter_by(target_id=target_id).delete()
    db.session.delete(row)
    db.session.commit()
    return jsonify({"ok": True})


@target_manager_bp.get("/api/targets/<int:target_id>/runs")
def list_runs(target_id: int):
    target = Target.query.get(target_id)
    if not target:
        return jsonify({"error": "Target not found"}), 404

    query = ValidationRun.query.filter_by(target_id=target_id)
    result_filter = (request.args.get("result") or "").strip().upper()
    search = (request.args.get("q") or "").strip().lower()
    date_from_raw = (request.args.get("date_from") or "").strip()
    date_to_raw = (request.args.get("date_to") or "").strip()
    if result_filter in {"PASS", "FAIL", "ERROR"}:
        query = query.filter(ValidationRun.result == result_filter)

    try:
        date_from = datetime.fromisoformat(date_from_raw) if date_from_raw else None
    except ValueError:
        date_from = None
    try:
        date_to = datetime.fromisoformat(date_to_raw) if date_to_raw else None
    except ValueError:
        date_to = None
    if date_from:
        query = query.filter(ValidationRun.timestamp >= date_from)
    if date_to:
        query = query.filter(ValidationRun.timestamp <= date_to)

    rows = query.order_by(ValidationRun.timestamp.desc()).limit(200).all()
    payload = []
    for row in rows:
        if search and search not in (row.nl_command or "").lower() and search not in (row.llm_summary or "").lower():
            continue
        try:
            command_count = len(json.loads(row.commands_executed or "[]"))
        except Exception:
            command_count = 0
        payload.append(
            {
                "id": row.id,
                "target_id": row.target_id,
                "target_serial": target.serial,
                "created_at": row.timestamp.isoformat() if row.timestamp else None,
                "use_case": row.nl_command or "General Validation",
                "result": row.result or "ERROR",
                "duration_sec": None,
                "command_count": command_count,
                "summary": row.llm_summary or "",
            }
        )
    return jsonify({"runs": payload})


@target_manager_bp.get("/api/targets/<int:target_id>/runs/<int:run_id>")
def get_run(target_id: int, run_id: int):
    row = ValidationRun.query.filter_by(id=run_id, target_id=target_id).first()
    if not row:
        return jsonify({"error": "Run not found"}), 404
    log = row.raw_output or ""
    return jsonify(
        {
            "id": row.id,
            "target_id": row.target_id,
            "log": log,
            "log_lines": log.splitlines(),
            "result": row.result or "ERROR",
            "summary": row.llm_summary or "",
            "created_at": row.timestamp.isoformat() if row.timestamp else None,
        }
    )


@target_manager_bp.route("/api/targets/<int:target_id>/validate/stream", methods=["GET", "POST"])
def run_validation_stream(target_id: int):
    target = Target.query.get(target_id)
    if not target:
        return jsonify({"error": "Target not found"}), 404

    payload = request.get_json(silent=True) or {}
    query = request.args
    nl_command = (
        (payload.get("nl_command") or payload.get("command"))
        or query.get("nl_command")
        or query.get("use_case")
        or "General Validation"
    )
    nl_command = (nl_command or "").strip()
    session_id = (
        (payload.get("session_id") or query.get("session_id"))
        or f"tgt-{uuid.uuid4().hex[:10]}"
    ).strip()

    target.status = "connecting"
    db.session.commit()

    @stream_with_context
    def generate():
        executed = []
        raw_output = ""
        summary = ""
        result = "ERROR"
        try:
            stream = _service.run_validation_stream(target.serial, nl_command)
            while True:
                try:
                    evt = next(stream)
                except StopIteration as stop:
                    if stop.value:
                        executed, raw_output, summary, result = stop.value
                    break

                evt_type = evt.get("type")
                if evt_type == "meta":
                    yield _service.encode_sse({"type": "step", "step": evt.get("message", "Planning validation")})
                elif evt_type == "command_start":
                    yield _service.encode_sse({"type": "step", "step": evt.get("command", "Running command")})
                elif evt_type == "command_output":
                    output_text = evt.get("output") or ""
                    lines = output_text.splitlines() or [output_text]
                    for line in lines:
                        if line.strip():
                            yield _service.encode_sse({"type": "log", "text": line})
                    yield _service.encode_sse({"type": "log", "text": f"[rc={evt.get('returncode', 1)}]"})
                elif evt_type == "final":
                    yield _service.encode_sse(
                        {
                            "type": "result",
                            "result": evt.get("result", "ERROR"),
                            "summary": evt.get("summary", ""),
                            "details": raw_output[-4000:] if raw_output else "",
                        }
                    )
        except Exception as exc:
            summary = f"Validation failed due to internal error: {exc}"
            result = "ERROR"
            yield _service.encode_sse({"type": "log", "text": f"[error] {summary}"})
            yield _service.encode_sse({"type": "result", "result": "ERROR", "summary": summary, "details": ""})
        finally:
            target.status = "online" if result == "PASS" else "offline"
            target.last_seen = datetime.utcnow()
            run = ValidationRun(
                target_id=target.id,
                session_id=session_id,
                nl_command=nl_command,
                commands_executed=json.dumps(executed or []),
                raw_output=raw_output,
                llm_summary=summary,
                result=result,
            )
            db.session.add(run)
            db.session.commit()
            yield _service.encode_sse(
                {
                    "type": "done",
                    "event": "done",
                    "run_id": run.id,
                    "result": result,
                    "summary": summary,
                }
            )

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
