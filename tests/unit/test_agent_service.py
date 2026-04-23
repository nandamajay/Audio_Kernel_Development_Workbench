from app.services.agent_service import (
    AgentService,
    colorize_terminal_line,
    detect_step_type,
    parse_stream_steps,
)
from app.services.session_service import get_session_messages


class FakeSocketIO:
    def __init__(self):
        self.events = []

    def emit(self, event, payload, to=None):
        self.events.append((event, payload, to))


def test_colorize_terminal_line():
    assert "\x1b[31m" in colorize_terminal_line("ERROR: boom")
    assert "\x1b[33m" in colorize_terminal_line("warning: check")
    assert "\x1b[32m" in colorize_terminal_line("SUCCESS")
    assert "\x1b[36m" in colorize_terminal_line("INFO: note")


def test_detect_step_type_markers():
    assert detect_step_type("THINK: planning") == "thinking"
    assert detect_step_type("TOOL_CALL: run") == "tool_call"
    assert detect_step_type("TOOL_RESULT: ok") == "tool_result"
    assert detect_step_type("Regular response") == "response"


def test_parse_stream_steps_with_markers():
    steps = parse_stream_steps(
        "THINK: planning\n"
        "TOOL_CALL: checkpatch {\"path\":\"a.patch\"}\n"
        "TOOL_RESULT: PASS\n"
        "Final response line"
    )
    assert [step.type for step in steps] == ["thinking", "tool_call", "tool_result", "response"]
    assert steps[1].tool_name == "checkpatch"
    assert steps[1].tool_args["path"] == "a.patch"


def test_stream_chat_emits_steps_and_persists(app, monkeypatch):
    fake_socket = FakeSocketIO()
    service = AgentService(fake_socket)

    monkeypatch.setattr(
        service,
        "_try_qgenie_chat",
        lambda model, prompt: "TOOL_CALL: lint {\"file\":\"foo.c\"}\nTOOL_RESULT: SUCCESS\nLooks good",
    )

    with app.app_context():
        result = service.stream_chat(
            session_id="sess-test-1",
            message="review",
            model="claude-sonnet-4",
            attachments=[],
            selected_code="int x = 0;",
            filename="/tmp/foo.c",
            page="agent",
        )

    assert result["ok"] is True
    event_names = [name for name, _, _ in fake_socket.events]
    assert "agent_step" in event_names
    assert "terminal_output" in event_names
    assert "agent_done" in event_names
    assert "file_diff" in event_names

    with app.app_context():
        messages = get_session_messages("sess-test-1")
    assert len(messages) >= 3
