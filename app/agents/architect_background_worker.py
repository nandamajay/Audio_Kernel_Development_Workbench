from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from app.agents.parallel_think_agent import ParallelThinkAgent
from app.agents.project_plan_manager import ProjectPlanManager
from app.agents.shared_state import AgentStateDB


class ArchitectBackgroundWorker:
    """Runs architect planning/enhancement analysis in parallel with designer coding."""

    def __init__(self):
        self.parallel = ParallelThinkAgent()
        self.plan_mgr = ProjectPlanManager()

    def _run_parallel_think(self, task_description: str) -> dict[str, Any]:
        self.parallel.start(task_description)
        return self.parallel.get_result(timeout=60)

    async def run(
        self,
        session_id: str,
        round_num: int,
        designer_task: str,
        architect_hint: str,
        state_db: AgentStateDB,
    ) -> dict[str, Any]:
        start_ts = datetime.utcnow().isoformat()
        state_db.append_history(
            session_id,
            round_num,
            "architect_background",
            f"started background analysis; hint={architect_hint or 'none'}",
            start_ts,
        )

        # Planning breadcrumbs are emitted early to prove parallel execution.
        await asyncio.sleep(0.05)
        next_task, next_phase = self.plan_mgr.get_next_pending_task()
        state_db.append_history(
            session_id,
            round_num,
            "architect_background",
            "next-phase planning completed",
        )

        parallel_result = await asyncio.to_thread(self._run_parallel_think, designer_task)
        state_db.append_history(
            session_id,
            round_num,
            "architect_background",
            "enhancement analysis completed",
        )

        return {
            "started_at": start_ts,
            "next_task": next_task or {},
            "next_phase": next_phase or {},
            "parallel_think": parallel_result,
        }
