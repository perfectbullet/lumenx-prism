"""Entity inference contract and Studio/AI service boundary; no paid API calls."""
import json
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from lumenx_prism.api import app, get_extractor
from lumenx_prism.extraction import EntityExtractionError, EntityExtractor
from lumenx_prism.schemas import EntityExtractionRequest


ENTITIES = {
    'characters': [
        {'id': 'c1', 'name': '叶墨', 'description': '黑发青年'},
        {'id': 'c2', 'name': '叶墨 (古装)', 'description': '黑发青年，穿长袍'},
    ],
    'scenes': [{'id': 's1', 'name': '书房', 'description': '木桌靠窗'}],
    'props': [{'id': 'p1', 'name': '书', 'description': '蓝色封皮'}],
}


def extractor(content=None, configured=True):
    llm = Mock(is_configured=configured)
    llm.chat.return_value = content if content is not None else json.dumps(ENTITIES)
    return EntityExtractor(llm)


def test_extraction_uses_existing_prompt_and_validates_fenced_json():
    engine = extractor('```json\n' + json.dumps(ENTITIES) + '\n```')
    result = engine.extract(EntityExtractionRequest(text='叶墨走进书房。'))
    assert result.characters[0].name == '叶墨'
    assert result.characters[0].visual_weight == 3
    message = engine.llm.chat.call_args.kwargs['messages'][0]['content']
    assert '叶墨走进书房。' in message
    assert '只提取角色' in message


@pytest.mark.parametrize('content', ['not json', '[]', '{}',
    '{"characters": "bad", "scenes": [], "props": []}',
    '{"characters": [{"name": " "}], "scenes": [], "props": []}',
    '{"characters": [{"id":"x", "name":"甲"},{"id":"x", "name":"乙"}], "scenes": [], "props": []}',
])
def test_malformed_model_output_is_not_success(content):
    with pytest.raises(EntityExtractionError, match='invalid_model_output'):
        extractor(content).extract(EntityExtractionRequest(text='一段剧本'))


def test_unconfigured_model_never_returns_mock_entities():
    engine = extractor(configured=False)
    with pytest.raises(EntityExtractionError, match='not_configured'):
        engine.extract(EntityExtractionRequest(text='一段剧本'))
    engine.llm.chat.assert_not_called()


def test_custom_prompt_keeps_literal_json_braces():
    engine = extractor()
    engine.extract(EntityExtractionRequest(text='原文', custom_extraction_prompt='返回 {"characters": []}，文本 {text}'))
    assert engine.llm.chat.call_args.kwargs['messages'][0]['content'] == '返回 {"characters": []}，文本 原文'


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv('AI_SERVICE_API_KEY', raising=False)
    app.dependency_overrides[get_extractor] = lambda: extractor()
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_http_success_returns_entities_without_project_fields(client):
    response = client.post('/v1/scripts/extract-entities', json={'text': '叶墨进入书房。'})
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {'characters', 'scenes', 'props'}
    assert data['characters'][0]['id'] == 'c1'
    assert 'frames' not in data and 'project_id' not in data


@pytest.mark.parametrize('text', ['', '   ', 'a' * 80001])
def test_http_rejects_invalid_input_without_calling_model(client, text):
    engine = extractor()
    app.dependency_overrides[get_extractor] = lambda: engine
    assert client.post('/v1/scripts/extract-entities', json={'text': text}).status_code == 422
    engine.llm.chat.assert_not_called()


def test_http_model_failure_is_sanitized(client):
    engine = extractor()
    engine.llm.chat.side_effect = RuntimeError('secret-key and private script')
    app.dependency_overrides[get_extractor] = lambda: engine
    response = client.post('/v1/scripts/extract-entities', json={'text': '一段剧本'})
    assert response.status_code == 502
    assert response.json()['detail']['code'] == 'upstream_error'
    assert 'secret-key' not in response.text


def test_http_configuration_and_invalid_output_errors(client):
    for engine, status in [(extractor(configured=False), 503), (extractor('{}'), 502)]:
        app.dependency_overrides[get_extractor] = lambda: engine
        assert client.post('/v1/scripts/extract-entities', json={'text': '一段剧本'}).status_code == status


def test_service_auth_precedes_model_call(client, monkeypatch):
    monkeypatch.setenv('AI_SERVICE_API_KEY', 'test-token')
    assert client.post('/v1/scripts/extract-entities', json={'text': '原文'}).status_code == 401
    assert client.post('/v1/scripts/extract-entities', json={'text': '原文'},
                       headers={'Authorization': 'Bearer test-token'}).status_code == 200
    assert client.get('/health').status_code == 200


def test_service_auth_can_be_temporarily_disabled(client, monkeypatch):
    monkeypatch.setenv('AI_SERVICE_API_KEY', 'configured-service-token')
    monkeypatch.setenv('PRISM_SERVICE_AUTH_ENABLED', 'false')
    assert client.post('/v1/scripts/extract-entities', json={'text': '原文'}).status_code == 200
    for path in ('/tools/entities', '/tools/models', '/tools/images'):
        page = client.get(path)
        assert page.status_code == 200
        assert 'display:none!important' in page.text
    monkeypatch.setenv('PRISM_SERVICE_AUTH_ENABLED', 'true')
    assert client.post('/v1/scripts/extract-entities', json={'text': '原文'}).status_code == 401
    assert 'display:none!important' not in client.get('/tools/images').text
