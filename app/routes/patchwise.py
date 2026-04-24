"""Patch workshop routes and APIs (Phase 3 scaffold)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import requests
from bs4 import BeautifulSoup
from flask import Blueprint, current_app, jsonify, render_template, request
from sqlalchemy import inspect, text

from app.config import MODEL_METADATA, get_available_models, get_default_model
from app.models import ReviewEvidence, ReviewSession, db
from app.services.env_service import resolve_ssl_verify


patchwise_bp = Blueprint("patchwise", __name__)
_schema_checked = False


def _json_load(text: str | None, fallback: Any) -> Any:
    if not text:
        return fallback
    try:
        return json.loads(text)
    except Exception:
        return fallback


def _verify_value() -> bool | str:
    return resolve_ssl_verify(
        ssl_verify_raw=os.environ.get("QGENIE_SSL_VERIFY", "true"),
        ca_bundle=os.environ.get("QGENIE_CA_BUNDLE", ""),
    )


def _fetch_context_url(url: str) -> str:
    verify = _verify_value()
    try:
        resp = requests.get(url, timeout=10, verify=verify, headers={"User-Agent": "AKDW/1.0"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        blocks = soup.find_all("pre")
        text = "\n".join(block.get_text() for block in blocks) if blocks else soup.get_text(separator="\n")
        return (text or "")[:8000]
    except Exception as exc:
        return f"[context fetch error: {exc}]"


def _extract_findings(text: str, patch_content: str) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    issue_re = re.compile(r"(?P<sev>CRITICAL|WARNING|SUGGESTION|INFO).*?(?P<file>[\w/\-.]+):(?:\s*)?(?P<line>\d+)?", re.IGNORECASE)

    for idx, raw in enumerate((text or "").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        m = issue_re.search(line)
        if not m:
            continue
        sev = (m.group("sev") or "INFO").upper()
        findings.append(
            {
                "id": f"f-{idx}",
                "severity": sev,
                "file": m.group("file") or "unknown",
                "line": int(m.group("line") or 0),
                "description": line,
                "suggested_fix": "Review and apply the suggested kernel-style-safe fix.",
            }
        )

    if findings:
        return findings

    # Fallback deterministic heuristic so endpoint always returns structured findings.
    fallback: List[Dict[str, Any]] = []
    lines = (patch_content or "").splitlines()
    for idx, line in enumerate(lines, start=1):
        clean = line.strip()
        if "kmalloc(" in clean and "__GFP_ZERO" not in clean:
            fallback.append(
                {
                    "id": f"f-{idx}",
                    "severity": "WARNING",
                    "file": "unknown",
                    "line": idx,
                    "description": "Dynamic allocation detected; verify initialization and error handling.",
                    "suggested_fix": "Use devm helpers where possible and ensure free path for all exits.",
                }
            )
        if clean.startswith("+") and "return -ENOMEM" in clean:
            fallback.append(
                {
                    "id": f"f-{idx}-ret",
                    "severity": "SUGGESTION",
                    "file": "unknown",
                    "line": idx,
                    "description": "ENOMEM handling present; verify logging context before return.",
                    "suggested_fix": "Add dev_err/dev_dbg before early returns for easier bring-up debugging.",
                }
            )

    if not fallback:
        fallback.append(
            {
                "id": "f-1",
                "severity": "INFO",
                "file": "unknown",
                "line": 0,
                "description": "Patch parsed successfully. No obvious high-risk pattern detected by baseline parser.",
                "suggested_fix": "Run checkpatch and subsystem-specific review before upstream submission.",
            }
        )
    return fallback


def _session_to_dict(row: ReviewSession) -> Dict[str, Any]:
    return {
        "session_id": row.session_id,
        "patch_hash": row.patch_hash,
        "patch_filename": getattr(row, "patch_filename", "") or "",
        "status": getattr(row, "status", "pending") or "pending",
        "ai_summary": getattr(row, "ai_summary", "") or "",
        "summary": _json_load(row.summary, {}),
        "findings": _json_load(row.findings_json, []),
        "checkpatch_output": row.checkpatch_output or "",
        "maintainers": _json_load(row.maintainers_json, []),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _find_script(path_hint: str, filename: str) -> str | None:
    direct = os.path.join(path_hint, "scripts", filename)
    if os.path.exists(direct):
        return direct
    try:
        out = subprocess.run(
            ["find", path_hint, "-name", filename],
            capture_output=True,
            text=True,
            timeout=6,
        )
        candidates = [line.strip() for line in out.stdout.splitlines() if line.strip()]
        return candidates[0] if candidates else None
    except Exception:
        return None


def _ensure_review_session_schema() -> None:
    global _schema_checked
    if _schema_checked:
        return
    inspector = inspect(db.engine)
    columns = {col["name"] for col in inspector.get_columns("review_sessions")}
    alter_stmts = []
    if "patch_filename" not in columns:
        alter_stmts.append("ALTER TABLE review_sessions ADD COLUMN patch_filename VARCHAR(255)")
    if "status" not in columns:
        alter_stmts.append("ALTER TABLE review_sessions ADD COLUMN status VARCHAR(24) DEFAULT 'pending'")
    if "ai_summary" not in columns:
        alter_stmts.append("ALTER TABLE review_sessions ADD COLUMN ai_summary TEXT")

    for stmt in alter_stmts:
        db.session.execute(text(stmt))
    if alter_stmts:
        db.session.commit()
    _schema_checked = True


def _extract_patch_files(patch_content: str) -> List[str]:
    files: List[str] = []
    for line in (patch_content or "").splitlines():
        if line.startswith("+++ b/"):
            files.append(line.replace("+++ b/", "", 1).strip())
    return [item for item in files if item and item != "/dev/null"]


def _extract_patch_metadata(patch_content: str) -> Dict[str, str]:
    subject = ""
    author = ""
    date = ""
    for line in (patch_content or "").splitlines():
        low = line.lower()
        if low.startswith("subject:") and not subject:
            subject = line.split(":", 1)[1].strip()
        elif low.startswith("from:") and not author:
            author = line.split(":", 1)[1].strip()
        elif low.startswith("date:") and not date:
            date = line.split(":", 1)[1].strip()
        if subject and author and date:
            break
    patch_files = _extract_patch_files(patch_content)
    patch_filename = patch_files[0] if patch_files else "patch.diff"
    if not subject:
        subject = f"Patch touching {patch_filename}"
    return {"subject": subject, "author": author or "Unknown", "date": date or "", "patch_filename": patch_filename}


def _parse_maintainer_role(raw_line: str) -> str:
    low = (raw_line or "").lower()
    if "reviewer" in low:
        return "reviewer"
    if "list" in low or "mailing" in low:
        return "list"
    return "maintainer"


def _get_maintainers_for_files(file_paths: List[str]) -> List[Dict[str, str]]:
    kernel_root = current_app.config.get("KERNEL_SRC_PATH", "/app/kernel")
    script = _find_script(kernel_root, "get_maintainer.pl")
    maintainers: List[Dict[str, str]] = []
    pattern = re.compile(r"^(?P<name>.+?)\s*<(?P<email>[^>]+)>")

    if script:
        for file_path in file_paths:
            cmd = ["perl", script, "--file", file_path]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            for line in ((proc.stdout or "") + "\n" + (proc.stderr or "")).splitlines():
                m = pattern.search(line.strip())
                if not m:
                    continue
                maintainers.append(
                    {
                        "name": m.group("name").strip(),
                        "email": m.group("email").strip(),
                        "role": _parse_maintainer_role(line.strip()),
                    }
                )

    seen = set()
    uniq: List[Dict[str, str]] = []
    for item in maintainers:
        key = item["email"].lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(item)
    return uniq


@patchwise_bp.get("/patchwise")
@patchwise_bp.get("/patchwise/")
def patchwise_home():
    return render_template(
        "patchwise.html",
        models=get_available_models(),
        model_metadata=MODEL_METADATA,
        default_model=get_default_model(),
    )


@patchwise_bp.post("/api/patchwise/review")
def review_patch():
    _ensure_review_session_schema()
    payload = request.get_json() or {}
    session_id = (payload.get("session_id") or f"patch-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}").strip()
    patch_content = payload.get("patch_content", "")
    context_url = (payload.get("context_url") or "").strip()
    model = (payload.get("model") or get_default_model()).strip()

    if not patch_content.strip():
        return jsonify({"ok": False, "error": "patch_content is required"}), 400

    context_text = _fetch_context_url(context_url) if context_url else ""

    system_prompt = (
        "You are a Senior Linux Kernel Code Reviewer and upstream expert. "
        "Review this patch for correctness, coding style, API usage, memory safety, "
        "concurrency, upstreaming readiness, and maintainer concerns. "
        "For each finding include severity (CRITICAL/WARNING/SUGGESTION/INFO), "
        "location (file:line), description, and suggested fix."
    )
    full_prompt = system_prompt + "\n\nPatch:\n" + patch_content
    if context_text:
        full_prompt += "\n\nExternal Context:\n" + context_text

    ai_raw = ""
    try:
        service = current_app.extensions.get("agent_service")
        if service:
            ai_raw = service._try_qgenie_chat(model, full_prompt)  # intentionally reusing runtime-configured client
    except Exception as exc:
        ai_raw = f"[ai review fallback: {exc}]"

    metadata = _extract_patch_metadata(patch_content)
    file_paths = _extract_patch_files(patch_content)
    maintainers = _get_maintainers_for_files(file_paths)
    findings = _extract_findings(ai_raw, patch_content)
    summary = {
        "critical": sum(1 for item in findings if item.get("severity") == "CRITICAL"),
        "warning": sum(1 for item in findings if item.get("severity") == "WARNING"),
        "suggestion": sum(1 for item in findings if item.get("severity") == "SUGGESTION"),
        "info": sum(1 for item in findings if item.get("severity") == "INFO"),
        "subject": metadata.get("subject", ""),
        "author": metadata.get("author", ""),
        "date": metadata.get("date", ""),
    }
    ai_summary = (ai_raw or "").strip().split("\n\n")[0][:1000]

    patch_hash = hashlib.sha256(patch_content.encode("utf-8", errors="ignore")).hexdigest()
    row = ReviewSession.query.filter_by(session_id=session_id).first()
    if not row:
        row = ReviewSession(session_id=session_id, patch_hash=patch_hash)
        db.session.add(row)

    row.patch_hash = patch_hash
    row.summary = json.dumps(summary)
    row.findings_json = json.dumps(findings)
    row.patch_filename = metadata.get("patch_filename", "")
    row.status = "reviewed"
    row.maintainers_json = json.dumps(maintainers)
    row.ai_summary = ai_summary
    row.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({
        "ok": True,
        "session_id": session_id,
        "findings": findings,
        "summary": summary,
        "maintainers": maintainers,
        "patch_metadata": metadata,
        "ai_summary": ai_summary,
        "raw": ai_raw,
    })


@patchwise_bp.post("/api/patchwise/run_checkpatch")
def run_checkpatch():
    payload = request.get_json() or {}
    session_id = (payload.get("session_id") or "checkpatch-session").strip()
    patch_content = payload.get("patch_content", "")
    if not patch_content.strip():
        return jsonify({"ok": False, "error": "patch_content is required"}), 400

    tmp_patch = f"/tmp/{session_id}.patch"
    with open(tmp_patch, "w", encoding="utf-8") as handle:
        handle.write(patch_content)

    kernel_root = current_app.config.get("KERNEL_SRC_PATH", "/app/kernel")
    script = _find_script(kernel_root, "checkpatch.pl")

    if not script:
        output = "checkpatch.pl not found - skipped"
        return jsonify({"ok": True, "output": output, "warnings_count": 0, "errors_count": 0})

    cmd = ["perl", script, "--no-tree", tmp_patch]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    output = (proc.stdout or "") + (proc.stderr or "")

    warnings_count = len(re.findall(r"\bWARNING\b", output))
    errors_count = len(re.findall(r"\bERROR\b", output))

    row = ReviewSession.query.filter_by(session_id=session_id).first()
    if row:
        row.checkpatch_output = output
        if not getattr(row, "status", None):
            row.status = "reviewed"
        row.updated_at = datetime.utcnow()
        db.session.commit()

    return jsonify(
        {
            "ok": True,
            "output": output,
            "warnings_count": warnings_count,
            "errors_count": errors_count,
        }
    )


@patchwise_bp.post("/api/patchwise/get_maintainers")
def get_maintainers():
    payload = request.get_json() or {}
    file_paths = payload.get("file_paths", []) or []
    return jsonify({"ok": True, "maintainers": _get_maintainers_for_files(file_paths)})


@patchwise_bp.get("/api/patchwise/sessions")
def list_patch_sessions():
    _ensure_review_session_schema()
    rows = ReviewSession.query.order_by(ReviewSession.updated_at.desc()).all()
    return jsonify(
        {
            "ok": True,
            "sessions": [
                {
                    "session_id": row.session_id,
                    "patch_filename": getattr(row, "patch_filename", "") or "",
                    "status": getattr(row, "status", "pending") or "pending",
                    "summary": _json_load(row.summary, {}),
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                }
                for row in rows
            ],
        }
    )


@patchwise_bp.get("/api/patchwise/session/<session_id>")
def patch_session_detail(session_id: str):
    _ensure_review_session_schema()
    row = ReviewSession.query.filter_by(session_id=session_id).first()
    if not row:
        return jsonify({"ok": False, "error": "session not found"}), 404
    return jsonify({"ok": True, "session": _session_to_dict(row)})


@patchwise_bp.get("/api/patchwise/export/<session_id>")
def export_patch_report(session_id: str):
    _ensure_review_session_schema()
    row = ReviewSession.query.filter_by(session_id=session_id).first()
    if not row:
        return jsonify({"ok": False, "error": "session not found"}), 404

    findings = _json_load(row.findings_json, [])
    summary = _json_load(row.summary, {})
    maintainers = _json_load(row.maintainers_json, [])
    patch_subject = summary.get("subject", f"Patch review {session_id}")
    patch_author = summary.get("author", "Unknown")
    patch_date = summary.get("date", "")
    ai_summary = getattr(row, "ai_summary", "") or "No AI summary available."
    evidence_rows = ReviewEvidence.query.filter_by(session_id=session_id).all()
    evidence_map: Dict[str, List[ReviewEvidence]] = {}
    for ev in evidence_rows:
        evidence_map.setdefault(ev.finding_id, []).append(ev)

    body = [
        "<html><head><meta charset='utf-8'><title>Patch Review Report</title>",
        "<style>body{font-family:Segoe UI,sans-serif;background:#0d1117;color:#e6edf3;padding:22px}"
        ".card{border:1px solid #30363d;background:#161b22;border-radius:10px;padding:14px;margin:12px 0}"
        ".sev{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:700}"
        ".critical{background:#f85149;color:#fff}.warning{background:#d29922;color:#111}"
        ".suggestion{background:#58a6ff;color:#05101d}.info{background:#3fb950;color:#051109}"
        ".meta{color:#8b949e;font-size:13px}.loc{font-family:monospace;color:#8b949e;margin-left:8px}"
        "a{color:#58a6ff}pre{background:#0b131f;border:1px solid #30363d;border-radius:8px;padding:10px;overflow:auto}"
        ".thumb{max-width:320px;border:1px solid #30363d;border-radius:6px}</style></head><body>",
        "<h1>Patch Review Report</h1>",
        f"<p class='meta'>Session: {session_id}</p>",
        f"<p class='meta'>Generated: {datetime.utcnow().isoformat()} UTC</p>",
        "<div class='card'>",
        "<h2>Patch Metadata</h2>",
        f"<p><strong>Subject:</strong> {patch_subject}</p>",
        f"<p><strong>Author:</strong> {patch_author}</p>",
        f"<p><strong>Date:</strong> {patch_date}</p>",
        f"<p><strong>Filename:</strong> {getattr(row, 'patch_filename', '') or 'patch.diff'}</p>",
        f"<p><strong>Status:</strong> {getattr(row, 'status', 'reviewed') or 'reviewed'}</p>",
        "</div>",
        "<div class='card'><h2>QGenie AI Summary</h2>",
        f"<p>{ai_summary}</p></div>",
    ]

    for finding in findings:
        fid = finding.get("id", "")
        sev = (finding.get("severity", "INFO") or "INFO").lower()
        body.append("<div class='card'>")
        body.append(
            f"<span class='sev {sev}'>{finding.get('severity', 'INFO')}</span>"
            f"<span class='loc'>{finding.get('file', 'unknown')}:{finding.get('line', 0)}</span>"
        )
        body.append(f"<p>{finding.get('description', '')}</p>")
        body.append(f"<pre>{finding.get('suggested_fix', '')}</pre>")
        for ev in evidence_map.get(fid, []):
            meta = _json_load(ev.metadata_json, {})
            if ev.evidence_type == "screenshot":
                body.append(f"<div><strong>Screenshot</strong><br><img class='thumb' src='data:image/png;base64,{ev.content}'></div>")
            elif ev.evidence_type == "lkml":
                title = meta.get("title", ev.content)
                author = meta.get("author", "Unknown")
                date = meta.get("date", "")
                lkml_url = meta.get("lkml_url", ev.content)
                body.append(f"<div>🔗 <a href='{lkml_url}'>{title}</a> - {author} - {date}</div>")
        body.append("</div>")

    body.append("<div class='card'><h2>Maintainers</h2>")
    if maintainers:
        for item in maintainers:
            name = item.get("name", "Unknown")
            email = item.get("email", "")
            role = item.get("role", "maintainer")
            body.append(f"<div><a href='mailto:{email}'>{name} &lt;{email}&gt;</a> ({role})</div>")
    else:
        body.append("<div class='meta'>No maintainers detected.</div>")
    body.append("</div>")

    body.append("<div class='card'><h2>Checkpatch Output</h2>")
    body.append(f"<pre>{(row.checkpatch_output or 'N/A')}</pre></div>")
    body.append("</body></html>")

    html = "\n".join(body)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"patch_review_{ts}.html"
    out_dir = Path(current_app.config.get("WORKSPACE_PATH", "/app/workspace")) / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / filename
    out_path.write_text(html, encoding="utf-8")
    row.status = "exported"
    row.updated_at = datetime.utcnow()
    db.session.commit()

    return current_app.response_class(
        html,
        mimetype="text/html",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
