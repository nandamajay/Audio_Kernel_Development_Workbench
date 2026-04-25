"""Lightweight PTY terminal session manager for browser terminal mode."""

from __future__ import annotations

import os
import pty
import re
import select
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from flask import has_app_context


BLOCKED_PATTERNS = [
    "rm -rf /",
    "shutdown",
    "reboot",
]

ALLOWED_PREFIX = [
    "git",
    "ls",
    "cat",
    "grep",
    "find",
    "checkpatch.pl",
    "make",
    "diff",
    "patch",
    "pwd",
    "echo",
]


@dataclass
class TerminalSession:
    session_id: str
    cwd: str
    master_fd: int
    pid: int
    process: subprocess.Popen
    output_lines: List[str] = field(default_factory=list)
    alive: bool = True
    started_at: float = field(default_factory=time.time)


class TerminalService:
    def __init__(self):
        self.sessions: Dict[str, TerminalSession] = {}
        self._lock = threading.Lock()
        self.socketio = None

    def attach_socketio(self, socketio):
        self.socketio = socketio

    def create_session(self, cwd: str = "/app/kernel", session_id: Optional[str] = None) -> str:
        sid = session_id or f"term-{uuid.uuid4().hex[:10]}"
        with self._lock:
            if sid in self.sessions and self.sessions[sid].alive:
                return sid
        if os.path.isdir(cwd):
            target_cwd = cwd
        elif os.path.isdir("/app/kernel"):
            target_cwd = "/app/kernel"
        else:
            target_cwd = "/app"
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            ["/bin/bash"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            preexec_fn=os.setsid,
            cwd=target_cwd,
            close_fds=True,
        )
        os.close(slave_fd)
        session = TerminalSession(
            session_id=sid,
            cwd=target_cwd,
            master_fd=master_fd,
            pid=proc.pid,
            process=proc,
        )
        with self._lock:
            self.sessions[sid] = session
        self._start_reader_thread(session)
        return sid

    def _start_reader_thread(self, session: TerminalSession) -> None:
        def _loop():
            while session.alive:
                try:
                    ready, _, _ = select.select([session.master_fd], [], [], 0.2)
                except Exception:
                    break
                if not ready:
                    continue
                try:
                    data = os.read(session.master_fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                text = data.decode("utf-8", errors="replace")
                self._append_output(session.session_id, text)
                if self.socketio:
                    self.socketio.emit(
                        "terminal:output",
                        {"session_id": session.session_id, "data": text},
                        namespace="/terminal",
                        to=session.session_id,
                    )
            session.alive = False

        t = threading.Thread(target=_loop, daemon=True)
        t.start()

    def _append_output(self, session_id: str, chunk: str) -> None:
        session = self.sessions.get(session_id)
        if not session:
            return
        for line in (chunk or "").splitlines():
            session.output_lines.append(line)
        if len(session.output_lines) > 2000:
            session.output_lines = session.output_lines[-2000:]

    def write(self, session_id: str, data: str) -> bool:
        session = self.sessions.get(session_id)
        if not session or not session.alive:
            return False
        try:
            os.write(session.master_fd, (data or "").encode("utf-8", errors="replace"))
            return True
        except Exception:
            return False

    def resize(self, session_id: str, cols: int, rows: int) -> bool:
        session = self.sessions.get(session_id)
        if not session or not session.alive:
            return False
        try:
            import fcntl
            import struct
            import termios

            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(session.master_fd, termios.TIOCSWINSZ, winsize)
            return True
        except Exception:
            return False

    def kill(self, session_id: str) -> bool:
        session = self.sessions.get(session_id)
        if not session:
            return False
        session.alive = False
        try:
            os.killpg(os.getpgid(session.pid), signal.SIGTERM)
        except Exception:
            pass
        try:
            os.close(session.master_fd)
        except Exception:
            pass
        return True

    def get_recent_output(self, session_id: str, lines: int = 20) -> str:
        session = self.sessions.get(session_id)
        if not session:
            return ""
        return "\n".join(session.output_lines[-lines:])

    def _is_command_allowed(self, command: str) -> tuple[bool, str]:
        low = (command or "").lower()
        if any(pattern in low for pattern in BLOCKED_PATTERNS):
            return False, "blocked destructive pattern"
        first = (command.strip().split() or [""])[0]
        if any(first == prefix for prefix in ALLOWED_PREFIX):
            return True, ""
        return False, "command prefix not allow-listed"

    def _record_audit(
        self,
        *,
        session_id: str,
        actor: str,
        command: str,
        cwd: str,
        exit_code: int,
        allowed: bool,
        blocked_reason: str = "",
        output_preview: str = "",
    ) -> None:
        if not has_app_context():
            return
        try:
            from app.models import TerminalCommandAudit, db

            row = TerminalCommandAudit(
                session_id=(session_id or "")[:64] or "unknown",
                actor=(actor or "agent_mode")[:64],
                command=(command or "")[:8000],
                cwd=(cwd or "")[:512],
                exit_code=int(exit_code),
                allowed=bool(allowed),
                blocked_reason=(blocked_reason or "")[:255] or None,
                output_preview=(output_preview or "")[:2000] or None,
            )
            db.session.add(row)
            db.session.commit()
        except Exception:
            try:
                from app.models import db

                db.session.rollback()
            except Exception:
                pass

    def execute_safe_command(self, session_id: str, command: str, cwd: str, actor: str = "agent_mode") -> str:
        is_allowed, reason = self._is_command_allowed(command)
        if not is_allowed:
            blocked_output = "Command blocked by safety policy."
            self._record_audit(
                session_id=session_id,
                actor=actor,
                command=command,
                cwd=cwd,
                exit_code=126,
                allowed=False,
                blocked_reason=reason,
                output_preview=blocked_output,
            )
            return blocked_output

        run_cwd = cwd if os.path.isdir(cwd) else "/app/kernel"
        exit_code = 0
        output = ""
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=run_cwd,
                capture_output=True,
                text=True,
                timeout=120,
            )
            exit_code = int(proc.returncode)
            output = ((proc.stdout or "") + (proc.stderr or "")).strip()
        except subprocess.TimeoutExpired as exc:
            exit_code = 124
            output = (
                "Command timed out after 120 seconds.\n"
                + ((exc.stdout or "") if isinstance(exc.stdout, str) else "")
                + ((exc.stderr or "") if isinstance(exc.stderr, str) else "")
            ).strip()
        except Exception as exc:
            exit_code = 127
            output = f"Command execution failed: {exc}"

        self._append_output(session_id, (output or "") + "\n")
        self._record_audit(
            session_id=session_id,
            actor=actor,
            command=command,
            cwd=run_cwd,
            exit_code=exit_code,
            allowed=True,
            blocked_reason="",
            output_preview=output,
        )
        return output or f"(exit {exit_code})"

    def extract_bash_blocks(self, text: str) -> List[str]:
        blocks: List[str] = []
        for match in re.finditer(r"```bash\s*(.*?)```", text or "", flags=re.DOTALL | re.IGNORECASE):
            cmd_block = (match.group(1) or "").strip()
            if not cmd_block:
                continue
            for line in cmd_block.splitlines():
                cmd = line.strip()
                if cmd:
                    blocks.append(cmd)
        return blocks


terminal_service = TerminalService()
