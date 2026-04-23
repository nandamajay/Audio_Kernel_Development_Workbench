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
