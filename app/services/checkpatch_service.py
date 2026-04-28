"""Helpers for resolving checkpatch.pl in common kernel locations."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import List, Optional


def _dedupe(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _repo_kernel_path() -> Optional[str]:
    try:
        repo_root = Path(__file__).resolve().parents[2]
    except Exception:
        return None
    return str(repo_root / "kernel" / "scripts" / "checkpatch.pl")


def resolve_checkpatch_path(kernel_root: str | None = None) -> str | None:
    """Return a valid checkpatch.pl path if found, else None."""
    candidates: List[str] = []

    def add(path: str | None) -> None:
        if path:
            candidates.append(path)

    def scripts_path(root: str | None) -> str | None:
        if not root:
            return None
        return os.path.join(root, "scripts", "checkpatch.pl")

    add(scripts_path(kernel_root))

    env_root = os.environ.get("KERNEL_PATH") or os.environ.get("KERNEL_SRC_PATH")
    add(scripts_path(env_root))

    add(_repo_kernel_path())
    add("/app/kernel/scripts/checkpatch.pl")
    add("/local/mnt/workspace/kernel/scripts/checkpatch.pl")
    add(os.path.expanduser("~/kernel/scripts/checkpatch.pl"))

    for path in _dedupe(candidates):
        if os.path.isfile(path):
            return path

    which_path = shutil.which("checkpatch.pl")
    if which_path:
        return which_path

    return None


def resolve_checkpatch_in_root(kernel_root: str | None) -> str | None:
    """Return checkpatch.pl only if it exists under the provided kernel root."""
    if not kernel_root:
        return None
    direct = os.path.join(kernel_root, "scripts", "checkpatch.pl")
    if os.path.isfile(direct):
        return direct
    return None
