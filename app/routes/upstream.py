"""Upstream patch tracker routes."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict
from urllib.parse import quote
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup
from flask import Blueprint, current_app, jsonify, render_template, request
from werkzeug.utils import secure_filename

from app.config import get_default_model
from app.models import UpstreamOfflineCache, UpstreamPatch, UpstreamSeries, db
from app.scripts.upstream_parser import enrich_series, parse_mbox_gz, summary_from_series
from app.services.activity_service import log_activity
from app.services.env_service import resolve_ssl_verify
from app.services.settings_service import get_json_setting, get_setting, save_setting


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
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
LORE_FEED = "https://lore.kernel.org/all/?q=f:{email}&x=A"
PATCHWORK_API = "https://patchwork.kernel.org/api/patches/?submitter={email}&format=json"


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


def _primary_email() -> str:
    email = (get_setting("user_email", "") or "").strip()
    if EMAIL_RE.match(email):
        return email
    display = os.environ.get("USER_DISPLAY_NAME", "").strip()
    if "@" in display and EMAIL_RE.match(display):
        return display
    fallback = _default_submitter()
    return fallback if EMAIL_RE.match(fallback) else ""


def _tracked_emails() -> list[str]:
    emails_raw = get_json_setting("upstream_tracked_emails", [])
    emails: list[str] = []
    if isinstance(emails_raw, list):
        for item in emails_raw:
            candidate = str(item or "").strip()
            if EMAIL_RE.match(candidate) and candidate not in emails:
                emails.append(candidate)
    primary = _primary_email()
    if primary and primary not in emails:
        emails.insert(0, primary)
    return emails


def _persist_tracked_emails(emails: list[str]) -> None:
    normalized: list[str] = []
    for item in emails:
        candidate = str(item or "").strip()
        if EMAIL_RE.match(candidate) and candidate not in normalized:
            normalized.append(candidate)
    save_setting("upstream_tracked_emails", json.dumps(normalized))


def _dedupe_patches(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    deduped = []
    for row in rows:
        subject = str(row.get("subject", "")).strip()
        date = str(row.get("date", "")).strip()
        key = (subject[:80].lower(), date[:10], str(row.get("msgid", ""))[:120])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _fetch_patchwork_by_email(email: str, verify: bool | str) -> list[dict[str, Any]]:
    patches: list[dict[str, Any]] = []
    try:
        res = requests.get(
            PATCHWORK_API.format(email=quote(email)),
            timeout=12,
            verify=verify,
            headers={"User-Agent": "AKDW/1.0"},
        )
        if res.status_code != 200:
            return patches
        payload = res.json()
        if not isinstance(payload, list):
            return patches
        for item in payload[:80]:
            project = item.get("project") if isinstance(item.get("project"), dict) else {}
            patches.append(
                {
                    "subject": item.get("name", ""),
                    "date": item.get("date", ""),
                    "list": project.get("name", ""),
                    "state": item.get("state", "unknown"),
                    "url": item.get("web_url", ""),
                    "msgid": item.get("msgid", ""),
                    "source": "patchwork",
                }
            )
    except Exception:
        return patches
    return patches


def _fetch_lore_by_email(email: str, verify: bool | str) -> list[dict[str, Any]]:
    patches: list[dict[str, Any]] = []
    try:
        res = requests.get(
            LORE_FEED.format(email=quote(email)),
            timeout=12,
            verify=verify,
            headers={"User-Agent": "AKDW/1.0"},
        )
        res.raise_for_status()
        root = ET.fromstring(res.text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)
        for entry in entries[:60]:
            title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
            published = (entry.findtext("atom:published", default="", namespaces=ns) or "").strip()
            msgid = (entry.findtext("atom:id", default="", namespaces=ns) or "").strip()
            link_node = entry.find("atom:link", ns)
            link = (link_node.get("href") if link_node is not None else "") or ""
            category = entry.find("atom:category", ns)
            list_name = (category.get("term") if category is not None else "") or ""
            patches.append(
                {
                    "subject": title,
                    "date": published,
                    "list": list_name,
                    "state": "unknown",
                    "url": link,
                    "msgid": msgid,
                    "source": "lore",
                }
            )
    except Exception:
        return patches
    return patches


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


def _status_to_visual(status: str) -> str:
    low = (status or "").strip().lower()
    if low in {"merged", "accepted"}:
        return "MERGED"
    if low in {"under_review", "changes_requested"}:
        return "REVIEWED_NOT_MERGED"
    return "PENDING"


def _series_row_to_dict(row: UpstreamSeries) -> Dict[str, Any]:
    def _j(raw: str, fallback):
        try:
            return json.loads(raw) if raw else fallback
        except Exception:
            return fallback

    return {
        "id": row.id,
        "title": row.title or "",
        "status": row.status or "PENDING",
        "versions": _j(row.versions, []),
        "version_count": int(row.version_count or 1),
        "final_patch_count": int(row.final_patch_count or 0),
        "v1_posted": row.v1_posted,
        "vN_posted": row.vN_posted,
        "days_to_merge": row.days_to_merge,
        "apply_date": row.apply_date,
        "days_to_apply": row.days_to_apply,
        "apply_basis": row.apply_basis,
        "maintainer_delay_days": row.maintainer_delay_days,
        "reviewed_by_count": int(row.reviewed_by_count or 0),
        "first_review_date": row.first_review_date,
        "days_to_first_review": row.days_to_first_review,
        "reviewers": _j(row.reviewers, []),
        "added_lines": int(row.added_lines or 0),
        "removed_lines": int(row.removed_lines or 0),
        "net_lines": int(row.net_lines or 0),
        "commit_shas": _j(row.commit_shas, []),
        "lore_url": row.lore_url or "",
        "fetch_mode": row.fetch_mode or "live",
        "last_updated": row.last_updated,
    }


def _upsert_series_cache(series: list[dict[str, Any]], fetch_mode: str, last_updated: str) -> None:
    for item in series:
        sid = str(item.get("id") or "")
        if not sid:
            continue
        row = UpstreamSeries.query.get(sid)
        if not row:
            row = UpstreamSeries(id=sid)
            db.session.add(row)
        row.title = item.get("title")
        row.status = item.get("status")
        row.versions = json.dumps(item.get("versions") or [])
        row.version_count = int(item.get("version_count") or 1)
        row.final_patch_count = int(item.get("final_patch_count") or 0)
        row.v1_posted = item.get("v1_posted")
        row.vN_posted = item.get("vN_posted")
        row.days_to_merge = item.get("days_to_merge")
        row.apply_date = item.get("apply_date")
        row.days_to_apply = item.get("days_to_apply")
        row.apply_basis = item.get("apply_basis")
        row.maintainer_delay_days = item.get("maintainer_delay_days")
        row.reviewed_by_count = int(item.get("reviewed_by_count") or 0)
        row.first_review_date = item.get("first_review_date")
        row.days_to_first_review = item.get("days_to_first_review")
        row.reviewers = json.dumps(item.get("reviewers") or [])
        row.added_lines = int(item.get("added_lines") or 0)
        row.removed_lines = int(item.get("removed_lines") or 0)
        row.net_lines = int(item.get("net_lines") or 0)
        row.commit_shas = json.dumps(item.get("commit_shas") or [])
        row.lore_url = item.get("lore_url")
        row.fetch_mode = fetch_mode
        row.last_updated = last_updated
    db.session.commit()


def _live_series_from_db(author_email: str) -> list[dict[str, Any]]:
    rows = UpstreamPatch.query.order_by(UpstreamPatch.created_at.desc()).all()
    raw_rows: list[dict[str, Any]] = []
    status_by_id: dict[str, str] = {}
    for row in rows:
        sid = f"live-{row.id}"
        status_by_id[sid] = row.status or "submitted"
        raw_rows.append(
            {
                "id": sid,
                "title": row.title or row.lore_url or f"Patch {row.id}",
                "lore_url": row.lore_url or "",
                "url": row.lore_url or "",
                "date": (row.submitted_at or row.created_at or datetime.utcnow()).isoformat(),
                "summary": (row.reviewer_comments or "") + " " + (row.tags or ""),
                "messages": [
                    {
                        "subject": row.title or "",
                        "body": (row.reviewer_comments or "") + " " + (row.tags or ""),
                        "date": (row.submitted_at or row.created_at or datetime.utcnow()).isoformat(),
                        "is_patch": True,
                    }
                ],
            }
        )

    enriched = enrich_series(raw_rows, author_email)
    for item in enriched:
        raw_status = status_by_id.get(str(item.get("id")))
        if raw_status:
            item["status"] = _status_to_visual(raw_status)
    return enriched


def _stats_payload(series: list[dict[str, Any]], fetch_mode: str, author: str) -> dict[str, Any]:
    timestamp = datetime.utcnow().isoformat() + "Z"
    summary = summary_from_series(series)
    return {
        "series": series,
        "summary": summary,
        "fetch_mode": fetch_mode,
        "last_updated": timestamp,
        "author": author,
    }


@upstream_bp.get("/upstream")
@upstream_bp.get("/upstream/")
def upstream_home():
    return render_template("upstream.html", author_email=_primary_email() or _default_submitter())


@upstream_bp.get("/api/upstream/list")
def upstream_list():
    rows = UpstreamPatch.query.order_by(UpstreamPatch.created_at.desc()).all()
    return jsonify([_patch_to_dict(row) for row in rows])


@upstream_bp.get("/api/upstream/stats")
def upstream_stats():
    mode = str(request.args.get("mode", "live")).strip().lower()
    author = _primary_email() or _default_submitter()

    if mode == "offline":
        cache = UpstreamOfflineCache.query.order_by(UpstreamOfflineCache.id.desc()).first()
        if cache and cache.series_json:
            try:
                series = json.loads(cache.series_json)
            except Exception:
                series = []
            payload = _stats_payload(series, "offline", cache.author or author)
            payload["offline_filename"] = cache.filename or ""
            payload["last_updated"] = cache.uploaded_at or payload["last_updated"]
            return jsonify(payload)
        return jsonify(_stats_payload([], "offline", author))

    series = _live_series_from_db(author)
    payload = _stats_payload(series, "live", author)
    _upsert_series_cache(series, "live", payload["last_updated"])
    return jsonify(payload)


@upstream_bp.get("/api/upstream/summary")
def upstream_summary():
    mode = str(request.args.get("mode", "live")).strip().lower()
    stats = upstream_stats().get_json() or {}
    return jsonify(
        {
            "summary": stats.get("summary", {}),
            "fetch_mode": mode,
            "last_updated": stats.get("last_updated"),
            "author": stats.get("author", _primary_email() or _default_submitter()),
        }
    )


@upstream_bp.post("/api/upstream/upload-mbox")
def upstream_upload_mbox():
    incoming = request.files.get("mbox_file")
    if not incoming:
        return jsonify({"error": "mbox_file is required"}), 400

    filename = secure_filename(incoming.filename or "").strip()
    low_name = filename.lower()
    if not (low_name.endswith(".mbox") or low_name.endswith(".mbox.gz") or low_name.endswith(".gz")):
        return jsonify({"error": "Only .mbox or .mbox.gz files are supported"}), 400

    target_dir = Path("/tmp/akdw_upstream_uploads")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{filename}"
    incoming.save(str(target))

    author = _primary_email() or _default_submitter()
    try:
        series = parse_mbox_gz(str(target), author)
    except Exception as exc:
        return jsonify({"error": f"Failed to parse mbox: {exc}"}), 500

    uploaded_at = datetime.utcnow().isoformat() + "Z"
    cache = UpstreamOfflineCache(
        filename=filename,
        author=author,
        uploaded_at=uploaded_at,
        series_json=json.dumps(series),
    )
    db.session.add(cache)
    db.session.commit()

    _upsert_series_cache(series, "offline", uploaded_at)
    payload = _stats_payload(series, "offline", author)
    payload["offline_filename"] = filename
    payload["last_updated"] = uploaded_at
    return jsonify(payload)


@upstream_bp.get("/api/upstream/emails")
def get_tracked_emails():
    """Return list of tracked email IDs from settings."""
    return jsonify({"emails": _tracked_emails()})


@upstream_bp.post("/api/upstream/emails")
def add_tracked_email():
    """Add a tracked email and persist in settings."""
    payload = request.get_json(silent=True) or {}
    email = str(payload.get("email", "")).strip()
    if not EMAIL_RE.match(email):
        return jsonify({"error": "Invalid email"}), 400
    emails = _tracked_emails()
    if email not in emails:
        emails.append(email)
    _persist_tracked_emails(emails)
    return jsonify({"emails": _tracked_emails(), "added": email})


@upstream_bp.delete("/api/upstream/emails")
def remove_tracked_email():
    payload = request.get_json(silent=True) or {}
    email = str(payload.get("email", "")).strip()
    if not email:
        return jsonify({"error": "email required"}), 400
    emails = [item for item in _tracked_emails() if item != email]
    _persist_tracked_emails(emails)
    return jsonify({"emails": _tracked_emails(), "removed": email})


@upstream_bp.get("/api/upstream/fetch")
def fetch_patches_for_email():
    """Fetch patches submitted by email from patchwork and lore atom feed."""
    email = str(request.args.get("email", "")).strip()
    if not EMAIL_RE.match(email):
        return jsonify({"error": "email required"}), 400

    verify = _verify_value()
    patches: list[dict[str, Any]] = []
    patches.extend(_fetch_patchwork_by_email(email, verify))
    patches.extend(_fetch_lore_by_email(email, verify))
    deduped = _dedupe_patches(patches)

    return jsonify(
        {
            "email": email,
            "patches": deduped,
            "count": len(deduped),
            "fetched": datetime.utcnow().isoformat(),
        }
    )


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
