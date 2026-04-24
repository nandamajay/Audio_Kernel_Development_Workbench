"""Triage routes."""

from __future__ import annotations

import json
import re
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

from app.models import TriageSession, db


triage_bp = Blueprint("triage", __name__)


def _detect_location(text: str) -> str:
    patterns = [
        re.compile(r"\b(?:RIP|IP|pc)\s*[:=]\s*(?:[0-9a-fx:]+)?\s*([A-Za-z0-9_./-]+)\+0x", re.IGNORECASE),
        re.compile(r"\b([A-Za-z0-9_./-]+\.(?:c|h)):(\d+)"),
        re.compile(r"\bCall Trace:\s*\n(?:.*\n){0,3}.*?\b([A-Za-z0-9_./-]+)\+0x", re.IGNORECASE),
    ]
    for pattern in patterns:
        m = pattern.search(text)
        if not m:
            continue
        if m.lastindex and m.lastindex > 1:
            return f"{m.group(1)}:{m.group(2)}"
        return m.group(1)
    return "Unknown (stack trace location not detected)"


def _build_triage_result(log_text: str) -> dict:
    low = (log_text or "").lower()
    location = _detect_location(log_text)

    if "null pointer" in low or "unable to handle kernel paging request" in low:
        return {
            "root_cause": "NULL pointer dereference in kernel path during audio flow handling.",
            "location": location,
            "suggested_fix": "Add NULL checks before dereference and validate probe/initialization ordering.",
            "subsystem": "ASoC / Audio",
            "maintainer": "Mark Brown <broonie@kernel.org>",
            "severity": "high",
        }

    if "lockdep" in low or "circular locking" in low:
        return {
            "root_cause": "Potential lock ordering violation detected by lockdep.",
            "location": location,
            "suggested_fix": "Review lock acquisition order and enforce a single lock hierarchy across code paths.",
            "subsystem": "Kernel Locking",
            "maintainer": "Peter Zijlstra <peterz@infradead.org>",
            "severity": "medium",
        }

    if "dapm" in low or "snd_soc" in low or "q6asm" in low or "audio" in low:
        return {
            "root_cause": "Audio subsystem runtime path failure detected in ASoC/QDSP flow.",
            "location": location,
            "suggested_fix": "Validate DAPM widget states, stream startup sequencing, and codec bring-up checks.",
            "subsystem": "ASoC / DAPM",
            "maintainer": "Mark Brown <broonie@kernel.org>",
            "severity": "medium",
        }

    if "panic" in low or "bug:" in low:
        return {
            "root_cause": "Kernel panic/oops detected; likely invalid state transition or unchecked pointer access.",
            "location": location,
            "suggested_fix": "Inspect first faulting frame, add defensive checks, and reproduce with debug symbols enabled.",
            "subsystem": "Core Kernel",
            "maintainer": "linux-kernel@vger.kernel.org",
            "severity": "high",
        }

    return {
        "root_cause": "Generic kernel warning without a direct signature match.",
        "location": location,
        "suggested_fix": "Collect complete dmesg context and rerun with debug config options enabled for targeted diagnosis.",
        "subsystem": "Unknown",
        "maintainer": "linux-kernel@vger.kernel.org",
        "severity": "low",
    }


@triage_bp.get("/triage")
@triage_bp.get("/triage/")
def triage_home():
    return render_template("triage.html")


@triage_bp.post("/api/triage/analyze")
def triage_analyze():
    payload = request.get_json() or {}
    crash_log = (payload.get("crash_log") or payload.get("log") or "").strip()
    if not crash_log:
        return jsonify({"ok": False, "error": "crash_log is required"}), 400

    result = _build_triage_result(crash_log)
    session = TriageSession(
        input_type="log",
        input_payload=crash_log,
        report=json.dumps(result),
        created_at=datetime.utcnow(),
    )
    db.session.add(session)
    db.session.commit()
    return jsonify(result)
