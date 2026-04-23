"""Session and message persistence helpers."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Dict, List

from app.models import ConversationSession, Message, Session, db


def make_session_name(page: str) -> str:
    return f"{page.title()} - {datetime.now().strftime('%Y-%m-%d %H:%M')}"


def ensure_session(session_id: str, page: str, model: str, phase: str = "phase2") -> Session:
    session = Session.query.filter_by(id=session_id).first()
    if session:
        session.updated_at = datetime.utcnow()
        session.model_used = model
        db.session.commit()
        return session

    session = Session(
        id=session_id,
        name=make_session_name(page),
        page=page,
        phase=phase,
        status="active",
        model_used=model,
    )
    db.session.add(session)

    convo = ConversationSession(id=session_id, messages_json="[]")
    db.session.add(convo)
    db.session.commit()
    return session


def append_message(
    *,
    session_id: str,
    role: str,
    content: str,
    step_type: str | None = None,
    tool_name: str | None = None,
    tool_args: Dict | None = None,
) -> Message:
    message = Message(
        session_id=session_id,
        role=role,
        content=content,
        step_type=step_type,
        tool_name=tool_name,
        tool_args=json.dumps(tool_args or {}),
    )
    db.session.add(message)

    convo = ConversationSession.query.filter_by(id=session_id).first()
    if convo:
        current = json.loads(convo.messages_json or "[]")
        current.append(
            {
                "role": role,
                "content": content,
                "step_type": step_type,
                "tool_name": tool_name,
                "tool_args": tool_args or {},
                "timestamp": datetime.utcnow().isoformat(),
            }
        )
        convo.messages_json = json.dumps(current)

    session = Session.query.filter_by(id=session_id).first()
    if session:
        session.updated_at = datetime.utcnow()

    db.session.commit()
    return message


def grouped_sessions() -> Dict[str, List[Dict]]:
    grouped: Dict[str, List[Dict]] = {
        "patchwise": [],
        "agent": [],
        "triage": [],
        "converter": [],
    }
    rows = Session.query.order_by(Session.updated_at.desc()).limit(50).all()
    for item in rows:
        grouped.setdefault(item.page, []).append(
            {
                "id": item.id,
                "name": item.name,
                "page": item.page,
                "status": item.status,
                "model_used": item.model_used,
                "updated_at": item.updated_at.isoformat() if item.updated_at else None,
            }
        )
    return grouped


def get_session_messages(session_id: str) -> List[Dict]:
    rows = Message.query.filter_by(session_id=session_id).order_by(Message.created_at.asc()).all()
    data: List[Dict] = []
    for row in rows:
        data.append(
            {
                "id": row.id,
                "role": row.role,
                "content": row.content,
                "step_type": row.step_type,
                "tool_name": row.tool_name,
                "tool_args": json.loads(row.tool_args or "{}"),
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
    return data


def create_session_id() -> str:
    return f"sess-{uuid.uuid4().hex[:12]}"
