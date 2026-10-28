"""Static console and config have no model calls or credential exposure."""
from fastapi.testclient import TestClient

from lumenx_prism.api import app, get_model_configs
from unittest.mock import Mock


def test_console_assets_and_safe_metadata(monkeypatch):
    monkeypatch.setenv('AI_SERVICE_API_KEY', 'test-token')
    monkeypatch.setenv('LLM_PROVIDER', 'openai')
    monkeypatch.setenv('OPENAI_API_KEY', 'private-model-secret')
    monkeypatch.setenv('OPENAI_BASE_URL', 'https://private.example.invalid')
    monkeypatch.setenv('OPENAI_MODEL', 'configured-model')
    configs=Mock()
    configs.resolve_entity_model.return_value={'_id': 'test', 'revision': 1, 'provider': 'openai', 'model': 'configured-model'}
    app.dependency_overrides[get_model_configs]=lambda:configs
    with TestClient(app) as client:
        for path in ['/', '/tools/entities']:
            response = client.get(path)
            assert response.status_code == 200
            assert '剧本实体提取' in response.text
            assert 'test-token' not in response.text
        assert client.get('/tools/models').status_code == 200
        for asset in ['entities.js', 'entities.css', 'sample.json', 'models.html', 'models.js', 'models.css']:
            assert client.get('/static/' + asset).status_code == 200
        response = client.get('/v1/test-console/config', headers={'Authorization': 'Bearer test-token'})
        assert response.status_code == 200
        assert response.json() == {'provider': 'openai', 'model': 'configured-model',
                                   'model_configured': True, 'history_configured': True, 'model_config_id': 'test', 'model_revision': 1}
        assert 'private' not in response.text
        assert response.headers['Cache-Control'] == 'no-store'

    app.dependency_overrides.clear()
