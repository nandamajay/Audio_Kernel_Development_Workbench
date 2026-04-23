from app.services.agent_service import AgentService


def test_session_save_load_and_continue(client, monkeypatch):
    monkeypatch.setattr(AgentService, '_try_qgenie_chat', lambda self, model, prompt: 'mocked reply')

    response = client.post(
        '/agent/chat',
        json={
            'page': 'agent',
            'session_id': 'sess-replay-1',
            'model': 'claude-sonnet-4',
            'message': 'Hello',
            'attachments': [],
        },
    )
    assert response.status_code == 200

    sessions = client.get('/agent/sessions')
    assert sessions.status_code == 200
    assert sessions.json['ok'] is True

    messages = client.get('/agent/sessions/sess-replay-1/messages')
    assert messages.status_code == 200
    assert len(messages.json['messages']) >= 2

    cont = client.post('/agent/sessions/sess-replay-1/continue')
    assert cont.status_code == 200
    assert cont.json['ok'] is True
