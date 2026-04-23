"""PatchWise parsing helpers (Phase 2 baseline)."""

from __future__ import annotations

from typing import Dict, List


def parse_review_cards(raw: str) -> List[Dict[str, str]]:
    cards: List[Dict[str, str]] = []
    for line in (raw or "").splitlines():
        text = line.strip()
        if not text:
            continue
        severity = "INFO"
        if "error" in text.lower():
            severity = "HIGH"
        elif "warn" in text.lower():
            severity = "MEDIUM"
        cards.append(
            {
                "file": "unknown",
                "line": "0",
                "issue": text,
                "suggestion": "Review and fix manually",
                "severity": severity,
            }
        )
    return cards
