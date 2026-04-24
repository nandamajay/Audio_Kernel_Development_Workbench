from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Optional, TypedDict

from app.agents.architect_background_worker import ArchitectBackgroundWorker
from app.agents.architect_reviewer_agent import ArchitectReviewerAgent
from app.agents.designer_agent import DesignerAgent
from app.agents.email_notifier import EmailNotifier
from app.agents.parallel_think_agent import ParallelThinkAgent
from app.agents.project_plan_manager import ProjectPlanManager
from app.agents.shared_state import AgentStateDB
from app.db import DB_PATH

try:
    from langgraph.graph import END, StateGraph  # type: ignore
except Exception:  # pragma: no cover
    END = "__END__"

    class _CompiledGraph:
        def __init__(self, graph):
            self.graph = graph

        def invoke(self, state):
            current = self.graph.entry
            while current != END:
                node = self.graph.nodes[current]
                state = node(state)
                if current in self.graph.edges:
                    current = self.graph.edges[current]
                elif current in self.graph.conds:
                    router, mapping = self.graph.conds[current]
                    current = mapping[router(state)]
                else:
                    current = END
            return state

    class StateGraph:
        def __init__(self, _typed):
            self.nodes = {}
            self.entry = None
            self.edges = {}
            self.conds = {}

        def add_node(self, name, fn):
            self.nodes[name] = fn

        def set_entry_point(self, name):
            self.entry = name

        def add_edge(self, left, right):
            self.edges[left] = right

        def add_conditional_edges(self, node, router, mapping):
            self.conds[node] = (router, mapping)

        def compile(self):
            return _CompiledGraph(self)


MAX_ROUNDS = 5


class AgentState(TypedDict):
    task_description: str
    current_task_id: str
    designer_output: Optional[str]
    parallel_think_result: Optional[dict]
    review_result: Optional[dict]
    round_num: int
    verdict: Optional[str]
    session_id: str
    history: list


def create_orchestrator():
    designer = DesignerAgent()
    architect = ArchitectReviewerAgent()
    parallel_think = ParallelThinkAgent()
    plan_mgr = ProjectPlanManager()
    notifier = EmailNotifier()

    def designer_node(state: AgentState) -> AgentState:
        feedback = ""
        if state["review_result"]:
            feedback = "\n".join(
                state["review_result"].get("issues", [])
            )
        parallel_think.start(state["task_description"])
        output = designer.run(state["task_description"], feedback)
        state["designer_output"] = output
        state["parallel_think_result"] = parallel_think.get_result(60)
        state["round_num"] += 1
        _save_to_db(state, "designer_output", output)
        _save_to_db(state, "parallel_think", json.dumps(state["parallel_think_result"] or {}))
        return state

    def reviewer_node(state: AgentState) -> AgentState:
        result = architect.review(
            task_description=state["task_description"],
            designer_output=state["designer_output"] or "",
            parallel_think_result=state["parallel_think_result"] or {},
            current_task_id=state["current_task_id"],
            round_num=state["round_num"],
        )
        state["review_result"] = result
        state["verdict"] = result["verdict"]
        _save_to_db(state, "review_result", json.dumps(result))

        if result["verdict"] == ArchitectReviewerAgent.VERDICT_APPROVED:
            next_task, _ = plan_mgr.get_next_pending_task()
            if next_task:
                state["task_description"] = next_task["description"]
                state["current_task_id"] = next_task["id"]
                state["round_num"] = 0
        return state

    def route(state: AgentState) -> str:
        v = state.get("verdict")
        r = state.get("round_num", 0)
        if v == ArchitectReviewerAgent.VERDICT_HUMAN:
            return "end"
        if v == ArchitectReviewerAgent.VERDICT_APPROVED:
            next_task, _ = plan_mgr.get_next_pending_task()
            if next_task and r < MAX_ROUNDS:
                return "designer"
            return "end"
        if r >= MAX_ROUNDS:
            return "end"
        return "designer"

    graph = StateGraph(AgentState)
    graph.add_node("designer", designer_node)
    graph.add_node("reviewer", reviewer_node)
    graph.set_entry_point("designer")
    graph.add_edge("designer", "reviewer")
    graph.add_conditional_edges(
        "reviewer",
        route,
        {
            "designer": "designer",
            "end": END,
        },
    )

    def on_finish(state: AgentState):
        plan = plan_mgr.load()
        summary = json.dumps(plan["completed_tasks"], indent=2)
        notifier.send(
            "TASK_SUMMARY",
            f"Run complete — {len(plan['completed_tasks'])} tasks done",
            f"<h3>Completed Tasks:</h3>"
            f"<pre style='background:rgba(0,0,0,0.4);padding:16px;border-radius:8px'>{summary}</pre>"
            f"<h3>Enhancement Backlog: {len(plan['enhancement_backlog'])} items</h3>",
            {
                "Total Rounds": state["round_num"],
                "Final Verdict": state.get("verdict", ""),
                "Session": state["session_id"],
            },
        )

    return graph.compile(), on_finish


