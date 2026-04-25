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


class ReviewSession(db.Model):
    __tablename__ = "review_sessions"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(64), nullable=False, unique=True, index=True)
    patch_hash = db.Column(db.String(128), nullable=False)
    summary = db.Column(db.Text, nullable=True)
    findings_json = db.Column(db.Text, nullable=False, default="[]")
    checkpatch_output = db.Column(db.Text, nullable=True)
    maintainers_json = db.Column(db.Text, nullable=True, default="[]")
    patch_filename = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(24), nullable=False, default="pending")
    ai_summary = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class ReviewEvidence(db.Model):
    __tablename__ = "review_evidence"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(64), nullable=False)
    finding_id = db.Column(db.String(128), nullable=False)
    evidence_type = db.Column(db.String(20), nullable=False)  # screenshot|lkml
    content = db.Column(db.Text, nullable=False)  # base64 or URL
    metadata_json = db.Column(db.Text, nullable=True, default="{}")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class PatchReviewTrace(db.Model):
    __tablename__ = "patch_review_traces"

    id = db.Column(db.Integer, primary_key=True)
    trace_id = db.Column(db.String(64), nullable=False, index=True)
    session_id = db.Column(db.String(64), nullable=False, index=True)
    stage = db.Column(db.String(64), nullable=False, index=True)
    tool = db.Column(db.String(64), nullable=True)
    status = db.Column(db.String(24), nullable=False, default="ok")
    duration_ms = db.Column(db.Integer, nullable=False, default=0)
    exit_code = db.Column(db.Integer, nullable=True)
    token_input = db.Column(db.Integer, nullable=False, default=0)
    token_output = db.Column(db.Integer, nullable=False, default=0)
    error_message = db.Column(db.Text, nullable=True)
    details_json = db.Column(db.Text, nullable=True, default="{}")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class PatchPipelineJob(db.Model):
    __tablename__ = "patch_pipeline_jobs"

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.String(64), nullable=False, unique=True, index=True)
    session_id = db.Column(db.String(64), nullable=False, index=True)
    trace_id = db.Column(db.String(64), nullable=False, index=True)
    status = db.Column(db.String(24), nullable=False, default="queued")
    progress = db.Column(db.Integer, nullable=False, default=0)
    current_step = db.Column(db.String(128), nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    result_json = db.Column(db.Text, nullable=True, default="{}")
    payload_json = db.Column(db.Text, nullable=True, default="{}")
    cancel_requested = db.Column(db.Boolean, nullable=False, default=False)
    retry_of = db.Column(db.String(64), nullable=True)
    duration_ms = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class UpstreamPatch(db.Model):
    __tablename__ = "upstream_patches"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(512))
    lore_url = db.Column(db.String(1024), unique=True)
    series_id = db.Column(db.String(256))
    submitter = db.Column(db.String(256))
    subsystem = db.Column(db.String(128))
    submitted_at = db.Column(db.DateTime)
    status = db.Column(db.String(64), nullable=False, default="submitted")
    last_checked = db.Column(db.DateTime)
    reviewer_comments = db.Column(db.Text)
    merged_tree = db.Column(db.String(256))
    tags = db.Column(db.String(256))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class ActivityLog(db.Model):
    __tablename__ = "activity_log"

    id = db.Column(db.Integer, primary_key=True)
    event = db.Column(db.String(500), nullable=False)
    event_type = db.Column(db.String(50), nullable=False, default="agent")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class Target(db.Model):
    __tablename__ = "targets"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    serial = db.Column(db.String(255), nullable=False, unique=True, index=True)
    platform = db.Column(db.String(128), nullable=True)
    status = db.Column(db.String(32), nullable=False, default="disconnected")
    last_seen = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class ValidationRun(db.Model):
    __tablename__ = "validation_runs"

    id = db.Column(db.Integer, primary_key=True)
    target_id = db.Column(db.Integer, db.ForeignKey("targets.id"), nullable=False, index=True)
    session_id = db.Column(db.String(64), nullable=False, index=True)
    nl_command = db.Column(db.Text, nullable=False)
    commands_executed = db.Column(db.Text, nullable=False, default="[]")
    raw_output = db.Column(db.Text, nullable=True)
    llm_summary = db.Column(db.Text, nullable=True)
    result = db.Column(db.String(16), nullable=False, default="ERROR")
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class TerminalCommandAudit(db.Model):
    __tablename__ = "terminal_command_audit"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.String(64), nullable=False, index=True)
    actor = db.Column(db.String(64), nullable=False, default="agent_mode")
    command = db.Column(db.Text, nullable=False)
    cwd = db.Column(db.String(512), nullable=True)
    exit_code = db.Column(db.Integer, nullable=False, default=0)
    allowed = db.Column(db.Boolean, nullable=False, default=True)
    blocked_reason = db.Column(db.String(255), nullable=True)
    output_preview = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
