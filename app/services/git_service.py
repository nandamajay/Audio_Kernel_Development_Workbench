"""Git service utilities used by AKDW."""
# REUSED FROM (PATTERN): Q-Build-Manager/editor_manager.py git helpers

from __future__ import annotations

import subprocess
from typing import Dict, List


def parse_commit_range(value: str, default_commits: int = 1) -> str:
    text = (value or "").strip()
    if not text:
        return f"HEAD~{default_commits}..HEAD"
    if text.isdigit():
        return f"HEAD~{text}..HEAD"
    return text


def parse_git_log(raw: str) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for line in (raw or "").splitlines():
        parts = line.split("|", 3)
        if len(parts) != 4:
            continue
        rows.append(
            {
                "sha": parts[0].strip(),
                "author": parts[1].strip(),
                "date": parts[2].strip(),
                "message": parts[3].strip(),
            }
        )
    return rows


def list_recent_commits(cwd: str, n: int = 10) -> List[Dict[str, str]]:
    cmd = [
        "git",
        "log",
        f"-n{max(1, n)}",
        "--date=iso",
        "--pretty=format:%H|%an|%ad|%s",
    ]
    result = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        return []
    return parse_git_log(result.stdout)


def get_commit_diff(cwd: str, sha: str) -> str:
    cmd = ["git", "show", "--format=", sha]
    result = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        return ""
    return result.stdout
