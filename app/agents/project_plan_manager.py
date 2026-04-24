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
        """Return the next PENDING task across all IN_PROGRESS phases."""
        plan = self.load()
        for phase in plan["phases"]:
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
