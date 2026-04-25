"""Patch workshop routes and APIs (Phase 3 scaffold)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from difflib import unified_diff
from datetime import datetime
from pathlib import Path
from threading import Lock, Thread
from typing import Any, Dict, List

import requests
from bs4 import BeautifulSoup
from flask import Blueprint, current_app, jsonify, render_template, request
from sqlalchemy import inspect, text
from werkzeug.utils import secure_filename

from app.config import MODEL_METADATA, get_available_models, get_default_model
from app.models import PatchReviewTrace, ReviewEvidence, ReviewSession, db
from app.services.activity_service import log_activity
from app.services.env_service import resolve_ssl_verify


patchwise_bp = Blueprint("patchwise", __name__)
_schema_checked = False
_trace_schema_checked = False
_uploaded_files: Dict[str, str] = {}
_autofix_backups: Dict[str, str] = {}
_pipeline_jobs: Dict[str, Dict[str, Any]] = {}
_pipeline_jobs_lock = Lock()


def _ensure_upload_dir() -> str:
    upload_dir = "/app/uploads"
    os.makedirs(upload_dir, exist_ok=True)
    try:
        os.chmod(upload_dir, 0o777)
    except Exception:
        pass
    return upload_dir


def _allowed_patch_dirs() -> List[str]:
    roots = [
        "/app/kernel",
        "/app/patches",
        "/app/uploads",
        "/tmp",
        "/tmp/akdw-uploads",
        tempfile.gettempdir(),
    ]
    kernel_env = os.environ.get("KERNEL_SOURCE_PATH") or os.environ.get("KERNEL_SRC_PATH")
    if kernel_env:
        roots.append(kernel_env)
    try:
        cfg_root = current_app.config.get("KERNEL_SRC_PATH")
        if cfg_root:
            roots.append(cfg_root)
    except Exception:
        pass
    dedup = []
    seen = set()
    for item in roots:
        if not item:
            continue
        path = os.path.realpath(item)
        if path in seen:
            continue
        seen.add(path)
        dedup.append(path)
    return dedup


def _is_allowed_patch_path(path_value: str) -> bool:
    try:
        real_path = os.path.realpath(path_value)
    except Exception:
        return False

    for raw_root in _allowed_patch_dirs():
        root = os.path.realpath(raw_root)
        if real_path == root or real_path.startswith(root + os.sep):
            return True
    return False


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


def _ensure_patch_trace_schema() -> None:
    global _trace_schema_checked
    if _trace_schema_checked:
        return
    inspector = inspect(db.engine)
    if not inspector.has_table("patch_review_traces"):
        db.session.execute(
            text(
                """
                CREATE TABLE patch_review_traces (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id VARCHAR(64) NOT NULL,
                    session_id VARCHAR(64) NOT NULL,
                    stage VARCHAR(64) NOT NULL,
                    tool VARCHAR(64),
                    status VARCHAR(24) NOT NULL DEFAULT 'ok',
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    exit_code INTEGER,
                    token_input INTEGER NOT NULL DEFAULT 0,
                    token_output INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT,
                    details_json TEXT DEFAULT '{}',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        db.session.commit()
    _trace_schema_checked = True


def _new_trace_id(prefix: str = "pwtrace") -> str:
    return f"{prefix}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"


def _estimate_tokens(value: str) -> int:
    return max(0, int(len(value or "") / 4))


def _log_patch_trace(
    *,
    trace_id: str,
    session_id: str,
    stage: str,
    tool: str = "",
    status: str = "ok",
    duration_ms: int = 0,
    exit_code: int | None = None,
    token_input: int = 0,
    token_output: int = 0,
    error_message: str = "",
    details: Dict[str, Any] | None = None,
) -> None:
    try:
        _ensure_patch_trace_schema()
        row = PatchReviewTrace(
            trace_id=trace_id,
            session_id=session_id,
            stage=stage,
            tool=tool or None,
            status=status,
            duration_ms=max(0, int(duration_ms or 0)),
            exit_code=exit_code,
            token_input=max(0, int(token_input or 0)),
            token_output=max(0, int(token_output or 0)),
            error_message=(error_message or None),
            details_json=json.dumps(details or {}),
        )
        db.session.add(row)
        db.session.commit()
    except Exception:
        db.session.rollback()


def _resolve_patch_payload(payload: Dict[str, Any]) -> tuple[str, str, str | None, int]:
    patch_content = payload.get("patch_content", "")
    filepath = (payload.get("filepath") or "").strip()
    upload_token = (payload.get("upload_token") or "").strip()

    if not patch_content.strip() and upload_token and upload_token in _uploaded_files:
        filepath = _uploaded_files[upload_token]

    if not patch_content.strip() and filepath:
        if not _is_allowed_patch_path(filepath):
            return "", filepath, "Path not allowed", 403
        if not os.path.isfile(filepath):
            return "", filepath, "Patch file not found", 404
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as handle:
                patch_content = handle.read()
        except Exception as exc:
            return "", filepath, f"Unable to read patch file: {exc}", 400

    if not patch_content.strip():
        return "", filepath, "patch_content or filepath is required", 400

    return patch_content, filepath, None, 200


def _run_shell_step(
    *,
    step_id: str,
    name: str,
    command: List[str] | None = None,
    timeout: int = 25,
    cwd: str | None = None,
    skip_reason: str = "",
) -> Dict[str, Any]:
    started = time.perf_counter()
    if skip_reason:
        return {
            "id": step_id,
            "name": name,
            "status": "SKIP",
            "exit_code": None,
            "duration_ms": 0,
            "output_preview": skip_reason,
            "command": command or [],
        }

    output = ""
    exit_code: int | None = None
    status = "PASS"
    error_message = ""
    try:
        proc = subprocess.run(
            command or [],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        exit_code = int(proc.returncode)
        output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        status = "PASS" if proc.returncode == 0 else "FAIL"
    except subprocess.TimeoutExpired as exc:
        partial_out = exc.stdout or ""
        partial_err = exc.stderr or ""
        output = f"{partial_out}\n{partial_err}\n[timeout after {timeout}s]".strip()
        exit_code = 124
        status = "FAIL"
        error_message = "timeout"
    except Exception as exc:
        output = str(exc)
        exit_code = 1
        status = "FAIL"
        error_message = str(exc)

    duration_ms = int((time.perf_counter() - started) * 1000)
    return {
        "id": step_id,
        "name": name,
        "status": status,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "output_preview": (output or "")[:8000],
        "error_message": error_message,
        "command": command or [],
    }


def _collect_pipeline_findings(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for step in steps:
        output = step.get("output_preview", "")
        if step.get("id") == "checkpatch":
            for line in output.splitlines():
                text_line = line.strip()
                if not text_line:
                    continue
                if "ERROR:" in text_line:
                    findings.append({"severity": "ERROR", "tool": "checkpatch", "message": text_line})
                elif "WARNING:" in text_line:
                    findings.append({"severity": "WARNING", "tool": "checkpatch", "message": text_line})
                elif "CHECK:" in text_line:
                    findings.append({"severity": "CHECK", "tool": "checkpatch", "message": text_line})
        elif step.get("status") == "FAIL":
            findings.append(
                {
                    "severity": "ERROR",
                    "tool": step.get("id"),
                    "message": step.get("error_message") or "Step failed. Inspect output for details.",
                }
            )
    return findings[:80]


def _build_pipeline_summary(steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    passed = sum(1 for item in steps if item.get("status") == "PASS")
    failed = sum(1 for item in steps if item.get("status") == "FAIL")
    skipped = sum(1 for item in steps if item.get("status") == "SKIP")
    return {
        "total_steps": len(steps),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "overall": "PASS" if failed == 0 else "FAIL",
    }


def _normalize_text(value: str) -> str:
    return (value or "").replace("\r\n", "\n").replace("\r", "\n")


def _apply_trim_trailing_whitespace(content: str, is_diff: bool) -> str:
    if not is_diff:
        return re.sub(r"[ \t]+$", "", content, flags=re.MULTILINE)
    out_lines: List[str] = []
    for raw in content.split("\n"):
        if raw.startswith("+") and not raw.startswith("+++"):
            out_lines.append(re.sub(r"[ \t]+$", "", raw))
        else:
            out_lines.append(raw)
    return "\n".join(out_lines)


def _apply_autofixes(content: str, accepted_fix_ids: List[str] | None = None) -> Dict[str, Any]:
    normalized = _normalize_text(content)
    is_diff = bool(re.search(r"^diff --git ", normalized, flags=re.MULTILINE))
    available = [
        {
            "id": "trim-trailing-whitespace",
            "title": "Trim trailing whitespace",
            "description": "Removes trailing spaces/tabs. For diff input, only applies to added lines.",
        },
        {
            "id": "ensure-eof-newline",
            "title": "Ensure newline at EOF",
            "description": "Adds a newline at the end of file if missing.",
        },
    ]
    if not is_diff:
        available.extend(
            [
                {
                    "id": "compact-blank-lines",
                    "title": "Compact blank lines",
                    "description": "Reduces 3+ consecutive blank lines to 2.",
                },
                {
                    "id": "modernize-printk-err",
                    "title": "Modernize printk(KERN_ERR)",
                    "description": "Converts printk(KERN_ERR ...) to pr_err(...).",
                },
            ]
        )

    selected = set(accepted_fix_ids or [item["id"] for item in available])
    text_now = normalized
    applied: List[Dict[str, Any]] = []

    if "trim-trailing-whitespace" in selected:
        after = _apply_trim_trailing_whitespace(text_now, is_diff=is_diff)
        if after != text_now:
            applied.append({"id": "trim-trailing-whitespace", "title": "Trim trailing whitespace"})
            text_now = after

    if "compact-blank-lines" in selected and not is_diff:
        after = re.sub(r"\n{3,}", "\n\n", text_now)
        if after != text_now:
            applied.append({"id": "compact-blank-lines", "title": "Compact blank lines"})
            text_now = after

    if "modernize-printk-err" in selected and not is_diff:
        after = re.sub(r"printk\s*\(\s*KERN_ERR\s*\"", 'pr_err("', text_now)
        if after != text_now:
            applied.append({"id": "modernize-printk-err", "title": "Modernize printk(KERN_ERR)"})
            text_now = after

    if "ensure-eof-newline" in selected:
        after = text_now if text_now.endswith("\n") else (text_now + "\n")
        if after != text_now:
            applied.append({"id": "ensure-eof-newline", "title": "Ensure newline at EOF"})
            text_now = after

    before_lines = normalized.splitlines(keepends=True)
    after_lines = text_now.splitlines(keepends=True)
    diff_lines = list(
        unified_diff(
            before_lines,
            after_lines,
            fromfile="before",
            tofile="after",
            lineterm="",
        )
    )
    diff_text = "\n".join(diff_lines)
    changed_line_count = sum(
        1
        for line in diff_lines
        if (line.startswith("+") or line.startswith("-"))
        and not line.startswith("+++")
        and not line.startswith("---")
    )

    return {
        "available_fixes": available,
        "selected_fix_ids": sorted(selected),
        "applied_fixes": applied,
        "has_changes": text_now != normalized,
        "changed_line_count": changed_line_count,
        "fixed_content": text_now,
        "diff": diff_text[:32000],
        "is_diff_input": is_diff,
    }


def _backup_key(session_id: str, path: str) -> str:
    return f"{session_id}:{os.path.realpath(path)}"


def _create_backup(session_id: str, target_path: str, original_content: str) -> str:
    safe_session = secure_filename(session_id) or "default"
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    backup_dir = os.path.join("/tmp/akdw-autofix", safe_session)
    os.makedirs(backup_dir, exist_ok=True)
    backup_name = f"{secure_filename(os.path.basename(target_path))}.{stamp}.bak"
    backup_path = os.path.join(backup_dir, backup_name)
    with open(backup_path, "w", encoding="utf-8", errors="replace") as handle:
        handle.write(original_content)
    _autofix_backups[_backup_key(session_id, target_path)] = backup_path
    return backup_path


def _set_pipeline_job(job_id: str, updates: Dict[str, Any]) -> None:
    with _pipeline_jobs_lock:
        existing = _pipeline_jobs.get(job_id, {})
        merged = dict(existing)
        merged.update(updates or {})
        _pipeline_jobs[job_id] = merged


def _get_pipeline_job(job_id: str) -> Dict[str, Any] | None:
    with _pipeline_jobs_lock:
        existing = _pipeline_jobs.get(job_id)
        return dict(existing) if existing else None


def _prune_pipeline_jobs(max_items: int = 200) -> None:
    with _pipeline_jobs_lock:
        if len(_pipeline_jobs) <= max_items:
            return
        ordered = sorted(
            _pipeline_jobs.items(),
            key=lambda item: (item[1].get("updated_at") or item[1].get("created_at") or ""),
        )
        while len(ordered) > max_items:
            job_id, _ = ordered.pop(0)
            _pipeline_jobs.pop(job_id, None)


def _execute_pipeline(
    *,
    session_id: str,
    patch_content: str,
    filepath: str,
    trace_id: str,
    progress_hook: Any = None,
) -> Dict[str, Any]:
    started = time.perf_counter()
    tmp_patch_path = ""
    steps: List[Dict[str, Any]] = []
    kernel_root = current_app.config.get("KERNEL_SRC_PATH", "/app/kernel")

    def _notify_progress(index: int, total: int, step: Dict[str, Any]) -> None:
        if progress_hook:
            try:
                progress_hook(index=index, total=total, step=step)
            except Exception:
                pass

    try:
        with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False, encoding="utf-8") as tmp:
            tmp.write(patch_content)
            tmp_patch_path = tmp.name

        checkpatch_script = _find_script(kernel_root, "checkpatch.pl")
        checkpatch_step = _run_shell_step(
            step_id="checkpatch",
            name="Checkpatch",
            command=["perl", checkpatch_script, "--no-tree", tmp_patch_path] if checkpatch_script else None,
            timeout=40,
            skip_reason="checkpatch.pl not found under kernel tree" if not checkpatch_script else "",
        )
        cp_out = checkpatch_step.get("output_preview", "")
        checkpatch_step["warnings_count"] = len(re.findall(r"\bWARNING\b", cp_out))
        checkpatch_step["errors_count"] = len(re.findall(r"\bERROR\b", cp_out))
        steps.append(checkpatch_step)
        _notify_progress(index=1, total=4, step=checkpatch_step)

        sparse_bin = shutil.which("sparse")
        sparse_step = _run_shell_step(
            step_id="sparse",
            name="Sparse Probe",
            command=[sparse_bin, "--version"] if sparse_bin else None,
            timeout=20,
            skip_reason="sparse not installed in runtime image" if not sparse_bin else "",
        )
        steps.append(sparse_step)
        _notify_progress(index=2, total=4, step=sparse_step)

        spatch_bin = shutil.which("spatch")
        cocc_step = _run_shell_step(
            step_id="coccinelle",
            name="Coccinelle Probe",
            command=[spatch_bin, "--version"] if spatch_bin else None,
            timeout=20,
            skip_reason="spatch (coccinelle) not installed in runtime image" if not spatch_bin else "",
        )
        steps.append(cocc_step)
        _notify_progress(index=3, total=4, step=cocc_step)

        make_bin = shutil.which("make")
        kernel_makefile = os.path.join(kernel_root, "Makefile")
        compile_skip = ""
        compile_cmd = None
        if not make_bin:
            compile_skip = "make is not available"
        elif not os.path.isfile(kernel_makefile):
            compile_skip = "kernel Makefile not found"
        else:
            compile_cmd = [make_bin, "-C", kernel_root, "-s", "kernelversion"]
        compile_step = _run_shell_step(
            step_id="compile_smoke",
            name="Compile Smoke",
            command=compile_cmd,
            timeout=30,
            skip_reason=compile_skip,
        )
        steps.append(compile_step)
        _notify_progress(index=4, total=4, step=compile_step)
    finally:
        if tmp_patch_path and os.path.exists(tmp_patch_path):
            try:
                os.unlink(tmp_patch_path)
            except Exception:
                pass

    summary = _build_pipeline_summary(steps)
    findings = _collect_pipeline_findings(steps)
    pipeline_duration = int((time.perf_counter() - started) * 1000)
    combined_output = "\n".join(item.get("output_preview", "") for item in steps if item.get("output_preview"))
    token_out = _estimate_tokens(combined_output)

    for item in steps:
        _log_patch_trace(
            trace_id=trace_id,
            session_id=session_id,
            stage=f"pipeline:{item.get('id')}",
            tool=item.get("id", ""),
            status=(item.get("status") or "FAIL").lower(),
            duration_ms=int(item.get("duration_ms") or 0),
            exit_code=item.get("exit_code"),
            token_input=_estimate_tokens(patch_content) if item.get("id") == "checkpatch" else 0,
            token_output=_estimate_tokens(item.get("output_preview", "")),
            error_message=item.get("error_message", ""),
            details={
                "name": item.get("name", ""),
                "command": item.get("command", []),
                "warnings_count": item.get("warnings_count", 0),
                "errors_count": item.get("errors_count", 0),
            },
        )

    _log_patch_trace(
        trace_id=trace_id,
        session_id=session_id,
        stage="pipeline",
        tool="pipeline",
        status=("ok" if summary.get("failed", 0) == 0 else "error"),
        duration_ms=pipeline_duration,
        token_input=_estimate_tokens(patch_content),
        token_output=token_out,
        details={"summary": summary, "findings_count": len(findings), "filepath": filepath},
    )

    row = ReviewSession.query.filter_by(session_id=session_id).first()
    if row and steps:
        row.checkpatch_output = steps[0].get("output_preview", "")
        row.updated_at = datetime.utcnow()
        if summary.get("failed", 0) == 0:
            row.status = "reviewed"
        db.session.commit()

    log_activity(f"Patch pipeline: {session_id} ({summary.get('overall', 'UNKNOWN')})", "review")
    return {
        "ok": True,
        "trace_id": trace_id,
        "session_id": session_id,
        "summary": summary,
        "steps": steps,
        "findings": findings,
        "token_budget": {
            "used_estimate": _estimate_tokens(patch_content) + token_out,
            "max": 131072,
        },
        "duration_ms": pipeline_duration,
    }


def _run_pipeline_job(app_obj: Any, job_id: str, session_id: str, patch_content: str, filepath: str, trace_id: str) -> None:
    _set_pipeline_job(
        job_id,
        {
            "job_id": job_id,
            "session_id": session_id,
            "trace_id": trace_id,
            "status": "running",
            "progress": 5,
            "message": "Pipeline started",
            "updated_at": datetime.utcnow().isoformat(),
        },
    )
    started = time.perf_counter()
    try:
        with app_obj.app_context():
            result = _execute_pipeline(
                session_id=session_id,
                patch_content=patch_content,
                filepath=filepath,
                trace_id=trace_id,
                progress_hook=lambda index, total, step: _set_pipeline_job(
                    job_id,
                    {
                        "status": "running",
                        "progress": int((index / max(total, 1)) * 100),
                        "current_step": step.get("name", ""),
                        "updated_at": datetime.utcnow().isoformat(),
                    },
                ),
            )
        _set_pipeline_job(
            job_id,
            {
                "status": "completed",
                "progress": 100,
                "result": result,
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "updated_at": datetime.utcnow().isoformat(),
            },
        )
    except Exception as exc:
        _set_pipeline_job(
            job_id,
            {
                "status": "failed",
                "progress": 100,
                "error": str(exc),
                "duration_ms": int((time.perf_counter() - started) * 1000),
                "updated_at": datetime.utcnow().isoformat(),
            },
        )


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


@patchwise_bp.post("/api/patchwise/upload")
def upload_patch():
    upload_dir = _ensure_upload_dir()
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "file is required"}), 400

    file_obj = request.files["file"]
    if not file_obj or not (file_obj.filename or "").strip():
        return jsonify({"ok": False, "error": "invalid file"}), 400

    token = str(uuid.uuid4())
    safe_name = secure_filename(file_obj.filename) or "patch.diff"
    dest_path = os.path.join(upload_dir, f"{token}_{safe_name}")
    file_obj.save(dest_path)
    _uploaded_files[token] = dest_path

    return jsonify(
        {
            "ok": True,
            "token": token,
            "filename": safe_name,
            "filepath": dest_path,
        }
    )


@patchwise_bp.post("/api/patchwise/review")
def review_patch():
    _ensure_review_session_schema()
    _ensure_patch_trace_schema()
    _ensure_upload_dir()
    review_started = time.perf_counter()
    trace_id = _new_trace_id("pwreview")
    payload = request.get_json() or {}
    session_id = (payload.get("session_id") or f"patch-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}").strip()
    patch_content, filepath, patch_error, patch_error_status = _resolve_patch_payload(payload)
    context_url = (payload.get("context_url") or "").strip()
    model = (payload.get("model") or get_default_model()).strip()

    if patch_error:
        _log_patch_trace(
            trace_id=trace_id,
            session_id=session_id,
            stage="ai_review",
            tool="qgenie",
            status="error",
            duration_ms=int((time.perf_counter() - review_started) * 1000),
            error_message=patch_error,
            details={"filepath": filepath},
        )
        return jsonify({"ok": False, "error": patch_error}), patch_error_status

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
    if filepath and not (metadata.get("patch_filename") or "").strip():
        metadata["patch_filename"] = os.path.basename(filepath)
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
    log_activity("Reviewed patch: " + (row.patch_filename or session_id), "review")
    _log_patch_trace(
        trace_id=trace_id,
        session_id=session_id,
        stage="ai_review",
        tool="qgenie",
        status="ok",
        duration_ms=int((time.perf_counter() - review_started) * 1000),
        token_input=_estimate_tokens(full_prompt),
        token_output=_estimate_tokens(ai_raw),
        details={
            "model": model,
            "findings_count": len(findings),
            "maintainers_count": len(maintainers),
            "context_url": context_url or "",
        },
    )

    return jsonify({
        "ok": True,
        "trace_id": trace_id,
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


@patchwise_bp.post("/api/patchwise/pipeline")
def run_pipeline():
    _ensure_review_session_schema()
    _ensure_patch_trace_schema()
    payload = request.get_json() or {}
    trace_id = _new_trace_id("pwpipeline")
    session_id = (payload.get("session_id") or f"pipeline-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}").strip()
    patch_content, filepath, patch_error, patch_error_status = _resolve_patch_payload(payload)
    if patch_error:
        _log_patch_trace(
            trace_id=trace_id,
            session_id=session_id,
            stage="pipeline",
            tool="pipeline",
            status="error",
            error_message=patch_error,
            details={"filepath": filepath},
        )
        return jsonify({"ok": False, "error": patch_error, "trace_id": trace_id}), patch_error_status

    result = _execute_pipeline(
        session_id=session_id,
        patch_content=patch_content,
        filepath=filepath,
        trace_id=trace_id,
    )
    return jsonify(result)


@patchwise_bp.post("/api/patchwise/pipeline/start")
def start_pipeline():
    _ensure_review_session_schema()
    _ensure_patch_trace_schema()
    payload = request.get_json() or {}
    session_id = (payload.get("session_id") or f"pipeline-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}").strip()
    patch_content, filepath, patch_error, patch_error_status = _resolve_patch_payload(payload)
    trace_id = _new_trace_id("pwpipeline")
    job_id = _new_trace_id("pwjob")
    if patch_error:
        _set_pipeline_job(
            job_id,
            {
                "job_id": job_id,
                "session_id": session_id,
                "trace_id": trace_id,
                "status": "failed",
                "progress": 100,
                "error": patch_error,
                "updated_at": datetime.utcnow().isoformat(),
            },
        )
        return jsonify({"ok": False, "error": patch_error, "job_id": job_id, "trace_id": trace_id}), patch_error_status

    _prune_pipeline_jobs(200)
    _set_pipeline_job(
        job_id,
        {
            "job_id": job_id,
            "session_id": session_id,
            "trace_id": trace_id,
            "status": "queued",
            "progress": 0,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        },
    )
    app_obj = current_app._get_current_object()
    worker = Thread(
        target=_run_pipeline_job,
        args=(app_obj, job_id, session_id, patch_content, filepath, trace_id),
        daemon=True,
    )
    worker.start()
    return jsonify(
        {
            "ok": True,
            "job_id": job_id,
            "session_id": session_id,
            "trace_id": trace_id,
            "status": "queued",
        }
    )


@patchwise_bp.get("/api/patchwise/pipeline/status/<job_id>")
def pipeline_status(job_id: str):
    row = _get_pipeline_job(job_id)
    if not row:
        return jsonify({"ok": False, "error": "job not found"}), 404
    return jsonify({"ok": True, **row})


@patchwise_bp.post("/api/patchwise/autofix/preview")
def preview_autofix():
    _ensure_patch_trace_schema()
    payload = request.get_json() or {}
    session_id = (payload.get("session_id") or "autofix-preview").strip()
    trace_id = _new_trace_id("pwautofix")
    started = time.perf_counter()
    patch_content, filepath, patch_error, patch_error_status = _resolve_patch_payload(payload)
    if patch_error:
        _log_patch_trace(
            trace_id=trace_id,
            session_id=session_id,
            stage="autofix_preview",
            tool="autofix",
            status="error",
            error_message=patch_error,
            details={"filepath": filepath},
        )
        return jsonify({"ok": False, "error": patch_error, "trace_id": trace_id}), patch_error_status

    accepted_fix_ids = payload.get("accepted_fix_ids", None)
    if accepted_fix_ids is not None and not isinstance(accepted_fix_ids, list):
        accepted_fix_ids = None

    result = _apply_autofixes(patch_content, accepted_fix_ids=accepted_fix_ids)
    _log_patch_trace(
        trace_id=trace_id,
        session_id=session_id,
        stage="autofix_preview",
        tool="autofix",
        status="ok",
        duration_ms=int((time.perf_counter() - started) * 1000),
        token_input=_estimate_tokens(patch_content),
        token_output=_estimate_tokens(result.get("diff", "")),
        details={
            "applied_count": len(result.get("applied_fixes", [])),
            "changed_line_count": result.get("changed_line_count", 0),
            "filepath": filepath,
        },
    )

    return jsonify(
        {
            "ok": True,
            "trace_id": trace_id,
            "session_id": session_id,
            "filepath": filepath,
            **result,
        }
    )


@patchwise_bp.post("/api/patchwise/autofix/apply")
def apply_autofix():
    _ensure_patch_trace_schema()
    payload = request.get_json() or {}
    session_id = (payload.get("session_id") or "autofix-apply").strip()
    trace_id = _new_trace_id("pwautofix")
    started = time.perf_counter()
    patch_content, filepath, patch_error, patch_error_status = _resolve_patch_payload(payload)
    if patch_error:
        _log_patch_trace(
            trace_id=trace_id,
            session_id=session_id,
            stage="autofix_apply",
            tool="autofix",
            status="error",
            error_message=patch_error,
            details={"filepath": filepath},
        )
        return jsonify({"ok": False, "error": patch_error, "trace_id": trace_id}), patch_error_status

    accepted_fix_ids = payload.get("accepted_fix_ids", None)
    if accepted_fix_ids is not None and not isinstance(accepted_fix_ids, list):
        accepted_fix_ids = None

    result = _apply_autofixes(patch_content, accepted_fix_ids=accepted_fix_ids)
    if not result.get("has_changes"):
        _log_patch_trace(
            trace_id=trace_id,
            session_id=session_id,
            stage="autofix_apply",
            tool="autofix",
            status="ok",
            duration_ms=int((time.perf_counter() - started) * 1000),
            token_input=_estimate_tokens(patch_content),
            token_output=0,
            details={"message": "No changes needed", "filepath": filepath},
        )
        return jsonify({"ok": True, "trace_id": trace_id, "session_id": session_id, "message": "No autofix changes needed", **result})

    persisted = False
    backup_path = ""
    target_path = (payload.get("target_path") or filepath or "").strip()
    if target_path:
        if not _is_allowed_patch_path(target_path):
            _log_patch_trace(
                trace_id=trace_id,
                session_id=session_id,
                stage="autofix_apply",
                tool="autofix",
                status="error",
                error_message="Path not allowed",
                details={"target_path": target_path},
            )
            return jsonify({"ok": False, "error": "Path not allowed", "trace_id": trace_id}), 403
        if os.path.isfile(target_path):
            backup_path = _create_backup(session_id, target_path, patch_content)
        with open(target_path, "w", encoding="utf-8", errors="replace") as handle:
            handle.write(result.get("fixed_content", ""))
        persisted = True
        log_activity(f"Autofix applied: {os.path.basename(target_path)}", "review")

    _log_patch_trace(
        trace_id=trace_id,
        session_id=session_id,
        stage="autofix_apply",
        tool="autofix",
        status="ok",
        duration_ms=int((time.perf_counter() - started) * 1000),
        token_input=_estimate_tokens(patch_content),
        token_output=_estimate_tokens(result.get("diff", "")),
        details={
            "persisted": persisted,
            "backup_path": backup_path,
            "target_path": target_path,
            "applied_count": len(result.get("applied_fixes", [])),
        },
    )

    return jsonify(
        {
            "ok": True,
            "trace_id": trace_id,
            "session_id": session_id,
            "persisted": persisted,
            "backup_path": backup_path,
            "target_path": target_path,
            **result,
        }
    )


@patchwise_bp.post("/api/patchwise/autofix/rollback")
def rollback_autofix():
    _ensure_patch_trace_schema()
    payload = request.get_json() or {}
    session_id = (payload.get("session_id") or "autofix-rollback").strip()
    trace_id = _new_trace_id("pwautofix")
    started = time.perf_counter()
    target_path = (payload.get("target_path") or "").strip()
    if not target_path:
        return jsonify({"ok": False, "error": "target_path is required", "trace_id": trace_id}), 400
    if not _is_allowed_patch_path(target_path):
        return jsonify({"ok": False, "error": "Path not allowed", "trace_id": trace_id}), 403

    backup_path = _autofix_backups.get(_backup_key(session_id, target_path), "")
    if not backup_path or not os.path.isfile(backup_path):
        _log_patch_trace(
            trace_id=trace_id,
            session_id=session_id,
            stage="autofix_rollback",
            tool="autofix",
            status="error",
            error_message="No backup found",
            details={"target_path": target_path},
        )
        return jsonify({"ok": False, "error": "No backup found for rollback", "trace_id": trace_id}), 404

    try:
        shutil.copyfile(backup_path, target_path)
        with open(target_path, "r", encoding="utf-8", errors="replace") as handle:
            restored_content = handle.read()
    except Exception as exc:
        _log_patch_trace(
            trace_id=trace_id,
            session_id=session_id,
            stage="autofix_rollback",
            tool="autofix",
            status="error",
            error_message=str(exc),
            details={"target_path": target_path, "backup_path": backup_path},
        )
        return jsonify({"ok": False, "error": f"Rollback failed: {exc}", "trace_id": trace_id}), 500

    log_activity(f"Autofix rollback: {os.path.basename(target_path)}", "review")
    _log_patch_trace(
        trace_id=trace_id,
        session_id=session_id,
        stage="autofix_rollback",
        tool="autofix",
        status="ok",
        duration_ms=int((time.perf_counter() - started) * 1000),
        token_output=_estimate_tokens(restored_content),
        details={"target_path": target_path, "backup_path": backup_path},
    )
    return jsonify(
        {
            "ok": True,
            "trace_id": trace_id,
            "session_id": session_id,
            "target_path": target_path,
            "backup_path": backup_path,
            "content": restored_content,
        }
    )


@patchwise_bp.get("/api/patchwise/traces")
def list_patch_traces():
    _ensure_patch_trace_schema()
    session_id = (request.args.get("session_id") or "").strip()
    trace_id = (request.args.get("trace_id") or "").strip()
    try:
        limit = max(1, min(200, int(request.args.get("limit", "80"))))
    except Exception:
        limit = 80

    query = PatchReviewTrace.query.order_by(PatchReviewTrace.created_at.desc(), PatchReviewTrace.id.desc())
    if session_id:
        query = query.filter(PatchReviewTrace.session_id == session_id)
    if trace_id:
        query = query.filter(PatchReviewTrace.trace_id == trace_id)

    rows = query.limit(limit).all()
    return jsonify(
        {
            "ok": True,
            "rows": [
                {
                    "id": row.id,
                    "trace_id": row.trace_id,
                    "session_id": row.session_id,
                    "stage": row.stage,
                    "tool": row.tool,
                    "status": row.status,
                    "duration_ms": row.duration_ms,
                    "exit_code": row.exit_code,
                    "token_input": row.token_input,
                    "token_output": row.token_output,
                    "error_message": row.error_message or "",
                    "details": _json_load(row.details_json, {}),
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ],
        }
    )


@patchwise_bp.get("/api/patchwise/analytics")
def patchwise_analytics():
    _ensure_patch_trace_schema()
    session_id = (request.args.get("session_id") or "").strip()
    try:
        limit = max(10, min(1000, int(request.args.get("limit", "400"))))
    except Exception:
        limit = 400

    query = PatchReviewTrace.query.order_by(PatchReviewTrace.created_at.desc(), PatchReviewTrace.id.desc())
    if session_id:
        query = query.filter(PatchReviewTrace.session_id == session_id)
    rows = query.limit(limit).all()

    if not rows:
        return jsonify(
            {
                "ok": True,
                "summary": {
                    "total_events": 0,
                    "distinct_traces": 0,
                    "distinct_sessions": 0,
                    "token_input_total": 0,
                    "token_output_total": 0,
                    "duration_total_ms": 0,
                    "duration_avg_ms": 0,
                    "error_events": 0,
                    "success_events": 0,
                },
                "stage_breakdown": [],
            }
        )

    distinct_traces = {row.trace_id for row in rows if row.trace_id}
    distinct_sessions = {row.session_id for row in rows if row.session_id}
    token_input_total = sum(int(row.token_input or 0) for row in rows)
    token_output_total = sum(int(row.token_output or 0) for row in rows)
    duration_total_ms = sum(int(row.duration_ms or 0) for row in rows)
    error_events = sum(1 for row in rows if (row.status or "").lower() in {"error", "fail"})
    success_events = sum(1 for row in rows if (row.status or "").lower() in {"ok", "pass"})

    stage_agg: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key = row.stage or "unknown"
        item = stage_agg.setdefault(
            key,
            {
                "stage": key,
                "count": 0,
                "errors": 0,
                "duration_ms": 0,
                "token_input": 0,
                "token_output": 0,
            },
        )
        item["count"] += 1
        item["duration_ms"] += int(row.duration_ms or 0)
        item["token_input"] += int(row.token_input or 0)
        item["token_output"] += int(row.token_output or 0)
        if (row.status or "").lower() in {"error", "fail"}:
            item["errors"] += 1

    stage_breakdown = sorted(stage_agg.values(), key=lambda item: item["count"], reverse=True)
    return jsonify(
        {
            "ok": True,
            "summary": {
                "total_events": len(rows),
                "distinct_traces": len(distinct_traces),
                "distinct_sessions": len(distinct_sessions),
                "token_input_total": token_input_total,
                "token_output_total": token_output_total,
                "duration_total_ms": duration_total_ms,
                "duration_avg_ms": int(duration_total_ms / max(len(rows), 1)),
                "error_events": error_events,
                "success_events": success_events,
                "error_rate_pct": round((error_events * 100.0) / max(len(rows), 1), 2),
            },
            "stage_breakdown": stage_breakdown[:24],
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
