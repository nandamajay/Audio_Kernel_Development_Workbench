def test_triage_page_loads(client):
    response = client.get('/triage/')
    assert response.status_code == 200
    assert b'triagePathInput' in response.data
