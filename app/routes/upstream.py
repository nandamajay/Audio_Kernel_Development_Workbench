"""Upstream patch tracker routes."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Dict

import requests
from bs4 import BeautifulSoup
from flask import Blueprint, current_app, jsonify, render_template, request

from app.config import get_default_model
from app.models import UpstreamPatch, db
from app.services.activity_service import log_activity
from app.services.env_service import resolve_ssl_verify


upstream_bp = Blueprint("upstream", __name__)

STATUS_VALUES = {
    "submitted",
    "under_review",
    "changes_requested",
    "accepted",
    "merged",
    "superseded",
    "rejected",
}


def _verify_value() -> bool | str:
    return resolve_ssl_verify(
        ssl_verify_raw=current_app.config.get("QGENIE_SSL_VERIFY", "true"),
        ca_bundle=current_app.config.get("QGENIE_CA_BUNDLE", ""),
    )


def _to_dt(raw: str) -> datetime | None:
    try:
        dt = parsedate_to_datetime(raw)
        if dt:
            return dt.replace(tzinfo=None)
    except Exception:
        return None
    return None


def _infer_subsystem(title: str) -> str:
    low = (title or "").lower()
    if "asoc" in low or "sound" in low or "codec" in low:
        return "ASoC"
    if "usb" in low:
        return "USB"
    if "net" in low:
        return "Networking"
    if "arm64" in low:
        return "arm64"
    return "Kernel"


def _detect_source(url: str) -> str:
    low = (url or "").lower()
    if "lore.kernel.org" in low:
        return "lore"
    if "github.com" in low:
        return "github"
    if "gerrit" in low:
        return "gerrit"
    return "unknown"


def _default_submitter() -> str:
    # Prefer configured settings/env; fall back to known submitter address.
    values = {
        os.environ.get("USER_EMAIL", "").strip(),
        os.environ.get("QGENIE_USER_EMAIL", "").strip(),
        "ajay.nandam@oss.qualcomm.com",
    }
    for value in values:
        if value and "@" in value:
            return value
    return "ajay.nandam@oss.qualcomm.com"


def _infer_status(text_blob: str, reply_count: int) -> str:
    low = (text_blob or "").lower()
    if "rejected" in low or "nacked-by" in low:
        return "rejected"
    if "merged" in low or "applied" in low:
        return "merged"
    if "accepted" in low:
        return "accepted"
    if "changes requested" in low or "please fix" in low or "please resend" in low:
        return "changes_requested"
    if reply_count > 0:
        return "under_review"
    return "submitted"


def _llm_status_summary(url: str, title: str, reply_count: int, latest_reply: str) -> Dict[str, str]:
    prompt = (
        "Kernel patch thread URL: " + url + "\n"
        "Subject: " + title + "\n"
        "Reply count: " + str(reply_count) + "\n"
        "Latest reply snippet: " + (latest_reply or "")[:500] + "\n\n"
        "Summarize in JSON:\n"
        "{\n"
        '  "status": "submitted|under_review|changes_requested|accepted|merged|rejected",\n'
        '  "summary": "one sentence status summary",\n'
        '  "action_needed": "what the submitter should do next"\n'
        "}\n"
    )
    service = current_app.extensions.get("agent_service")
    if not service:
        return {}
    try:
        raw = service._try_qgenie_chat(get_default_model(), prompt) or ""
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            return {}
        data = json.loads(match.group(0))
        if not isinstance(data, dict):
            return {}
        return {
            "status": str(data.get("status", "")).strip(),
            "summary": str(data.get("summary", "")).strip(),
            "action_needed": str(data.get("action_needed", "")).strip(),
        }
    except Exception:
        return {}


def _fetch_lore_metadata(url: str) -> Dict[str, Any]:
    verify = _verify_value()
    title = url
    series_id = ""
    submitter = _default_submitter()
    submitted_at = None
    reviewer_comments = ""
    merged_tree = ""
    tags = []
    reply_count = 0
    latest_reply = ""
    low_blob = ""

    try:
        resp = requests.get(url, timeout=12, verify=verify, headers={"User-Agent": "AKDW/1.0"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        title = (soup.title.get_text(strip=True) if soup.title else "") or title
        h1 = soup.find("h1")
        if h1 and h1.get_text(strip=True):
            title = h1.get_text(strip=True)

        text_blob = soup.get_text("\n", strip=True)
        low_blob = text_blob.lower()

        m_id = re.search(r"message-id:\s*<?([^>\s]+)>?", text_blob, flags=re.IGNORECASE)
        if m_id:
            series_id = m_id.group(1).strip()
        else:
            from_url = re.search(r"/([A-Za-z0-9_.+-]+@[A-Za-z0-9_.-]+)/?", url)
            if from_url:
                series_id = from_url.group(1)

        m_from = re.search(r"from:\s*(.+)", text_blob, flags=re.IGNORECASE)
        if m_from:
            submitter = m_from.group(1).strip()[:250]

        m_date = re.search(r"date:\s*(.+)", text_blob, flags=re.IGNORECASE)
        if m_date:
            submitted_at = _to_dt(m_date.group(1).strip())

        reply_count = len(re.findall(r"\nre:\s", low_blob))
        if reply_count == 0:
            guess_reply = re.search(r"(\d+)\s+repl(?:y|ies)", low_blob)
            if guess_reply:
                try:
                    reply_count = int(guess_reply.group(1))
                except Exception:
                    reply_count = 0

        for tag in ["acked-by:", "reviewed-by:", "tested-by:", "applied", "merged", "nacked-by:"]:
            if tag in low_blob:
                tags.append(tag.replace(":", ""))

        pre_blocks = soup.find_all("pre")
        if pre_blocks:
            latest_reply = pre_blocks[-1].get_text("\n", strip=True)[:800]
            reviewer_comments = latest_reply[:360]

        applied_match = re.search(r"applied to ([A-Za-z0-9/_-]+)", low_blob)
        if applied_match:
            merged_tree = applied_match.group(1)
    except Exception as exc:
        reviewer_comments = f"Metadata fetch failed: {exc}"

    inferred_status = _infer_status(low_blob, reply_count)
    ai_summary = _llm_status_summary(url, title, reply_count, latest_reply)
    if ai_summary.get("status") in STATUS_VALUES:
        inferred_status = ai_summary["status"]
    if ai_summary.get("summary"):
        reviewer_comments = ai_summary["summary"]
    if ai_summary.get("action_needed"):
        reviewer_comments = (reviewer_comments + " Next: " + ai_summary["action_needed"]).strip()

    return {
        "title": title,
        "series_id": series_id,
        "submitter": submitter or _default_submitter(),
        "subsystem": _infer_subsystem(title),
        "submitted_at": submitted_at,
        "status": inferred_status,
        "last_checked": datetime.utcnow(),
        "reviewer_comments": reviewer_comments,
        "merged_tree": merged_tree,
        "tags": ",".join(sorted(set(tags))),
    }


def _patch_to_dict(row: UpstreamPatch) -> Dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "lore_url": row.lore_url,
        "series_id": row.series_id,
        "submitter": row.submitter,
        "subsystem": row.subsystem,
        "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
        "status": row.status or "submitted",
        "last_checked": row.last_checked.isoformat() if row.last_checked else None,
        "reviewer_comments": row.reviewer_comments or "",
        "merged_tree": row.merged_tree or "",
        "tags": row.tags or "",
        "notes": row.notes or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@upstream_bp.get("/upstream")
@upstream_bp.get("/upstream/")
def upstream_home():
    return render_template("upstream.html")


@upstream_bp.get("/api/upstream/list")
def upstream_list():
    rows = UpstreamPatch.query.order_by(UpstreamPatch.created_at.desc()).all()
    return jsonify([_patch_to_dict(row) for row in rows])


@upstream_bp.get("/api/upstream/stats")
def upstream_stats():
    rows = UpstreamPatch.query.all()
    stats = {"total": len(rows)}
    for status in STATUS_VALUES:
        stats[status] = 0
    for row in rows:
        key = row.status or "submitted"
        stats[key] = stats.get(key, 0) + 1
    return jsonify(stats)


@upstream_bp.post("/api/upstream/add")
def upstream_add():
    payload = request.get_json() or {}
    url = (payload.get("url") or "").strip()
    notes = (payload.get("notes") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "Valid URL is required"}), 400

    row = UpstreamPatch.query.filter_by(lore_url=url).first()
    if row:
        if notes:
            row.notes = notes
            db.session.commit()
        return jsonify(_patch_to_dict(row))

    meta = _fetch_lore_metadata(url)
    forced_title = (payload.get("title") or "").strip()
    forced_status = (payload.get("status") or "").strip()
    source = _detect_source(url)
    row = UpstreamPatch(
        title=forced_title or meta.get("title") or url,
        lore_url=url,
        series_id=meta.get("series_id"),
        submitter=meta.get("submitter") or _default_submitter(),
        subsystem=meta.get("subsystem"),
        submitted_at=meta.get("submitted_at"),
        status=(forced_status if forced_status in STATUS_VALUES else meta.get("status")) or "submitted",
        last_checked=meta.get("last_checked"),
        reviewer_comments=((meta.get("reviewer_comments") or "") + (" [source:" + source + "]")).strip(),
        merged_tree=meta.get("merged_tree"),
        tags=meta.get("tags"),
        notes=notes,
    )
    db.session.add(row)
    db.session.commit()
    log_activity("Added patch to tracker: " + (row.title or row.lore_url or "")[:120], "upstream")
    return jsonify(_patch_to_dict(row))


@upstream_bp.put("/api/upstream/<int:patch_id>")
def upstream_update(patch_id: int):
    row = UpstreamPatch.query.get(patch_id)
    if not row:
        return jsonify({"ok": False, "error": "Patch not found"}), 404
    payload = request.get_json() or {}
    status = (payload.get("status") or "").strip()
    if status:
        row.status = status if status in STATUS_VALUES else row.status
    if "notes" in payload:
        row.notes = (payload.get("notes") or "").strip()
    if "reviewer_comments" in payload:
        row.reviewer_comments = (payload.get("reviewer_comments") or "").strip()
    row.last_checked = datetime.utcnow()
    db.session.commit()
    return jsonify(_patch_to_dict(row))


@upstream_bp.delete("/api/upstream/<int:patch_id>")
def upstream_delete(patch_id: int):
    row = UpstreamPatch.query.get(patch_id)
    if not row:
        return jsonify({"ok": False, "error": "Patch not found"}), 404
    db.session.delete(row)
    db.session.commit()
    return jsonify({"ok": True})


@upstream_bp.post("/api/upstream/<int:patch_id>/refresh")
def upstream_refresh(patch_id: int):
    row = UpstreamPatch.query.get(patch_id)
    if not row:
        return jsonify({"ok": False, "error": "Patch not found"}), 404

    meta = _fetch_lore_metadata(row.lore_url)
    row.title = meta.get("title") or row.title
    row.series_id = meta.get("series_id") or row.series_id
    row.submitter = meta.get("submitter") or row.submitter
    row.subsystem = meta.get("subsystem") or row.subsystem
    row.submitted_at = meta.get("submitted_at") or row.submitted_at
    row.status = meta.get("status") or row.status
    row.last_checked = meta.get("last_checked") or datetime.utcnow()
    row.reviewer_comments = meta.get("reviewer_comments") or row.reviewer_comments
    row.merged_tree = meta.get("merged_tree") or row.merged_tree
    row.tags = meta.get("tags") or row.tags
    db.session.commit()
    return jsonify(_patch_to_dict(row))
