"""Evidence API routes for patch review cards."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from flask import Blueprint, jsonify, request

from app.models import ReviewEvidence, db
from app.services.env_service import resolve_ssl_verify


evidence_bp = Blueprint("evidence", __name__)


def _verify_value() -> bool | str:
    return resolve_ssl_verify(
        ssl_verify_raw=os.environ.get("QGENIE_SSL_VERIFY", "true"),
        ca_bundle=os.environ.get("QGENIE_CA_BUNDLE", ""),
    )


def _parse_preview(url: str) -> dict:
    verify = _verify_value()
    fallback = {"title": url, "author": "Unknown", "date": "", "url": url}
    try:
        resp = requests.get(url, timeout=5, verify=verify, headers={"User-Agent": "AKDW/1.0"})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text("\n")
        title = (soup.title.get_text(strip=True) if soup.title else url) or url

        author = "Unknown"
        date = ""
        m_from = re.search(r"^From:\s*(.+)$", text, flags=re.MULTILINE)
        if m_from:
            author = m_from.group(1).strip()[:200]
        m_date = re.search(r"^Date:\s*(.+)$", text, flags=re.MULTILINE)
        if m_date:
            date = m_date.group(1).strip()[:200]

        return {"title": title, "author": author, "date": date, "url": url}
    except Exception:
        return fallback


@evidence_bp.post("/api/evidence/attach_screenshot")
def attach_screenshot():
    payload = request.get_json() or {}
    session_id = (payload.get("session_id") or "").strip()
    finding_id = (payload.get("finding_id") or "").strip()
    image_base64 = (payload.get("image_base64") or "").strip()

    if not session_id or not finding_id or not image_base64:
        return jsonify({"ok": False, "error": "session_id, finding_id, image_base64 are required"}), 400

    row = ReviewEvidence(
        session_id=session_id,
        finding_id=finding_id,
        evidence_type="screenshot",
        content=image_base64,
        metadata_json=json.dumps({}),
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"ok": True, "evidence_id": row.id})


@evidence_bp.post("/api/evidence/lkml_preview")
def lkml_preview():
    payload = request.get_json() or {}
    url = (payload.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "url is required"}), 400

    allowed_prefixes = (
        "https://lore.kernel.org",
        "https://patchwork.",
        "https://github.com",
    )
    if not url.startswith(allowed_prefixes):
        return jsonify({"title": url, "author": "Unknown", "date": "", "url": url})

    return jsonify(_parse_preview(url))


@evidence_bp.post("/api/evidence/save_lkml")
def save_lkml():
    payload = request.get_json() or {}
    session_id = (payload.get("session_id") or "").strip()
    finding_id = (payload.get("finding_id") or "").strip()
    url = (payload.get("url") or "").strip()
    title = (payload.get("title") or url).strip()
    author = (payload.get("author") or "Unknown").strip()
    date = (payload.get("date") or "").strip()

    if not session_id or not finding_id or not url:
        return jsonify({"ok": False, "error": "session_id, finding_id, url are required"}), 400

    row = ReviewEvidence(
        session_id=session_id,
        finding_id=finding_id,
        evidence_type="lkml",
        content=url,
        metadata_json=json.dumps({"title": title, "author": author, "date": date}),
    )
    db.session.add(row)
    db.session.commit()
    return jsonify({"ok": True, "evidence_id": row.id})


@evidence_bp.get("/api/evidence/list/<session_id>")
def list_evidence(session_id: str):
    rows = ReviewEvidence.query.filter_by(session_id=session_id).order_by(ReviewEvidence.created_at.asc()).all()
    return jsonify(
        {
            "ok": True,
            "records": [
                {
                    "id": row.id,
                    "session_id": row.session_id,
                    "finding_id": row.finding_id,
                    "evidence_type": row.evidence_type,
                    "content": row.content,
                    "metadata": json.loads(row.metadata_json or "{}"),
                    "created_at": row.created_at.isoformat() if row.created_at else datetime.utcnow().isoformat(),
                }
                for row in rows
            ],
        }
    )


@evidence_bp.delete("/api/evidence/<int:evidence_id>")
def delete_evidence(evidence_id: int):
    row = ReviewEvidence.query.filter_by(id=evidence_id).first()
    if not row:
        return jsonify({"ok": False, "error": "not found"}), 404
    db.session.delete(row)
    db.session.commit()
    return jsonify({"ok": True, "deleted": evidence_id})
