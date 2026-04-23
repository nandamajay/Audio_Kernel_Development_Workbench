from pathlib import Path

from app.services import fs_service


def test_safe_path_and_list_directory(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    kernel = tmp_path / "kernel"
    workspace.mkdir()
    kernel.mkdir()
    (workspace / "foo.txt").write_text("hello", encoding="utf-8")
    (kernel / "bar.c").write_text("int x;", encoding="utf-8")

    monkeypatch.setenv("WORKSPACE_PATH", str(workspace))
    monkeypatch.setenv("KERNEL_SRC_PATH", str(kernel))

    assert fs_service.safe_path(str(workspace / "foo.txt"))
    assert fs_service.safe_path(str(kernel / "bar.c"))
    assert fs_service.safe_path("/etc/passwd") is None

    entries = fs_service.list_directory(str(workspace))
    assert entries
    assert entries[0]["name"] == "foo.txt"
