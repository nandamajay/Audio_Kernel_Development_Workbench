"""AKDW database models."""

from datetime import datetime

from flask_sqlalchemy import SQLAlchemy


db = SQLAlchemy()


class PatchRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    commit_range = db.Column(db.String(255), nullable=False)
    summary = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(32), nullable=False, default="pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class TriageSession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    input_type = db.Column(db.String(32), nullable=False, default="log")
    input_payload = db.Column(db.Text, nullable=False)
    report = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class ConversionJob(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    conversion_type = db.Column(db.String(64), nullable=False)
    source_input = db.Column(db.Text, nullable=False)
    converted_output = db.Column(db.Text, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class ConversationSession(db.Model):
    id = db.Column(db.String(64), primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    messages_json = db.Column(db.Text, nullable=False, default="[]")


class Session(db.Model):
    __tablename__ = "sessions"

    id = db.Column(db.String(64), primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    page = db.Column(db.String(32), nullable=False)
    phase = db.Column(db.String(32), nullable=True)
    status = db.Column(db.String(16), nullable=False, default="active")
    model_used = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class Message(db.Model):
    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(64), db.ForeignKey("sessions.id"), nullable=False)
    role = db.Column(db.String(16), nullable=False)
    content = db.Column(db.Text, nullable=False)
    step_type = db.Column(db.String(32), nullable=True)
    tool_name = db.Column(db.String(128), nullable=True)
    tool_args = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
