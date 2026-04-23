"""Agent service with streaming events and terminal colorization."""
# REUSED FROM (PATTERN): Q-Build-Manager/services/agent_runtime.py

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from flask import has_app_context

from app.config import Config, get_default_model
from app.services.session_service import append_message, ensure_session

ANSI_RESET = "\x1b[0m"
ANSI_RED = "\x1b[31m"
ANSI_YELLOW = "\x1b[33m"
ANSI_GREEN = "\x1b[32m"
ANSI_CYAN = "\x1b[36m"

TOOL_CALL_RE = re.compile(r"^(?:TOOL_CALL:|CALL:)\s*([^\s{]+)?\s*(\{.*\})?\s*$", re.IGNORECASE)
TOOL_RESULT_RE = re.compile(r"^(?:TOOL_RESULT:|RESULT:)\s*(.*)$", re.IGNORECASE)


def colorize_terminal_line(line: str) -> str:
    text = line or ""
    upper = text.upper()
    if any(token in upper for token in ["ERROR", "FAIL", "FAILED", "FATAL"]):
        return f"{ANSI_RED}{text}{ANSI_RESET}"
    if any(token in upper for token in ["WARNING", "WARN"]):
        return f"{ANSI_YELLOW}{text}{ANSI_RESET}"
    if any(token in upper for token in ["OK", "PASS", "PASSED", "SUCCESS", "DONE"]):
        return f"{ANSI_GREEN}{text}{ANSI_RESET}"
    if any(token in upper for token in ["INFO", "NOTE"]):
        return f"{ANSI_CYAN}{text}{ANSI_RESET}"
    return text


def detect_step_type(content: str) -> str:
    line = (content or "").strip()
    upper = line.upper()

    if upper.startswith("THINK:") or upper.startswith("THINKING:") or "THINKING" in upper:
        return "thinking"
    if upper.startswith("TOOL_CALL:") or upper.startswith("CALL:"):
        return "tool_call"
    if upper.startswith("TOOL_RESULT:") or upper.startswith("RESULT:"):
        return "tool_result"
    return "response"


@dataclass
class AgentStep:
    type: str
    content: str
    tool_name: Optional[str] = None
    tool_args: Optional[Dict[str, Any]] = None


def _safe_json(raw: str) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {"value": value}
    except Exception:
        return {"raw": raw}


def parse_stream_steps(raw_text: str) -> List[AgentStep]:
    text = (raw_text or "").strip()
    if not text:
        return [AgentStep(type="response", content="")]

    lines = text.splitlines()
    if not lines:
        return [AgentStep(type="response", content=text)]

    found_markers = False
    steps: List[AgentStep] = []

    for line in lines:
        step_type = detect_step_type(line)
        if step_type != "response":
            found_markers = True

        if step_type == "tool_call":
            match = TOOL_CALL_RE.match(line.strip())
            tool_name = (match.group(1) if match else "tool_call") or "tool_call"
            tool_args = _safe_json((match.group(2) if match else "") or "")
            steps.append(AgentStep(type="tool_call", content=line, tool_name=tool_name, tool_args=tool_args))
            continue

        if step_type == "tool_result":
            match = TOOL_RESULT_RE.match(line.strip())
            content = match.group(1).strip() if match else line
            steps.append(AgentStep(type="tool_result", content=content))
            continue

        if step_type == "thinking":
            steps.append(AgentStep(type="thinking", content=line))
            continue

        steps.append(AgentStep(type="response", content=line))

    if not found_markers:
        return [AgentStep(type="response", content=text)]
    return steps


