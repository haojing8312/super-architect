import asyncio
import json

from fastapi.testclient import TestClient

from architecture_workbench.app import create_app
from architecture_workbench.deliverables import export_deliverable


def test_dashboard_uses_real_runtime_questions_and_saved_deliverables(tmp_path):
    app = create_app(tmp_path / 'data')
    session = app.state.store.create_session('核对建筑轴网')
    with TestClient(app) as client:
        assert client.get('/api/local/dashboard').status_code == 401
        client.headers['X-Workbench-Token'] = client.get('/api/bootstrap').json()['token']
        assert client.get('/api/local/dashboard').json() == {'tasks': [], 'artifacts': []}
        run = app.state.runtime_manager.reserve(session['id'])
        assert client.get('/api/local/dashboard').json()['tasks'][0]['status'] == 'running'
        question = app.state.interactions.present(session['id'], '采用哪个轴网？', ['A', 'B'])
        row = client.get('/api/local/dashboard').json()['tasks'][0]
        assert row['status'] == 'awaiting_input' and row['detail'] == '采用哪个轴网？'
        app.state.interactions.answer(session['id'], question['id'], 'A')
        asyncio.run(app.state.runtime_manager.release(run))
        app.state.store.add_message(session['id'], 'assistant', '轴网已核对', completed=True)
        document = export_deliverable(app.state.store, {'title': '轴网核对记录', 'sections': [{'title': '结论', 'content': '合成验收记录'}], 'formats': ['html']})
        data = client.get('/api/local/dashboard').json()
        assert data['tasks'][0]['status'] == 'completed'
        assert data['artifacts'][0]['id'] == document['id']
        assert data['artifacts'][0]['title'] == '轴网核对记录'
        context = app.state.store.data_dir / 'runtime-context'
        context.mkdir(exist_ok=True)
        (context / (session['id'] + '.json')).write_text(json.dumps({'status':'cancelled'}))
        assert client.get('/api/local/dashboard').json()['tasks'][0]['status'] == 'cancelled'
