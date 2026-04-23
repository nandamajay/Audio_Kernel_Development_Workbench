from app.services.session_service import append_message, ensure_session, get_session_messages, grouped_sessions


def test_session_and_message_crud(app):
    with app.app_context():
        ensure_session(session_id="sess-db-1", page="agent", model="claude-sonnet-4")
        append_message(session_id="sess-db-1", role="user", content="hello", step_type="response")
        append_message(session_id="sess-db-1", role="assistant", content="world", step_type="response")

        grouped = grouped_sessions()
        messages = get_session_messages("sess-db-1")

    assert "agent" in grouped
    assert grouped["agent"][0]["id"] == "sess-db-1"
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"
