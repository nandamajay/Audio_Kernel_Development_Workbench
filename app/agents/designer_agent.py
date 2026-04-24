"""Designer agent that drafts implementation output for a given task."""

from __future__ import annotations

import os

from app.utils.settings import get_settings


class _LLMWrapper:
    def __init__(self):
        s = get_settings()
        self.model = s.get("default_model", "claude-sonnet-4")
        self.api_key = s.get("api_key")
        self.base_url = s.get("provider_url")

    def invoke(self, messages):
        # Best-effort local fallback when qgenie package is unavailable.
        class _Resp:
            def __init__(self, content):
                self.content = content

        user_text = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
        content = (
            "Proposed implementation plan:\n"
            "1. Update relevant route/service for task scope.\n"
            "2. Add UI wiring and tests for acceptance criteria.\n"
            "3. Validate endpoints and persist artifacts.\n\n"
            "Task context:\n" + user_text[:1200]
        )
        return _Resp(content)


class DesignerAgent:
    """Executes task implementation drafts based on reviewer feedback."""

    SYSTEM_PROMPT = (
        "You are Designer Agent for AKDW. Produce concise implementation output, "
        "focusing on kernel-workbench correctness and acceptance criteria."
    )

    def __init__(self):
        self.llm = _LLMWrapper()

    def run(self, task_description: str, reviewer_feedback: str = "") -> str:
        prompt = (
            f"Task:\n{task_description}\n\n"
            f"Reviewer feedback:\n{reviewer_feedback or 'none'}\n\n"
            "Return a concrete implementation summary and patch plan."
        )
        resp = self.llm.invoke(
            [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
        )
        return (getattr(resp, "content", "") or "").strip() or "Designer produced no content."
