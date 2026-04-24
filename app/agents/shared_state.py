from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from typing import Any

from app.agents.project_plan_manager import ProjectPlanManager
from app.db import DB_PATH

_LOCK = threading.Lock()


class AgentStateDB:
    """Shared state and timeline storage for dual-agent runs."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or DB_PATH
        self.plan_mgr = ProjectPlanManager()
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
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

    def get_project_plan(self) -> dict[str, Any]:
        return self.plan_mgr.load()

    def save_project_plan(self, plan: dict[str, Any]) -> None:
        self.plan_mgr.save(plan)

    def upsert_session(self, session_id: str, task_id: str, status: str, verdict: str | None = None) -> None:
        with _LOCK:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                """
                INSERT OR IGNORE INTO dual_agent_sessions
                (session_id, task_id, status, started_at)
                VALUES (?,?,?,?)
                """,
                (session_id, task_id, status, datetime.utcnow().isoformat()),
            )
            if verdict is not None or status in ("complete", "paused"):
                conn.execute(
                    """
                    UPDATE dual_agent_sessions
                    SET status=?, completed_at=?, final_verdict=COALESCE(?, final_verdict)
                    WHERE session_id=?
                    """,
                    (status, datetime.utcnow().isoformat(), verdict, session_id),
                )
            else:
                conn.execute(
                    "UPDATE dual_agent_sessions SET status=? WHERE session_id=?",
                    (status, session_id),
                )
            conn.commit()
            conn.close()

    def append_history(
        self,
        session_id: str,
        round_num: int,
        role: str,
        content: str,
        timestamp: str | None = None,
    ) -> None:
        ts = timestamp or datetime.utcnow().isoformat()
        with _LOCK:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                """
                INSERT INTO dual_agent_history
                  (session_id, round_num, role, content, timestamp)
                VALUES (?,?,?,?,?)
                """,
                (session_id, round_num, role, content, ts),
            )
            conn.commit()
            conn.close()

    def get_history(self, session_id: str, limit: int = 300) -> list[dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            """
            SELECT round_num, role, content, timestamp
            FROM dual_agent_history
            WHERE session_id=?
            ORDER BY id ASC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()
        conn.close()
        return [
            {
                "round_num": r[0],
                "role": r[1],
                "content": r[2],
                "timestamp": r[3],
            }
            for r in rows
        ]

    @staticmethod
    def as_json(value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=True)
        except Exception:
            return str(value)
