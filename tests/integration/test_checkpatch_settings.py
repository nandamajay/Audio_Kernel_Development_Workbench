import os
import subprocess
from pathlib import Path


def _write_checkpatch(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_validate_checkpatch_not_found(client):
    res = client.get("/api/validate_checkpatch?path=/does/not/exist")
    assert res.status_code == 200
    data = res.get_json()
    assert data["found"] is False


def test_validate_checkpatch_found(client, app):
    kernel_root = Path(app.config["KERNEL_SRC_PATH"])
    script_path = kernel_root / "scripts" / "checkpatch.pl"
    _write_checkpatch(script_path, "#!/usr/bin/perl\nprint \"OK\";\n")

    res = client.get(f"/api/validate_checkpatch?path={kernel_root}")
    assert res.status_code == 200
    data = res.get_json()
    assert data["found"] is True
    assert data["path"].endswith("checkpatch.pl")


def test_run_checkpatch_missing(client, app, monkeypatch):
    kernel_root = Path(app.config["KERNEL_SRC_PATH"])
    script_path = kernel_root / "scripts" / "checkpatch.pl"
    if script_path.exists():
        script_path.unlink()

    import app.routes.patchwise as patchwise
    monkeypatch.setattr(patchwise, "resolve_checkpatch_path", lambda _root: None)
    monkeypatch.setattr(patchwise, "_find_script", lambda _root, _name: None)

    res = client.post(
        "/api/patchwise/run_checkpatch",
        json={"session_id": "cp-missing", "patch_content": "diff --git a/a b/a"},
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is False
    assert "not found" in (data.get("output") or "").lower()


def test_run_checkpatch_success(client, app):
    kernel_root = Path(app.config["KERNEL_SRC_PATH"])
    script_path = kernel_root / "scripts" / "checkpatch.pl"
    _write_checkpatch(
        script_path,
        "#!/usr/bin/perl\nprint \"WARNING: test warn\\nERROR: test err\\n\";\nexit 1;\n",
    )

    res = client.post(
        "/api/patchwise/run_checkpatch",
        json={"session_id": "cp-ok", "patch_content": "diff --git a/a b/a"},
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert data["warnings_count"] == 1
    assert data["errors_count"] == 1


def test_run_checkpatch_timeout(client, app, monkeypatch):
    kernel_root = Path(app.config["KERNEL_SRC_PATH"])
    script_path = kernel_root / "scripts" / "checkpatch.pl"
    _write_checkpatch(script_path, "#!/usr/bin/perl\nprint \"OK\";\n")

    def _raise_timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="checkpatch", timeout=60)

    import app.routes.patchwise as patchwise

    monkeypatch.setattr(patchwise.subprocess, "run", _raise_timeout)

    res = client.post(
        "/api/patchwise/run_checkpatch",
        json={"session_id": "cp-timeout", "patch_content": "diff --git a/a b/a"},
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is False
    assert "timed out" in (data.get("output") or "").lower()


def test_settings_page_contains_ssl_fields(client):
    res = client.get("/settings")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-testid=\"ssl-verify-field\"" in html
    assert "data-testid=\"ca-bundle-field\"" in html
