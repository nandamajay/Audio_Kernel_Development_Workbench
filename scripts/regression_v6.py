#!/usr/bin/env python3
"""AKDW v6 regression suite (15 checks) with artifact output."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class CheckResult:
    name: str
    ok: bool
    note: str


def request_json(base_url: str, path: str, method: str = "GET", data: Dict[str, Any] | None = None) -> tuple[int, str, Dict[str, Any] | None]:
    url = base_url.rstrip("/") + path
    payload = None
    headers = {"Content-Type": "application/json"}
    if data is not None:
        payload = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url=url, method=method, headers=headers, data=payload)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            parsed = None
            try:
                parsed = json.loads(body)
            except Exception:
                parsed = None
            return resp.status, body, parsed
    except urllib.error.HTTPError as exc:
        body = (exc.read() or b"").decode("utf-8", errors="replace")
        return exc.code, body, None
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc), None


def request_text(base_url: str, path: str) -> tuple[int, str]:
    status, body, _ = request_json(base_url, path, method="GET", data=None)
    return status, body


def run_suite(base_url: str) -> List[CheckResult]:
    results: List[CheckResult] = []

    def add(name: str, ok: bool, note: str) -> None:
        results.append(CheckResult(name=name, ok=ok, note=note))

    s, body = request_text(base_url, "/")
    add("CHECK_1", s == 200, f"GET / => {s}")

    s, body = request_text(base_url, "/agent/")
    add("CHECK_2", s == 200 and "REPLAY MODE" not in body, f"GET /agent/ => {s}")

    s, body = request_text(base_url, "/editor/")
    add("CHECK_3", s == 200 and ("xterm" in body or "terminal-panel" in body), f"GET /editor/ => {s}")

    s, body = request_text(base_url, "/patchwise/")
    add("CHECK_4", s == 200 and "step-indicator" in body, f"GET /patchwise/ => {s}")

    s, body = request_text(base_url, "/upstream/")
    add("CHECK_5", s == 200 and "upstream-tracker" in body, f"GET /upstream/ => {s}")

    s, body = request_text(base_url, "/triage/")
    add("CHECK_6", s == 200 and "triage-onboarding" in body, f"GET /triage/ => {s}")

    s, body = request_text(base_url, "/settings/")
    add("CHECK_7", s == 200 and ("ssl_verify" in body or "SSL Verify" in body), f"GET /settings/ => {s}")

    s, body = request_text(base_url, "/health")
    add("CHECK_8", s == 200 and '"status": "ok"' in body.replace("\n", " "), f"GET /health => {s}")

    s, body, parsed = request_json(base_url, "/api/agent/chat", method="POST", data={"message": "hi", "session_id": "reg9"})
    response_text = (parsed or {}).get("response", "") if parsed else body
    add("CHECK_9", s == 200 and bool(response_text) and "UNAUTHORIZED" not in response_text, f"POST /api/agent/chat => {s}")

    s, body, parsed = request_json(
        base_url,
        "/api/patchwise/review",
        method="POST",
        data={"patch_content": "diff --git a/test.c b/test.c\n", "filename": "test.patch"},
    )
    add("CHECK_10", s == 200 and "not allowed" not in body.lower(), f"POST /api/patchwise/review => {s}")

    s, body, parsed = request_json(
        base_url,
        "/api/upstream/add",
        method="POST",
        data={"url": "https://lore.kernel.org/r/ci-reg@example.com", "title": "CI Regression Patch", "status": "under_review"},
    )
    patch_id = 0
    if parsed:
        patch_id = int(parsed.get("id") or ((parsed.get("patch") or {}).get("id") or 0))
    add("CHECK_11", s == 200 and patch_id > 0, f"POST /api/upstream/add => {s}, id={patch_id}")

    s, body, parsed = request_json(base_url, "/api/dashboard/stats", method="GET")
    add("CHECK_12", s == 200 and isinstance(parsed, dict) and "patch_health" in parsed, f"GET /api/dashboard/stats => {s}")

    s, body, parsed = request_json(
        base_url,
        "/api/terminal/agent",
        method="POST",
        data={"prompt": "pwd", "session_id": "r13"},
    )
    add("CHECK_13", s == 200 and bool((parsed or {}).get("response")), f"POST /api/terminal/agent => {s}")

    big_text = "A" * 500000
    s, body, parsed = request_json(
        base_url,
        "/api/agent/chat",
        method="POST",
        data={"message": "summarize", "session_id": "reg14", "files": [{"name": "big.txt", "content": big_text}]},
    )
    add("CHECK_14", s == 200 and "EXTERNAL_API_ERROR" not in body, f"POST /api/agent/chat large file => {s}")

    s, body = request_text(base_url, "/upstream/")
    add("CHECK_15", s == 200 and "Upstream Tracker" in body, f"GET /upstream/ nav marker => {s}")

    return results


def run_observability_extras(base_url: str) -> List[CheckResult]:
    extras: List[CheckResult] = []

    s, body, parsed = request_json(base_url, "/api/agent/stream/metrics", method="GET")
    ok = s == 200 and isinstance(parsed, dict) and parsed.get("ok") is True and isinstance(parsed.get("metrics"), dict)
    extras.append(CheckResult(name="EXTRA_STREAM_METRICS", ok=ok, note=f"GET /api/agent/stream/metrics => {s}"))

    s, body, parsed = request_json(base_url, "/api/terminal/audit?limit=1", method="GET")
    ok = s == 200 and isinstance(parsed, dict) and parsed.get("ok") is True and isinstance(parsed.get("rows"), list)
    extras.append(CheckResult(name="EXTRA_TERMINAL_AUDIT", ok=ok, note=f"GET /api/terminal/audit?limit=1 => {s}"))

    s, body, parsed = request_json(
        base_url,
        "/api/patchwise/pipeline",
        method="POST",
        data={"session_id": "extra-pipeline", "patch_content": "diff --git a/a.c b/a.c\n"},
    )
    ok = s == 200 and isinstance(parsed, dict) and parsed.get("ok") is True and isinstance(parsed.get("steps"), list)
    extras.append(CheckResult(name="EXTRA_PATCHWISE_PIPELINE", ok=ok, note=f"POST /api/patchwise/pipeline => {s}"))

    s, body, parsed = request_json(
        base_url,
        "/api/patchwise/autofix/preview",
        method="POST",
        data={"session_id": "extra-autofix", "patch_content": "int main(){return 0;}\t"},
    )
    ok = s == 200 and isinstance(parsed, dict) and parsed.get("ok") is True and "diff" in parsed
    extras.append(CheckResult(name="EXTRA_PATCHWISE_AUTOFIX", ok=ok, note=f"POST /api/patchwise/autofix/preview => {s}"))

    s, body, parsed = request_json(base_url, "/api/patchwise/traces?limit=1", method="GET")
    ok = s == 200 and isinstance(parsed, dict) and parsed.get("ok") is True and isinstance(parsed.get("rows"), list)
    extras.append(CheckResult(name="EXTRA_PATCHWISE_TRACES", ok=ok, note=f"GET /api/patchwise/traces?limit=1 => {s}"))

    s, body, parsed = request_json(
        base_url,
        "/api/patchwise/pipeline/start",
        method="POST",
        data={"session_id": "extra-pipeline-async", "patch_content": "diff --git a/a.c b/a.c\n"},
    )
    job_id = (parsed or {}).get("job_id") if isinstance(parsed, dict) else ""
    ok = s == 200 and bool(job_id)
    extras.append(CheckResult(name="EXTRA_PATCHWISE_PIPELINE_ASYNC_START", ok=ok, note=f"POST /api/patchwise/pipeline/start => {s}"))
    if job_id:
        status_ok = False
        final_status = "unknown"
        for _ in range(30):
            s2, body2, parsed2 = request_json(base_url, f"/api/patchwise/pipeline/status/{job_id}", method="GET")
            if s2 != 200 or not isinstance(parsed2, dict) or parsed2.get("ok") is not True:
                break
            final_status = str(parsed2.get("status") or "unknown")
            if final_status in {"completed", "failed", "canceled"}:
                status_ok = True
                break
            time.sleep(1)
        extras.append(
            CheckResult(
                name="EXTRA_PATCHWISE_PIPELINE_ASYNC_STATUS",
                ok=status_ok,
                note=f"GET /api/patchwise/pipeline/status/<job> => {final_status}",
            )
        )

        s3, body3, parsed3 = request_json(base_url, f"/api/patchwise/pipeline/cancel/{job_id}", method="POST", data={})
        cancel_ok = s3 == 200 and isinstance(parsed3, dict) and parsed3.get("ok") is True
        extras.append(CheckResult(name="EXTRA_PATCHWISE_PIPELINE_CANCEL", ok=cancel_ok, note=f"POST /api/patchwise/pipeline/cancel/<job> => {s3}"))

        s4, body4, parsed4 = request_json(base_url, f"/api/patchwise/pipeline/retry/{job_id}", method="POST", data={})
        retry_job = (parsed4 or {}).get("job_id") if isinstance(parsed4, dict) else ""
        retry_ok = s4 == 200 and bool(retry_job)
        extras.append(CheckResult(name="EXTRA_PATCHWISE_PIPELINE_RETRY", ok=retry_ok, note=f"POST /api/patchwise/pipeline/retry/<job> => {s4}"))

        s5, body5, parsed5 = request_json(base_url, "/api/patchwise/pipeline/history?limit=5", method="GET")
        history_ok = s5 == 200 and isinstance(parsed5, dict) and parsed5.get("ok") is True and isinstance(parsed5.get("rows"), list)
        extras.append(CheckResult(name="EXTRA_PATCHWISE_PIPELINE_HISTORY", ok=history_ok, note=f"GET /api/patchwise/pipeline/history?limit=5 => {s5}"))
    else:
        extras.append(
            CheckResult(
                name="EXTRA_PATCHWISE_PIPELINE_ASYNC_STATUS",
                ok=False,
                note="job_id missing from async start",
            )
        )
        extras.append(CheckResult(name="EXTRA_PATCHWISE_PIPELINE_CANCEL", ok=False, note="job_id missing from async start"))
        extras.append(CheckResult(name="EXTRA_PATCHWISE_PIPELINE_RETRY", ok=False, note="job_id missing from async start"))
        extras.append(CheckResult(name="EXTRA_PATCHWISE_PIPELINE_HISTORY", ok=False, note="job_id missing from async start"))

    s, body, parsed = request_json(base_url, "/api/patchwise/analytics?limit=50", method="GET")
    ok = s == 200 and isinstance(parsed, dict) and parsed.get("ok") is True and isinstance(parsed.get("summary"), dict)
    extras.append(CheckResult(name="EXTRA_PATCHWISE_ANALYTICS", ok=ok, note=f"GET /api/patchwise/analytics?limit=50 => {s}"))

    return extras


def write_reports(results: List[CheckResult], extras: List[CheckResult], out_json: Path, out_md: Path, base_url: str) -> None:
    passed = sum(1 for item in results if item.ok)
    payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "pass_count": passed,
        "total": len(results),
        "threshold": 13,
        "status": "PASS" if passed >= 13 else "FAIL",
        "checks": [
            {"name": item.name, "status": "PASS" if item.ok else "FAIL", "note": item.note}
            for item in results
        ],
        "extras": [
            {"name": item.name, "status": "PASS" if item.ok else "FAIL", "note": item.note}
            for item in extras
        ],
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# AKDW v6 Regression Report",
        "",
        f"- Timestamp (UTC): `{payload['timestamp_utc']}`",
        f"- Base URL: `{base_url}`",
        f"- Score: **{passed}/{len(results)}**",
        f"- Threshold: **13/15**",
        f"- Overall: **{payload['status']}**",
        "",
        "| Check | Status | Note |",
        "|---|---|---|",
    ]
    for item in results:
        status = "PASS" if item.ok else "FAIL"
        lines.append(f"| {item.name} | {status} | {item.note} |")
    if extras:
        lines.extend(["", "## Observability Extras (non-gating)", "", "| Check | Status | Note |", "|---|---|---|"])
        for item in extras:
            status = "PASS" if item.ok else "FAIL"
            lines.append(f"| {item.name} | {status} | {item.note} |")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run AKDW v6 regression suite.")
    parser.add_argument("--base-url", default="http://localhost:5001")
    parser.add_argument("--out-json", default="artifacts/regression_v6.json")
    parser.add_argument("--out-md", default="artifacts/regression_v6.md")
    args = parser.parse_args()

    results = run_suite(args.base_url)
    extras = run_observability_extras(args.base_url)
    for item in results:
        print(f"{item.name}: {'PASS' if item.ok else 'FAIL'} - {item.note}")
    for item in extras:
        print(f"{item.name}: {'PASS' if item.ok else 'FAIL'} - {item.note}")

    write_reports(results, extras, Path(args.out_json), Path(args.out_md), args.base_url)
    passed = sum(1 for item in results if item.ok)
    print(f"SUMMARY: {passed}/{len(results)} checks passed")
    return 0 if passed >= 13 else 1


if __name__ == "__main__":
    raise SystemExit(main())