def _save_to_db(state: AgentState, role: str, content: str):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
          CREATE TABLE IF NOT EXISTS dual_agent_history (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id  TEXT    NOT NULL,
              round_num   INTEGER NOT NULL,
              role        TEXT    NOT NULL,
              content     TEXT    NOT NULL,
              timestamp   TEXT    NOT NULL
          )
      """
    )
    conn.execute(
        """
          INSERT INTO dual_agent_history
            (session_id, round_num, role, content, timestamp)
          VALUES (?,?,?,?,?)
      """,
        (
            state["session_id"],
            state["round_num"],
            role,
            content,
            datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()
    conn.close()


class AKDWParallelGraph:
    """Async dual-agent graph for parallel designer/architect execution."""

    def __init__(self):
        self.designer = DesignerAgent()
        self.architect = ArchitectReviewerAgent()
        self.plan_mgr = ProjectPlanManager()
        self.notifier = EmailNotifier()
        self.state_db = AgentStateDB()
        self.background_worker = ArchitectBackgroundWorker()

    async def _run_designer(
        self,
        session_id: str,
        round_num: int,
        task_description: str,
        feedback: str,
    ) -> str:
        self.state_db.append_history(session_id, round_num, "designer_lifecycle", "designer started")
        # Small delay to make parallel architect outputs observable before finish.
        await asyncio.sleep(0.20)
        output = await asyncio.to_thread(self.designer.run, task_description, feedback)
        self.state_db.append_history(session_id, round_num, "designer_output", output)
        self.state_db.append_history(session_id, round_num, "designer_lifecycle", "designer finished")
        return output

    def _resolve_task_identity(self, phase: int, task_description: str) -> tuple[str, str]:
        plan = self.plan_mgr.load()
        phase_obj = next((p for p in plan.get("phases", []) if int(p.get("id", 0)) == int(phase)), None)
        if phase_obj:
            pending = next((t for t in phase_obj.get("tasks", []) if t.get("status") == "PENDING"), None)
            if pending:
                return str(pending.get("id", f"{phase}.1")), str(pending.get("description") or task_description)
            # No pending task in this phase: create an ad-hoc task so completion updates plan.
            seq = len(phase_obj.get("tasks", [])) + 1
            task_id = f"{phase}.{seq}"
            ad_hoc = {
                "id": task_id,
                "name": f"Ad-hoc execution task {task_id}",
                "status": "PENDING",
                "assigned_to": "Designer",
                "description": task_description or f"Ad-hoc task in phase {phase}",
                "auto_created": True,
                "created_at": datetime.utcnow().isoformat(),
            }
            phase_obj.setdefault("tasks", []).append(ad_hoc)
            self.plan_mgr.save(plan)
            return task_id, ad_hoc["description"]
        return f"{phase}.adhoc", task_description

    def _force_human_gate(self, architect_hint: str) -> bool:
        hint = (architect_hint or "").strip().lower()
        return any(token in hint for token in ("human_review", "force_human", "manual_gate", "pause_loop"))

    def _parallel_evidence(self, history: list[dict[str, Any]]) -> dict[str, Any]:
        architect_first = None
        designer_start = None
        designer_finish = None
        for row in history:
            role = row.get("role")
            ts = row.get("timestamp")
            content = (row.get("content") or "").lower()
            if role == "architect_background" and architect_first is None:
                architect_first = ts
            if role == "designer_lifecycle" and "started" in content and designer_start is None:
                designer_start = ts
            if role == "designer_lifecycle" and "finished" in content:
                designer_finish = ts
        before_finish = bool(architect_first and designer_finish and architect_first <= designer_finish)
        overlap = bool(architect_first and designer_start and architect_first >= designer_start)
        return {
            "architect_output_before_designer_finish": before_finish,
            "overlap_detected": overlap or before_finish,
            "architect_first_output_ts": architect_first,
            "designer_started_ts": designer_start,
            "designer_finished_ts": designer_finish,
        }

    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        session_id = state.get("session_id") or f"da-run-{uuid.uuid4().hex[:10]}"
        phase = int(state.get("current_phase") or 0)
        designer_task = (state.get("designer_task") or "").strip()
        architect_hint = (state.get("architect_task") or "").strip()
        review_status = "pending"
        max_rounds = int(state.get("max_rounds") or MAX_ROUNDS)
        feedback = ""
        enhancement_log = list(state.get("enhancement_log") or [])

        task_id, resolved_task = self._resolve_task_identity(phase, designer_task)
        task_description = resolved_task or designer_task

        self.state_db.upsert_session(session_id, task_id, "running")
        self.state_db.append_history(session_id, 0, "orchestrator", f"parallel run started for {task_id}")

        verdict = "NEEDS_REVISION"
        last_review: dict[str, Any] = {}
        rounds_executed = 0

        for round_num in range(1, max_rounds + 1):
            rounds_executed = round_num
            architect_bg = asyncio.create_task(
                self.background_worker.run(
                    session_id=session_id,
                    round_num=round_num,
                    designer_task=task_description,
                    architect_hint=architect_hint,
                    state_db=self.state_db,
                )
            )
            designer_job = asyncio.create_task(
                self._run_designer(
                    session_id=session_id,
                    round_num=round_num,
                    task_description=task_description,
                    feedback=feedback,
                )
            )

            architect_ctx, designer_output = await asyncio.gather(architect_bg, designer_job)
            parallel_result = architect_ctx.get("parallel_think") or {}

            review_result = await asyncio.to_thread(
                self.architect.review,
                task_description,
                designer_output,
                parallel_result,
                task_id,
                round_num,
            )

            if self._force_human_gate(architect_hint):
                review_result["verdict"] = ArchitectReviewerAgent.VERDICT_HUMAN
                review_result["human_review_reason"] = (
                    review_result.get("human_review_reason")
                    or "Forced by architect_hint manual gate."
                )
                self.plan_mgr.add_human_review_item(
                    {
                        "task_id": task_id,
                        "reason": review_result["human_review_reason"],
                        "round": round_num,
                    }
                )
                self.notifier.send(
                    "REVIEW_REQUIRED",
                    f"Task {task_id} — forced manual gate",
                    f"<p>Dual-agent run paused by manual architect gate.</p>"
                    f"<p><b>Reason:</b> {review_result['human_review_reason']}</p>",
                    {
                        "Session": session_id,
                        "Task": task_id,
                        "Round": round_num,
                    },
                )

            verdict = review_result.get("verdict", ArchitectReviewerAgent.VERDICT_REVISE)
            last_review = review_result
            review_status = verdict
            enhancement_log.extend(review_result.get("enhancements") or [])
            self.state_db.append_history(
                session_id,
                round_num,
                "review_result",
                AgentStateDB.as_json(review_result),
            )

            if verdict == ArchitectReviewerAgent.VERDICT_APPROVED:
                next_task, next_phase = self.plan_mgr.get_next_pending_task()
                if next_task:
                    self.state_db.append_history(
                        session_id,
                        round_num,
                        "architect_background",
                        f"auto-queued next task {next_task.get('id')} - {next_task.get('name')}",
                    )
                    task_id = next_task.get("id", task_id)
                    task_description = next_task.get("description", task_description)
                    phase = int((next_phase or {}).get("id", phase))
                    if bool(state.get("auto_continue", True)):
                        feedback = ""
                        continue
                break

            if verdict == ArchitectReviewerAgent.VERDICT_REVISE:
                issues = review_result.get("issues") or []
                feedback = "\n".join(issues) if issues else "Please tighten implementation details."
                continue

            if verdict == ArchitectReviewerAgent.VERDICT_HUMAN:
                break

        final_status = "paused" if verdict == ArchitectReviewerAgent.VERDICT_HUMAN else "complete"
        self.state_db.upsert_session(session_id, task_id, final_status, verdict=verdict)
        history = self.state_db.get_history(session_id)
        evidence = self._parallel_evidence(history)

        return {
            "session_id": session_id,
            "current_phase": phase,
            "review_status": review_status,
            "rounds_executed": rounds_executed,
            "final_verdict": verdict,
            "project_plan": self.plan_mgr.load(),
            "enhancement_log": enhancement_log,
            "email_queue": self.notifier.get_recent_logs(10),
            "history": history,
            "parallel_evidence": evidence,
            "latest_review": last_review,
        }


def build_akdw_graph() -> AKDWParallelGraph:
    return AKDWParallelGraph()
