import json
import threading

from app.utils.settings import get_settings


class _LLMWrapper:
    def __init__(self):
        s = get_settings()
        self.model = s.get("default_model", "claude-sonnet-4")

    def invoke(self, messages):
        class _Resp:
            def __init__(self, content):
                self.content = content

        task = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
        result = {
            "corner_cases": [
                "Empty input payloads should not crash route handlers.",
                "Concurrent session updates should not race on shared state.",
            ],
            "enhancements": [
                {
                    "title": "Add structured telemetry",
                    "rationale": "Improves debugging for autonomous loops.",
                }
            ],
            "risks": ["Potential stale plan cache if file writes fail."],
            "upstream_concerns": ["Need deterministic error handling for reviewer approvals."],
            "manual_test_needed": False,
            "manual_test_reason": "",
        }
        return _Resp(json.dumps(result))


class ParallelThinkAgent:
    """Runs concurrently while the Designer is generating code.
    Analyses the current task for:
      - Edge cases the Designer might miss
      - Enhancement opportunities
      - Upstream compatibility concerns
      - Security/stability risks
    Results are injected into the Reviewer's context."""

    def __init__(self):
        self.llm = _LLMWrapper()
        self._result = None
        self._thread = None

    SYSTEM_PROMPT = """You are the Architect's analytical co-processor.
While the Designer agent is writing code for the given task,
your job is to CONCURRENTLY think about:

1. CORNER CASES: What edge cases could the Designer miss?
   List them as testable assertions.
2. ENHANCEMENTS: What improvements beyond the task spec
   would make this kernel driver more robust/upstream-ready?
3. RISKS: Any locking, memory, IRQ, or DT binding concerns?
4. UPSTREAM CONCERNS: Would a kernel maintainer reject this?
   What would they ask for?

Format your output as structured JSON:
{
  "corner_cases": ["...", "..."],
  "enhancements": [{"title": "...", "rationale": "..."}],
  "risks": ["...", "..."],
  "upstream_concerns": ["...", "..."],
  "manual_test_needed": true/false,
  "manual_test_reason": "..."
}"""

    def start(self, task_description: str):
        """Non-blocking — starts thinking in background thread."""
        self._result = None
        self._thread = threading.Thread(
            target=self._think,
            args=(task_description,),
            daemon=True,
        )
        self._thread.start()

    def _think(self, task_description: str):
        try:
            resp = self.llm.invoke(
                [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": f"Task being implemented:\n{task_description}",
                    },
                ]
            )
            raw = resp.content.strip()
            if "```" in raw:
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            self._result = json.loads(raw)
        except Exception as e:
            self._result = {
                "error": str(e),
                "corner_cases": [],
                "enhancements": [],
                "risks": [],
                "upstream_concerns": [],
                "manual_test_needed": False,
            }

    def get_result(self, timeout: int = 60) -> dict:
        """Wait for parallel think to complete and return result."""
        if self._thread:
            self._thread.join(timeout=timeout)
        return self._result or {}
