"""Driver converter routes."""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List

import requests
from requests.auth import HTTPBasicAuth
from flask import Blueprint, current_app, jsonify, render_template, request
from sqlalchemy import inspect, text

from app.config import Config, MODEL_METADATA, get_available_models, get_default_model
from app.models import db
from app.services.env_service import load_env_values, resolve_ssl_verify
from app.utils.driver_link_fetcher import DriverLinkFetcher
from app.utils.upstream_converter_prompt import build_conversion_prompt


converter_bp = Blueprint("converter", __name__, url_prefix="/converter")
converter_api_bp = Blueprint("converter_api", __name__)


@converter_bp.get("/")
def converter_home():
    models = get_available_models()
    return render_template(
        "converter.html",
        models=models,
        model_metadata=MODEL_METADATA,
        default_model=get_default_model(),
    )


def _ensure_converter_jobs_schema() -> None:
    inspector = inspect(db.engine)
    if inspector.has_table("converter_jobs"):
        return
    db.session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS converter_jobs (
              id TEXT PRIMARY KEY,
              timestamp TEXT,
              filename TEXT,
              cl_number TEXT,
              cl_subject TEXT,
              source_link TEXT,
              requirements TEXT,
              conversion_type TEXT,
              status TEXT,
              result_patch TEXT,
              summary TEXT,
              files_modified TEXT,
              model_used TEXT
            )
            """
        )
    )
    db.session.commit()


def _converter_tls() -> Dict[str, Any]:
    values = load_env_values()
    ssl_verify = values.get("QGENIE_SSL_VERIFY", "true")
    ca_bundle = values.get("QGENIE_CA_BUNDLE", "")
    verify = resolve_ssl_verify(ssl_verify_raw=ssl_verify, ca_bundle=ca_bundle)
    return {"ssl_verify": ssl_verify, "ca_bundle": ca_bundle, "verify": verify}


def _parse_patch_summary(text_output: str) -> Dict[str, Any]:
    files = []
    for line in (text_output or "").splitlines():
        if line.startswith("+++ b/"):
            files.append(line.replace("+++ b/", "").strip())
    summary_match = re.search(r"## CONVERSION SUMMARY(.*)", text_output or "", re.DOTALL)
    summary = summary_match.group(1).strip() if summary_match else ""
    return {"files": files, "summary": summary}


def _qgenie_chat(messages: List[Dict[str, str]], model: str) -> Dict[str, str]:
    runtime_api_key = (current_app.config.get("QGENIE_API_KEY") or os.getenv("QGENIE_API_KEY", "")).strip()
    provider_url = (
        current_app.config.get("QGENIE_PROVIDER_URL")
        or os.getenv("QGENIE_PROVIDER_URL", "https://qgenie-chat.qualcomm.com/v1")
    ).strip()

    if not runtime_api_key:
        return {"output": "", "error": "QGENIE_API_KEY not configured"}

    try:
        from qgenie import ChatMessage, QGenieClient
    except Exception:
        try:
            from qgenie_sdk import ChatMessage, QGenieClient  # type: ignore
        except Exception as exc:
            return {"output": "", "error": f"QGenie SDK unavailable: {exc}"}

    try:
        client = QGenieClient(api_key=runtime_api_key, base_url=provider_url)
    except TypeError:
        client = QGenieClient(api_key=runtime_api_key)

    payload = [ChatMessage(role=item["role"], content=item["content"]) for item in messages]
    try:
        response = client.chat(messages=payload, model=model)
    except Exception as exc:
        return {"output": "", "error": str(exc)}

    output = ""
    if isinstance(response, str):
        output = response
    else:
        output = getattr(response, "first_content", "") or getattr(response, "content", "") or ""
    return {"output": output, "error": ""}


@converter_api_bp.post("/api/converter/fetch-link")
def fetch_link():
    payload = request.get_json() or {}
    url = (payload.get("url") or "").strip()
    if not url:
        return jsonify({"success": False, "error": "url is required"}), 200

    tls = _converter_tls()
    fetcher = DriverLinkFetcher(ssl_verify=tls["ssl_verify"], ca_bundle=tls["ca_bundle"])
    auth = {
        "gerrit_username": payload.get("gerrit_username"),
        "gerrit_password": payload.get("gerrit_password"),
    }
    result = fetcher.fetch(url, auth=auth)
    return jsonify(result)


@converter_api_bp.post("/api/converter/convert")
def convert_driver():
    _ensure_converter_jobs_schema()
    payload = request.get_json() or {}
    source_code = payload.get("source_code", "")
    filename = payload.get("filename") or "driver.c"
    metadata = payload.get("metadata") or {}
    requirements = payload.get("requirements") or ""
    conversion_type = payload.get("conversion_type") or "full_upstream"
    target_kernel = payload.get("target_kernel") or "latest"
    model = (payload.get("model") or current_app.config.get("QGENIE_DEFAULT_MODEL") or get_default_model()).strip()

    if not source_code.strip():
        return jsonify({"success": False, "error": "source_code is required"}), 200

    messages = build_conversion_prompt(
        {
            "source_code": source_code,
            "filename": filename,
            "metadata": metadata,
            "requirements": requirements,
            "conversion_type": conversion_type,
            "target_kernel": target_kernel,
        }
    )

    qgenie_result = _qgenie_chat(messages, model)
    output = qgenie_result.get("output", "")
    error = qgenie_result.get("error", "")
    parsed = _parse_patch_summary(output)

    job_id = f"conv-{uuid.uuid4().hex[:10]}"
    status = "done" if output and not error else "error"
    db.session.execute(
        text(
            """
            INSERT INTO converter_jobs (
              id, timestamp, filename, cl_number, cl_subject, source_link,
              requirements, conversion_type, status, result_patch, summary,
              files_modified, model_used
            ) VALUES (
              :id, :timestamp, :filename, :cl_number, :cl_subject, :source_link,
              :requirements, :conversion_type, :status, :result_patch, :summary,
              :files_modified, :model_used
            )
            """
        ),
        {
            "id": job_id,
            "timestamp": datetime.utcnow().isoformat(),
            "filename": filename,
            "cl_number": metadata.get("cl_number", ""),
            "cl_subject": metadata.get("subject", ""),
            "source_link": metadata.get("source_link", ""),
            "requirements": requirements,
            "conversion_type": conversion_type,
            "status": status,
            "result_patch": output,
            "summary": parsed.get("summary", ""),
            "files_modified": json.dumps(parsed.get("files", [])),
            "model_used": model,
        },
    )
    db.session.commit()

    return jsonify(
        {
            "job_id": job_id,
            "patch": output,
            "summary": parsed.get("summary", ""),
            "files_modified": parsed.get("files", []),
            "success": bool(output) and not error,
            "error": error,
        }
    )


@converter_api_bp.get("/api/converter/jobs")
def list_jobs():
    _ensure_converter_jobs_schema()
    rows = db.session.execute(text("SELECT * FROM converter_jobs ORDER BY timestamp DESC")).mappings().all()
    return jsonify([dict(row) for row in rows])


@converter_api_bp.get("/api/converter/jobs/<job_id>")
def get_job(job_id: str):
    _ensure_converter_jobs_schema()
    row = db.session.execute(
        text("SELECT * FROM converter_jobs WHERE id = :job_id"),
        {"job_id": job_id},
    ).mappings().first()
    if not row:
        return jsonify({"success": False, "error": "job not found"}), 404
    return jsonify(dict(row))


@converter_api_bp.post("/api/converter/gerrit-auth-test")
def gerrit_auth_test():
    payload = request.get_json() or {}
    username = (payload.get("gerrit_username") or "").strip()
    password = (payload.get("gerrit_password") or "").strip()
    if not username or not password:
        return jsonify({"success": False, "error": "Missing Gerrit credentials"}), 200

    auth = HTTPBasicAuth(username, password)
    tls = _converter_tls()
    try:
        resp = requests.get(
            "https://gerrit.qualcomm.com/a/accounts/self",
            timeout=10,
            verify=tls["verify"],
            auth=auth,
        )
        if resp.status_code == 401:
            return jsonify({"success": False, "error": "Unauthorized"}), 200
        if resp.status_code >= 400:
            return jsonify({"success": False, "error": f"Gerrit error: {resp.status_code}"}), 200
        text_resp = resp.text
        if text_resp.startswith(")]}'"):
            text_resp = "\n".join(text_resp.splitlines()[1:])
        data = json.loads(text_resp)
        return jsonify({"success": True, "username": data.get("name") or username})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 200
