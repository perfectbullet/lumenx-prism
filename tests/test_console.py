"""Static console and config have no model calls or credential exposure."""
from fastapi.testclient import TestClient

from lumenx_prism.api import app


def test_console_assets_and_safe_metadata(monkeypatch):
    monkeypatch.setenv('AI_SERVICE_API_KEY', 'test-token')
    monkeypatch.setenv('LLM_PROVIDER', 'openai')
    monkeypatch.setenv('OPENAI_API_KEY', 'private-model-secret')
    monkeypatch.setenv('OPENAI_BASE_URL', 'https://private.example.invalid')
    monkeypatch.setenv('OPENAI_MODEL', 'configured-model')
    with TestClient(app) as client:
        for path in ['/', '/tools/entities']:
            response = client.get(path)
            assert response.status_code == 200
            assert '剧本实体提取' in response.text
            assert 'test-token' not in response.text
        for asset in ['entities.js', 'entities.css', 'sample.json']:
            assert client.get('/static/' + asset).status_code == 200
        response = client.get('/v1/test-console/config', headers={'Authorization': 'Bearer test-token'})
        assert response.status_code == 200
        assert response.json() == {'provider': 'openai', 'model': 'configured-model',
                                   'model_configured': True, 'history_configured': False}
        assert 'private' not in response.text
        assert response.headers['Cache-Control'] == 'no-store'
