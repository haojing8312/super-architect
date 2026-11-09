from fastapi.testclient import TestClient
from architecture_workbench.app import create_app


def test_local_capability_and_cross_origin(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.get('/api/sessions').status_code == 401
        assert client.get('/api/bootstrap', headers={'Host':'rebound.example'}).status_code == 400
        assert client.get('/', headers={'Host':'rebound.example'}).status_code == 400
        token = client.get('/api/bootstrap').json()['token']
        assert client.get('/api/sessions', headers={'X-Workbench-Token': token}).status_code == 200
        assert client.post('/api/memories', json={'content':'test'}, headers={'Origin':'https://unrelated.invalid','X-Workbench-Token':token}).status_code == 403
        assert client.get('/api/bootstrap', headers={'Sec-Fetch-Site':'cross-site'}).status_code == 403
        client.headers['X-Workbench-Token'] = token
        item = client.post('/api/attachments', files={'file':('a.txt',b'test','text/plain')}).json()
        client.headers.pop('X-Workbench-Token')
        assert client.get(item['url']).status_code == 401
        assert client.get(item['url']+'?token='+token).content == b'test'
