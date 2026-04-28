"""AKDW SSH Session Manager.

Bridges Paramiko SSH PTY channels to Flask-SocketIO room streams.
Each terminal tab maps to one SSH session.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import errno
from typing import Dict, Optional

import eventlet
import paramiko
from flask_socketio import SocketIO


logger = logging.getLogger("akdw.ssh")

_sessions: Dict[str, "SSHSession"] = {}
_sessions_lock = threading.Lock()


class SSHSession:
    """Single SSH connection + PTY channel lifecycle wrapper."""

    def __init__(self, session_id: str, socketio: SocketIO):
        self.session_id = session_id
        self.socketio = socketio
        self.client: Optional[paramiko.SSHClient] = None
        self.channel = None
        self.reader_thread: Optional[threading.Thread] = None
        self.reader_lock = threading.Lock()
        self.active = False
        self.hostname: Optional[str] = None
        self.username: Optional[str] = None

    def connect(
        self,
        hostname: str,
        port: int,
        username: str,
        password: str | None = None,
        key_path: str | None = None,
    ) -> dict:
        """Connect and allocate PTY shell channel."""
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            connect_kwargs = {
                "hostname": hostname,
                "port": int(port),
                "username": username,
                "timeout": 15,
                "auth_timeout": 15,
                "banner_timeout": 15,
                "look_for_keys": True,
                "allow_agent": True,
            }
            if password:
                connect_kwargs["password"] = password
            if key_path:
                expanded = os.path.expanduser(key_path)
                if os.path.exists(expanded):
                    connect_kwargs["key_filename"] = expanded

            with eventlet.Timeout(25):
                self.client.connect(**connect_kwargs)
            self.channel = self.client.invoke_shell(
                term="xterm-256color",
                width=220,
                height=50,
            )
            self.channel.setblocking(False)
            self.active = True
            self.hostname = hostname
            self.username = username

            logger.info("SSH connected: %s@%s | session=%s", username, hostname, self.session_id)
            return {"success": True, "message": f"Connected to {username}@{hostname}"}
        except paramiko.AuthenticationException as exc:
            logger.error("Auth failed for %s@%s: %s", username, hostname, exc)
            return {"success": False, "message": f"Authentication failed: {exc}"}
        except paramiko.SSHException as exc:
            logger.error("SSH error for %s@%s: %s", username, hostname, exc)
            return {"success": False, "message": f"SSH error: {exc}"}
        except socket.timeout:
            return {"success": False, "message": f"Connection timed out to {hostname}:{port}"}
        except eventlet.timeout.Timeout:
            logger.error("Timed out connecting to %s@%s", username, hostname)
            return {"success": False, "message": f"Connection timed out to {hostname}:{port}"}
        except Exception as exc:  # noqa: BLE001
            logger.error("Unexpected error connecting to %s: %s", hostname, exc)
            return {"success": False, "message": f"Connection error: {exc}"}

    def start_pty_reader(self) -> None:
        """Start background PTY read loop and emit terminal output events."""
        with self.reader_lock:
            if self.reader_thread and self.reader_thread.is_alive():
                return
            self.active = True

        def _read_loop() -> None:
            import select

            while self.active and self.channel is not None:
                try:
                    readable, _, _ = select.select([self.channel], [], [], 0.1)
                    if not readable:
                        continue
                    data = self.channel.recv(4096)
                    if data:
                        try:
                            self.socketio.emit(
                                "terminal_output",
                                {
                                    "data": data.decode("utf-8", errors="replace"),
                                    "session_id": self.session_id,
                                },
                                room=self.session_id,
                            )
                        except Exception as emit_exc:  # noqa: BLE001
                            logger.warning("Socket emit failed [%s]: %s", self.session_id, emit_exc)
                        continue
                    if self.channel.exit_status_ready():
                        self.socketio.emit(
                            "terminal_closed",
                            {
                                "session_id": self.session_id,
                                "message": "SSH session closed by remote",
                            },
                            room=self.session_id,
                        )
                        break
                except socket.timeout:
                    # Non-fatal timeout on non-blocking channels.
                    continue
                except OSError as exc:
                    # Transient non-fatal readiness mismatch.
                    if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                        continue
                    if self.active:
                        logger.error("PTY OS error [%s]: %s", self.session_id, exc)
                    break
                except Exception as exc:  # noqa: BLE001
                    text = str(exc).lower()
                    if "timed out" in text or "would block" in text:
                        continue
                    if self.active:
                        logger.error("PTY read error [%s]: %s", self.session_id, exc)
                    break

            self.active = bool(
                self.channel is not None
                and not getattr(self.channel, "closed", False)
                and not self.channel.exit_status_ready()
            )
            logger.info("PTY reader stopped for session %s", self.session_id)

        self.reader_thread = threading.Thread(
            target=_read_loop,
            name=f"ssh-pty-{self.session_id}",
            daemon=True,
        )
        self.reader_thread.start()

    def send(self, data: str) -> None:
        """Forward user input to remote PTY."""
        if not self.channel:
            return
        if getattr(self.channel, "closed", False):
            self.active = False
            return
        try:
            self.channel.send(data)
            self.active = True
            if not self.reader_thread or not self.reader_thread.is_alive():
                self.start_pty_reader()
        except Exception as exc:  # noqa: BLE001
            logger.error("Send error [%s]: %s", self.session_id, exc)

    def resize(self, cols: int, rows: int) -> None:
        """Resize remote PTY for current xterm viewport."""
        if not self.channel or not self.active:
            return
        try:
            self.channel.resize_pty(width=int(cols), height=int(rows))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Resize error [%s]: %s", self.session_id, exc)

    def close(self) -> None:
        """Close channel and client gracefully."""
        self.active = False
        try:
            if self.channel:
                self.channel.close()
            if self.client:
                self.client.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Close error [%s]: %s", self.session_id, exc)
        logger.info("SSH session closed: %s", self.session_id)


def create_session(session_id: str, socketio: SocketIO) -> SSHSession:
    with _sessions_lock:
        sess = SSHSession(session_id=session_id, socketio=socketio)
        _sessions[session_id] = sess
        return sess


def get_session(session_id: str) -> Optional[SSHSession]:
    return _sessions.get(session_id)


def close_session(session_id: str) -> None:
    with _sessions_lock:
        sess = _sessions.pop(session_id, None)
    if sess:
        sess.close()


def list_sessions() -> list:
    return [
        {
            "session_id": sess.session_id,
            "hostname": sess.hostname,
            "username": sess.username,
            "active": sess.active,
        }
        for sess in _sessions.values()
    ]
