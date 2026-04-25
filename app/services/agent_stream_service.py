"""Replayable SSE stream manager for agent responses."""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional


@dataclass
class AgentStreamEvent:
    event_id: int
    payload: Dict[str, Any]


@dataclass
class AgentStreamState:
    stream_id: str
    session_id: str
    model: str
    page: str
    message: str
    attachments: List[Dict[str, str]]
    selected_code: str
    filename: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    events: List[AgentStreamEvent] = field(default_factory=list)
    done: bool = False
    error: Optional[str] = None

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)

    def push(self, payload: Dict[str, Any]) -> int:
        with self._cond:
            event_id = len(self.events) + 1
            outgoing = dict(payload or {})
            outgoing["stream_id"] = self.stream_id
            outgoing["cursor"] = event_id
            self.events.append(AgentStreamEvent(event_id=event_id, payload=outgoing))
            self.updated_at = time.time()
            self._cond.notify_all()
            return event_id

    def finish(self, error: Optional[str] = None) -> None:
        with self._cond:
            self.done = True
            self.error = error
            self.updated_at = time.time()
            self._cond.notify_all()

    def wait_for_event(self, next_event_id: int, timeout: float) -> Optional[AgentStreamEvent]:
        with self._cond:
            if len(self.events) >= next_event_id:
                return self.events[next_event_id - 1]
            if self.done:
                return None
            self._cond.wait(timeout=timeout)
            if len(self.events) >= next_event_id:
                return self.events[next_event_id - 1]
            return None


class AgentStreamManager:
    def __init__(self, ttl_seconds: int = 1800):
        self.ttl_seconds = ttl_seconds
        self._streams: Dict[str, AgentStreamState] = {}
        self._lock = threading.Lock()

    def _cleanup_locked(self) -> None:
        now = time.time()
        stale_ids = []
        for sid, state in self._streams.items():
            age = now - state.updated_at
            if age > self.ttl_seconds:
                stale_ids.append(sid)
        for sid in stale_ids:
            self._streams.pop(sid, None)

    def get(self, stream_id: str) -> Optional[AgentStreamState]:
        with self._lock:
            self._cleanup_locked()
            return self._streams.get(stream_id)

    def start_stream(
        self,
        *,
        app,
        agent_service,
        session_id: str,
        message: str,
        model: str,
        page: str,
        attachments: List[Dict[str, str]],
        selected_code: str = "",
        filename: str = "",
    ) -> AgentStreamState:
        stream_id = f"astream-{uuid.uuid4().hex[:10]}"
        state = AgentStreamState(
            stream_id=stream_id,
            session_id=session_id,
            model=model,
            page=page,
            message=message,
            attachments=attachments,
            selected_code=selected_code,
            filename=filename,
        )
        state.push({"type": "meta", "session_id": session_id, "message": "stream initialized"})

        with self._lock:
            self._cleanup_locked()
            self._streams[stream_id] = state

        t = threading.Thread(
            target=self._run_stream_worker,
            args=(app, agent_service, state),
            daemon=True,
        )
        t.start()
        return state

    def _run_stream_worker(self, app, agent_service, state: AgentStreamState) -> None:
        from app.services.activity_service import log_activity
        from app.services.session_service import append_message, ensure_session

        with app.app_context():
            try:
                ensure_session(session_id=state.session_id, page=state.page, model=state.model)

                thinking_steps = [
                    "🧠 Analyzing your query...",
                    "⚙️ Preparing context and session state...",
                ]
                for file_item in state.attachments:
                    thinking_steps.append("📄 Reading: " + str(file_item.get("filename") or "attachment"))
                thinking_steps.append("⚙️ Invoking QGenie agent...")

                for idx, step in enumerate(thinking_steps, start=1):
                    append_message(
                        session_id=state.session_id,
                        role="assistant",
                        content=step,
                        step_type="thinking",
                    )
                    state.push({"type": "thinking", "step": step, "idx": idx, "session_id": state.session_id})
                    time.sleep(0.05)

                result = agent_service.stream_chat(
                    session_id=state.session_id,
                    message=state.message,
                    model=state.model,
                    attachments=state.attachments,
                    selected_code=state.selected_code,
                    filename=state.filename,
                    page=state.page,
                )

                notices = result.get("notices", []) or []
                for offset, notice in enumerate(notices, start=1):
                    step = "⚠️ " + str(notice)
                    append_message(
                        session_id=state.session_id,
                        role="assistant",
                        content=step,
                        step_type="thinking",
                    )
                    state.push(
                        {
                            "type": "thinking",
                            "step": step,
                            "idx": len(thinking_steps) + offset,
                            "session_id": state.session_id,
                        }
                    )

                tokens = {
                    "used": int(result.get("token_usage_estimate", 0) or 0),
                    "limit": int(result.get("token_usage_max", 131072) or 131072),
                }
                state.push(
                    {
                        "type": "response",
                        "content": result.get("response", ""),
                        "tokens": tokens,
                        "session_id": state.session_id,
                    }
                )
                log_activity("Agent session: " + ((state.message or "(attachments)")[:50]), "agent")
                state.finish()
            except Exception as exc:
                state.push({"type": "error", "content": str(exc), "session_id": state.session_id})
                state.finish(error=str(exc))

    def sse_iter(self, stream_id: str, cursor: int = 0) -> Iterator[str]:
        state = self.get(stream_id)
        if not state:
            payload = {"type": "error", "content": "stream not found", "stream_id": stream_id}
            yield "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
            yield "data: [DONE]\n\n"
            return

        next_event_id = max(1, int(cursor or 0) + 1)
        idle_heartbeats = 0

        while True:
            event = state.wait_for_event(next_event_id, timeout=5.0)
            if event is None:
                if state.done and len(state.events) < next_event_id:
                    break
                idle_heartbeats += 1
                yield f": keepalive {idle_heartbeats}\n\n"
                continue

            next_event_id = event.event_id + 1
            payload_raw = json.dumps(event.payload, ensure_ascii=False)
            yield f"id: {event.event_id}\n"
            yield "event: message\n"
            yield "data: " + payload_raw + "\n\n"

        yield "data: [DONE]\n\n"


agent_stream_manager = AgentStreamManager()

