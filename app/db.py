"""Additional sqlite migrations for dual-agent system."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path


def _can_write(path: str) -> bool:
    try:
        parent = Path(path).parent
        parent.mkdir(parents=True, exist_ok=True)
        probe = parent / ".dual_agent_write_probe"
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def _resolve_dual_agent_db_path() -> str:
    candidates = [
        os.getenv("DUAL_AGENT_DB_PATH", "").strip(),
        "/local/mnt/workspace/AKDW/Audio_Kernel_Development_Workbench/akdw.db",
        "/app/sessions/akdw.db",
        "/tmp/akdw-dual-agent.db",
    ]
    for candidate in candidates:
        if candidate and _can_write(candidate):
            return candidate
    return "/tmp/akdw-dual-agent.db"


DB_PATH = _resolve_dual_agent_db_path()


def ensure_dual_agent_tables() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
          CREATE TABLE IF NOT EXISTS dual_agent_history (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id  TEXT    NOT NULL,
              round_num   INTEGER NOT NULL,
              role        TEXT    NOT NULL,
              content     TEXT    NOT NULL,
              timestamp   TEXT    NOT NULL
          )
      """
    )
    conn.execute(
        """
          CREATE TABLE IF NOT EXISTS dual_agent_sessions (
              id            INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id    TEXT NOT NULL UNIQUE,
              task_id       TEXT NOT NULL,
              status        TEXT NOT NULL DEFAULT 'running',
              started_at    TEXT NOT NULL,
              completed_at  TEXT,
              final_verdict TEXT
          )
      """
    )
    conn.commit()
    conn.close()
