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

    def _is_command_allowed(self, command: str) -> bool:
        low = (command or "").lower()
        if any(pattern in low for pattern in BLOCKED_PATTERNS):
            return False
        first = (command.strip().split() or [""])[0]
        return any(first == prefix for prefix in ALLOWED_PREFIX)

    def execute_safe_command(self, session_id: str, command: str, cwd: str) -> str:
        if not self._is_command_allowed(command):
            return "Command blocked by safety policy."
        run_cwd = cwd if os.path.isdir(cwd) else "/app/kernel"
        proc = subprocess.run(
            command,
            shell=True,
            cwd=run_cwd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = ((proc.stdout or "") + (proc.stderr or "")).strip()
        self._append_output(session_id, output + "\n")
        return output or f"(exit {proc.returncode})"

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
