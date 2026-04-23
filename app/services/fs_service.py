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


def workspace_path() -> str:
    return os.getenv("WORKSPACE_PATH", DEFAULT_WORKSPACE)


def kernel_src_path() -> str:
    return os.getenv("KERNEL_SRC_PATH", DEFAULT_KERNEL)


def _allowed_roots() -> List[str]:
    return [os.path.abspath(workspace_path()), os.path.abspath(kernel_src_path())]


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
    ]
    for root in roots:
        Path(root).mkdir(parents=True, exist_ok=True)