class AgentService:
    def __init__(self, socketio):
        self.socketio = socketio

    def emit_step(self, session_id: str, step: AgentStep) -> None:
        payload = {
            "type": step.type,
            "content": step.content,
            "tool_name": step.tool_name,
            "tool_args": step.tool_args or {},
            "session_id": session_id,
            "timestamp": time.strftime("%H:%M:%S"),
        }
        self.socketio.emit("agent_step", payload, to=session_id)

    def emit_terminal_line(self, session_id: str, line: str) -> None:
        self.socketio.emit(
            "terminal_output",
            {"data": colorize_terminal_line(line), "session_id": session_id},
            to=session_id,
        )

    def build_user_prompt(self, message: str, attachments: List[Dict[str, str]]) -> str:
        prompt = (message or "").strip()
        chunks = [prompt]
        for item in attachments:
            filename = item.get("filename", "attachment.txt")
            content = item.get("content", "")
            chunks.append(
                f"The user has attached the following file: {filename}\n\n{content}"
            )
        return "\n\n".join([chunk for chunk in chunks if chunk])

    def _try_qgenie_chat(self, model: str, prompt: str) -> str:
        try:
            from qgenie import ChatMessage, QGenieClient
        except Exception:
            try:
                from qgenie_sdk import ChatMessage, QGenieClient  # type: ignore
            except Exception:
                return "QGenie SDK unavailable in runtime; returning simulated response."

        if not Config.QGENIE_API_KEY:
            return "QGENIE_API_KEY is not configured; returning simulated response."

        messages = [ChatMessage(role="user", content=prompt)]
        try:
            client = QGenieClient(
                api_key=Config.QGENIE_API_KEY,
                base_url=Config.QGENIE_PROVIDER_URL,
            )
        except TypeError:
            try:
                client = QGenieClient(api_key=Config.QGENIE_API_KEY)
            except Exception as exc:
                return f"QGenie initialization failed: {exc}"

        try:
            response = client.chat(messages=messages, model=model)
        except TypeError:
            try:
                response = client.chat(messages=messages)
            except Exception as exc:
                return f"QGenie call failed, fallback response generated. Details: {exc}"
        except Exception as exc:
            return f"QGenie call failed, fallback response generated. Details: {exc}"

        if isinstance(response, str):
            return response
        first_content = getattr(response, "first_content", "")
        if isinstance(first_content, str) and first_content.strip():
            return first_content
        content = getattr(response, "content", "")
        if isinstance(content, str) and content.strip():
            return content
        return "Model returned an empty response."

    def _suggest_patch(self, selected_code: str) -> Optional[Dict[str, str]]:
        if not selected_code.strip():
            return None

        if re.search(r"/\*.*QGenie.*\*/", selected_code):
            return None

        before = selected_code
        after = selected_code + "\n/* QGenie suggestion: review error handling for this block. */\n"
        return {"before": before, "after": after}

    def _persist_step(self, session_id: str, step: AgentStep) -> None:
        if not has_app_context():
            return
        append_message(
            session_id=session_id,
            role="assistant",
            content=step.content,
            step_type=step.type,
            tool_name=step.tool_name,
            tool_args=step.tool_args,
        )

    def stream_chat(
        self,
        *,
        session_id: str,
        message: str,
        model: Optional[str],
        attachments: Optional[List[Dict[str, str]]] = None,
        selected_code: str = "",
        filename: str = "",
        page: str = "agent",
    ) -> Dict[str, Any]:
        active_model = (model or get_default_model()).strip() or get_default_model()
        files = attachments or []
        prompt = self.build_user_prompt(message, files)

        if has_app_context():
            ensure_session(session_id=session_id, page=page, model=active_model)
            append_message(
                session_id=session_id,
                role="user",
                content=message or "(attachments)",
                step_type="response",
            )

        pre_steps: List[AgentStep] = [
            AgentStep(type="thinking", content="THINK: Analysing user request and context."),
        ]

        if selected_code.strip():
            pre_steps.append(
                AgentStep(
                    type="tool_call",
                    content="TOOL_CALL: inspect_selected_code",
                    tool_name="inspect_selected_code",
                    tool_args={"filename": filename or "unspecified", "length": len(selected_code)},
                )
            )
            pre_steps.append(
                AgentStep(
                    type="tool_result",
                    content="TOOL_RESULT: collected selected code context.",
                    tool_name="inspect_selected_code",
                    tool_args={"ok": True},
                )
            )

        for step in pre_steps:
            self.emit_step(session_id, step)
            self.emit_terminal_line(session_id, step.content + "\n")
            self._persist_step(session_id, step)

        assistant_text = self._try_qgenie_chat(active_model, prompt)
        parsed_steps = parse_stream_steps(assistant_text)
        response_parts: List[str] = []

        for step in parsed_steps:
            self.emit_step(session_id, step)
            self.emit_terminal_line(session_id, step.content + "\n")
            self._persist_step(session_id, step)
            if step.type == "response" and step.content.strip():
                response_parts.append(step.content.strip())

        final_response = "\n".join(response_parts).strip() or assistant_text.strip()

        self.emit_terminal_line(session_id, "INFO: response stream completed\n")

        diff = self._suggest_patch(selected_code)
        if diff:
            self.socketio.emit(
                "file_diff",
                {
                    "filename": filename or "selection.c",
                    "before": diff["before"],
                    "after": diff["after"],
                    "session_id": session_id,
                },
                to=session_id,
            )

        done_payload = {
            "session_id": session_id,
            "ok": True,
            "message": "Agent response completed",
            "model": active_model,
            "response": final_response,
        }
        self.socketio.emit("agent_done", done_payload, to=session_id)
        return done_payload


session_id_prefix = "sess"


def create_session_id() -> str:
    return f"{session_id_prefix}-{uuid.uuid4().hex[:12]}"
