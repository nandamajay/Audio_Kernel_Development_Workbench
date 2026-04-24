from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from typing import Optional, TypedDict

from app.agents.architect_reviewer_agent import ArchitectReviewerAgent
from app.agents.designer_agent import DesignerAgent
from app.agents.email_notifier import EmailNotifier
from app.agents.parallel_think_agent import ParallelThinkAgent
from app.agents.project_plan_manager import ProjectPlanManager
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
