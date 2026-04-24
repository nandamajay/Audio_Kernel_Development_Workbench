"""Filesystem helpers for editor and path browsing."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional


DEFAULT_WORKSPACE = "/app/workspace"
DEFAULT_KERNEL = "/app/kernel"
DEFAULT_PATCHES = "/app/patches"
DEFAULT_LOGS = "/app/workspace/logs"
DEFAULT_SESSIONS_DB = "/app/sessions/akdw_sessions.db"
DEFAULT_WORKSPACE_MOUNTS = "/app/workspace_mounts"


def workspace_path() -> str:
    return os.getenv("WORKSPACE_PATH", DEFAULT_WORKSPACE)


def kernel_src_path() -> str:
    return os.getenv("KERNEL_SRC_PATH", DEFAULT_KERNEL)


def extra_workspace_paths() -> List[str]:
    raw = os.getenv("EXTRA_WORKSPACE_PATHS", "")
    if not raw.strip():
        return []
    return [os.path.abspath(item.strip()) for item in raw.split(",") if item.strip()]


def workspace_mounts_path() -> str:
    return os.getenv("WORKSPACE_MOUNTS_PATH", DEFAULT_WORKSPACE_MOUNTS)


def _allowed_roots() -> List[str]:
    roots = [
        os.path.abspath(workspace_path()),
        os.path.abspath(kernel_src_path()),
        os.path.abspath(workspace_mounts_path()),
        os.path.abspath("/app/kernel"),
        os.path.abspath("/app/workspace"),
        os.path.abspath("/workspace"),
        os.path.abspath("/local/mnt/workspace"),
    ]
    roots.extend(extra_workspace_paths())
    # Preserve order while removing duplicates.
    dedup: List[str] = []
    seen = set()
    for root in roots:
        if root in seen:
            continue
        seen.add(root)
        dedup.append(root)
    return dedup


def normalize_path(path: str) -> str:
    if not path:
        path = kernel_src_path()
    return os.path.abspath(path)


def is_path_allowed(path: str) -> bool:
    candidate = normalize_path(path)
    for root in _allowed_roots():
        try:
            if os.path.commonpath([candidate, root]) == root:
                return True
        except ValueError:
            continue
    return False


def safe_path(path: str) -> Optional[str]:
    candidate = normalize_path(path)
    if not is_path_allowed(candidate):
        return None
    return candidate


def list_directory(path: str) -> List[Dict[str, str]]:
    target = safe_path(path)
    if not target or not os.path.isdir(target):
        return []

    entries = []
    for name in sorted(os.listdir(target)):
        if name.startswith("."):
            continue
        full_path = os.path.join(target, name)
        entries.append(
            {
                "name": name,
                "path": full_path,
                "type": "dir" if os.path.isdir(full_path) else "file",
            }
        )
    return entries


def list_browse_roots() -> List[Dict[str, str]]:
    roots = [os.path.abspath(kernel_src_path())]
    roots.extend(extra_workspace_paths())
    mounts_root = os.path.abspath(workspace_mounts_path())
    if os.path.isdir(mounts_root):
        for name in sorted(os.listdir(mounts_root)):
            full = os.path.join(mounts_root, name)
            if os.path.isdir(full):
                roots.append(full)

    dedup = []
    seen = set()
    for item in roots:
        if item in seen or not os.path.isdir(item):
            continue
        seen.add(item)
        dedup.append(item)

    return [{"label": os.path.basename(path.rstrip("/")) or path, "path": path} for path in dedup]


def ensure_workspace_structure() -> None:
    workspace = workspace_path()
    kernel = kernel_src_path()
    patches = os.getenv("PATCHES_PATH", DEFAULT_PATCHES)
    logs = os.getenv("LOGS_PATH", DEFAULT_LOGS)
    sessions_db = os.getenv("SESSIONS_DB_PATH", DEFAULT_SESSIONS_DB)

    roots = [
        workspace,
        kernel,
        patches,
        os.path.dirname(sessions_db),
        logs,
        os.path.join(workspace, "workspace"),
        workspace_mounts_path(),
    ]
    for root in roots:
        Path(root).mkdir(parents=True, exist_ok=True)
