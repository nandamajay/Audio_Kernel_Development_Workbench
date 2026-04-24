import json
import os
from datetime import datetime

PLAN_PATH = os.path.join(
    os.path.dirname(__file__), "../data/PROJECT_PLAN.json"
)


class ProjectPlanManager:
    """Reviewer/Architect's persistent design plan — the single
    source of truth for all phases, tasks, and enhancements."""

    def load(self):
        with open(PLAN_PATH, encoding="utf-8") as f:
            return json.load(f)

    def save(self, plan):
        with open(PLAN_PATH, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2)

    def get_next_pending_task(self):
        """Return the next PENDING task, prioritizing current_phase first."""
        plan = self.load()
        phases = list(plan.get("phases", []))
        current_phase = int(plan.get("current_phase", 0) or 0)

        def _phase_id(phase_obj):
            try:
                return int(phase_obj.get("id", 0) or 0)
            except Exception:
                return 0

        phases.sort(key=lambda p: (0 if _phase_id(p) == current_phase else 1, _phase_id(p)))
        for phase in phases:
            if phase["status"] in ("IN_PROGRESS", "PLANNED"):
                for task in phase.get("tasks", []):
                    if task["status"] == "PENDING":
                        return task, phase
        return None, None

    def mark_task_complete(self, task_id: str):
        plan = self.load()
        for phase in plan["phases"]:
            for task in phase.get("tasks", []):
                if task["id"] == task_id:
                    task["status"] = "COMPLETE"
                    task["completed_at"] = datetime.utcnow().isoformat()
                    if not any(t.get("id") == task_id for t in plan["completed_tasks"]):
                        plan["completed_tasks"].append(task)

        for phase in plan["phases"]:
            tasks = phase.get("tasks", [])
            if tasks and all(t["status"] == "COMPLETE" for t in tasks):
                if phase["status"] == "IN_PROGRESS":
                    phase["status"] = "COMPLETE"
                    for p in plan["phases"]:
                        if p["status"] == "PLANNED":
                            p["status"] = "IN_PROGRESS"
                            plan["current_phase"] = p["id"]
                            break
        self.save(plan)

    def add_enhancement(self, enhancement: dict):
        plan = self.load()
        enhancement = dict(enhancement or {})
        enhancement["id"] = f"ENH-{len(plan['enhancement_backlog'])+1}"
        enhancement["proposed_at"] = datetime.utcnow().isoformat()
        enhancement["status"] = "PROPOSED"
        plan["enhancement_backlog"].append(enhancement)
        self.save(plan)
        return enhancement["id"]

    def add_human_review_item(self, item: dict):
        plan = self.load()
        row = dict(item or {})
        row["queued_at"] = datetime.utcnow().isoformat()
        row["status"] = "AWAITING_HUMAN"
        plan["human_review_queue"].append(row)
        self.save(plan)

    def get_full_context_for_reviewer(self) -> str:
        """Return a rich text summary of the full project plan
        for injection into the Architect's system prompt."""
        plan = self.load()
        lines = [
            f"PROJECT: {plan['project']}",
            f"CURRENT PHASE: {plan['current_phase']}",
            "",
            "PHASES SUMMARY:",
        ]
        for phase in plan["phases"]:
            lines.append(
                f"  Phase {phase['id']} [{phase['status']}]: {phase['name']}"
            )
            for t in phase.get("tasks", []):
                lines.append(
                    f"    Task {t['id']} [{t['status']}]: {t['name']}"
                )
        lines += [
            "",
            f"ENHANCEMENT BACKLOG: {len(plan['enhancement_backlog'])} items",
            f"HUMAN REVIEW QUEUE:  {len(plan['human_review_queue'])} items",
        ]
        return "\n".join(lines)

    def auto_create_phase_from_enhancements(
        self,
        phase_id: int = 5,
        phase_name: str = "Enhancement Execution Sprint",
    ) -> dict:
        """Promote enhancement backlog items into a new execution phase."""
        plan = self.load()
        phases = plan.get("phases", [])

        existing = next((p for p in phases if p.get("id") == phase_id), None)
        if existing:
            return {
                "created": False,
                "phase_id": phase_id,
                "tasks_created": 0,
                "phase_status": existing.get("status", "PLANNED"),
                "message": "Phase already exists",
            }

        backlog = plan.get("enhancement_backlog", [])
        candidates = []
        seen_titles = set()
        for enh in backlog:
            if str(enh.get("status", "")).upper() not in {"PROPOSED", "NEW"}:
                continue
            title = (enh.get("title") or "Enhancement").strip()
            norm = title.lower()
            if norm in seen_titles:
                continue
            seen_titles.add(norm)
            candidates.append(enh)

        tasks = []
        for idx, enh in enumerate(candidates, start=1):
            task_id = f"{phase_id}.{idx}"
            title = (enh.get("title") or f"Enhancement {idx}").strip()
            rationale = (enh.get("rationale") or "").strip()
            effort = (enh.get("effort") or "M").strip().upper()
            priority = "MEDIUM"
            if effort == "S":
                priority = "LOW"
            elif effort == "L":
                priority = "HIGH"
            tasks.append(
                {
                    "id": task_id,
                    "name": title,
                    "status": "PENDING",
                    "assigned_to": "Designer",
                    "description": rationale or f"Implement enhancement: {title}",
                    "source_enhancement_id": enh.get("id", ""),
                    "priority": priority,
                }
            )
            enh["status"] = "PLANNED_FOR_EXECUTION"
            enh["promoted_task_id"] = task_id
            enh["promoted_at"] = datetime.utcnow().isoformat()

        if not tasks:
            tasks = [
                {
                    "id": f"{phase_id}.1",
                    "name": "Architect Hardening Sweep",
                    "status": "PENDING",
                    "assigned_to": "Designer",
                    "description": "Run one hardening cycle across dual-agent orchestration and UI stability.",
                    "priority": "MEDIUM",
                }
            ]

        all_prior_complete = all(
            p.get("status") == "COMPLETE" for p in phases if int(p.get("id", 0)) < phase_id
        )
        phase_status = "IN_PROGRESS" if all_prior_complete else "PLANNED"
        phases.append(
            {
                "id": phase_id,
                "name": phase_name,
                "status": phase_status,
                "tasks": tasks,
                "created_at": datetime.utcnow().isoformat(),
                "auto_generated": True,
            }
        )
        plan["phases"] = phases
        if phase_status == "IN_PROGRESS":
            plan["current_phase"] = phase_id

        self.save(plan)
        return {
            "created": True,
            "phase_id": phase_id,
            "tasks_created": len(tasks),
            "phase_status": phase_status,
            "message": "Phase created",
        }

    def activate_phase(self, phase_id: int, force: bool = False) -> dict:
        """Set an existing phase as the active phase/current_phase.

        If prior phases are incomplete, activation is blocked unless force=True.
        """
        plan = self.load()
        phases = list(plan.get("phases", []))

        target = next((p for p in phases if int(p.get("id", 0) or 0) == int(phase_id)), None)
        if not target:
            return {
                "ok": False,
                "error": f"Phase {phase_id} not found",
                "phase_id": phase_id,
            }

        current_phase = int(plan.get("current_phase", 0) or 0)
        if current_phase == int(phase_id) and target.get("status") in ("IN_PROGRESS", "COMPLETE"):
            return {
                "ok": True,
                "phase_id": int(phase_id),
                "current_phase": int(phase_id),
                "forced": bool(force),
                "prior_incomplete": [],
                "message": "Phase already active",
            }

        prior_incomplete = [
            {
                "id": int(p.get("id", 0) or 0),
                "name": p.get("name", ""),
                "status": p.get("status", "PLANNED"),
            }
            for p in phases
            if int(p.get("id", 0) or 0) < int(phase_id)
            and p.get("status") != "COMPLETE"
        ]
        if prior_incomplete and not force:
            return {
                "ok": False,
                "error": f"Cannot activate Phase {phase_id} while prior phases are incomplete",
                "phase_id": phase_id,
                "prior_incomplete": prior_incomplete,
            }

        for phase in phases:
            pid = int(phase.get("id", 0) or 0)
            if pid == int(phase_id):
                if phase.get("status") != "COMPLETE":
                    phase["status"] = "IN_PROGRESS"
            elif phase.get("status") == "IN_PROGRESS":
                tasks = phase.get("tasks", [])
                if tasks and all(t.get("status") == "COMPLETE" for t in tasks):
                    phase["status"] = "COMPLETE"
                else:
                    phase["status"] = "PLANNED"

        plan["phases"] = phases
        plan["current_phase"] = int(phase_id)
        self.save(plan)
        return {
            "ok": True,
            "phase_id": int(phase_id),
            "current_phase": int(phase_id),
            "forced": bool(force),
            "prior_incomplete": prior_incomplete,
        }
