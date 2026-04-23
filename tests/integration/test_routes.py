def test_core_routes_status(client, app):
    response = client.get('/health')
    assert response.status_code == 200

    response = client.get('/')
    assert response.status_code == 200

    response = client.get('/editor/')
    assert response.status_code == 200

    response = client.get('/agent/')
    assert response.status_code == 200

    response = client.get('/settings')
    assert response.status_code == 200

    response = client.get('/setup')
    assert response.status_code == 200


def test_fs_api_routes(client, app):
    with app.app_context():
        kernel = app.config['KERNEL_SRC_PATH']

    response = client.get('/api/fs/browse', query_string={'path': kernel})
    assert response.status_code == 200
    assert response.json['ok'] is True
