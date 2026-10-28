"""History semantics with fake repositories: no live database or model calls."""
from copy import deepcopy
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from lumenx_prism.api import app, get_extractor, get_history
from lumenx_prism.history import MongoHistory, HistoryUnavailable
from lumenx_prism.schemas import EntityExtractionRequest
from test_entity_service import extractor


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv('AI_SERVICE_API_KEY', 'test-token')
    repo = Mock()
    repo.begin.return_value = str(uuid4())
    engine = extractor()
    app.dependency_overrides[get_history] = lambda: repo
    app.dependency_overrides[get_extractor] = lambda: engine
    with TestClient(app) as client:
        client.headers['Authorization'] = 'Bearer test-token'
        yield client, repo, engine
    app.dependency_overrides.clear()


def test_input_is_durable_before_model_and_success_keeps_original_body(setup):
    client, repo, engine = setup
    def infer(**kwargs):
        repo.begin.assert_called_once()
        return '{"characters": [], "scenes": [], "props": []}'
    engine.llm.chat.side_effect = infer
    response = client.post('/v1/scripts/extract-entities', json={'text': '中文剧本'})
    assert response.status_code == 200
    assert response.json() == {'characters': [], 'scenes': [], 'props': []}
    assert response.headers['x-prism-history-id'] == repo.begin.return_value
    assert repo.begin.call_args.args[0].text == '中文剧本'
    assert repo.finish.call_args.kwargs['result'] == response.json()


def test_storage_failure_does_not_call_paid_model_or_expose_uri(setup):
    client, repo, engine = setup
    repo.begin.side_effect = RuntimeError('mongodb://user:secret@private')
    response = client.post('/v1/scripts/extract-entities', json={'text': '中文'})
    assert response.status_code == 503
    assert response.json() == {'detail': {'code': 'history_unavailable'}}
    engine.llm.chat.assert_not_called()


def test_failed_inference_is_recorded_without_raw_provider_errors(setup):
    client, repo, engine = setup
    engine.llm.chat.side_effect = RuntimeError('private prompt and key')
    response = client.post('/v1/scripts/extract-entities', json={'text': '中文'})
    assert response.status_code == 502
    assert response.headers['x-prism-history-id']
    assert repo.finish.call_args.kwargs['error_code'] == 'upstream_error'
    assert 'private' not in str(repo.finish.call_args)


def test_finalize_failure_preserves_result_and_warns_without_retry(setup):
    client, repo, engine = setup
    repo.finish.side_effect = RuntimeError('secret')
    response = client.post('/v1/scripts/extract-entities', json={'text': '中文'})
    assert response.status_code == 200
    assert response.headers['x-prism-history-warning'] == 'finalize_failed'
    engine.llm.chat.assert_called_once()
    assert 'secret' not in response.text


def test_history_requires_auth_and_bounds_and_does_not_generate(setup):
    client, repo, engine = setup
    repo.list.return_value = {'items': [], 'has_more': False}
    client.headers.pop('Authorization')
    assert client.get('/v1/scripts/extract-entities/history').status_code == 401
    assert client.get('/v1/test-console/config').status_code == 401
    assert client.post('/v1/scripts/extract-entities', json={'text': '中文'}).status_code == 401
    repo.begin.assert_not_called()
    client.headers['Authorization'] = 'Bearer test-token'
    assert client.get('/v1/scripts/extract-entities/history?limit=101').status_code == 422
    assert client.get('/v1/scripts/extract-entities/history?status=invalid').status_code == 422
    assert client.get('/v1/scripts/extract-entities/history?limit=5&offset=10&status=failed').status_code == 200
    repo.list.assert_called_once_with(5, 10, 'failed')
    repo.get.return_value = None
    assert client.get('/v1/scripts/extract-entities/history/' + str(uuid4())).status_code == 404
    assert client.get('/v1/scripts/extract-entities/history/not-an-id').status_code == 422
    engine.llm.chat.assert_not_called()


def test_invalid_input_never_creates_history(setup):
    client, repo, engine = setup
    assert client.post('/v1/scripts/extract-entities', json={'text': ' '}).status_code == 422
    repo.begin.assert_not_called()
    engine.llm.chat.assert_not_called()


def test_history_optional_when_not_configured(setup):
    client, repo, engine = setup
    app.dependency_overrides[get_history] = lambda: None
    assert client.post('/v1/scripts/extract-entities', json={'text': '中文'}).status_code == 200
    assert client.get('/v1/scripts/extract-entities/history').json() == {'detail': {'code': 'history_not_configured'}}


def test_mongo_record_is_replayable_without_credentials(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'secret-key')
    monkeypatch.setenv('OPENAI_BASE_URL', 'https://private.invalid')
    monkeypatch.setenv('LLM_PROVIDER', 'openai')
    monkeypatch.setenv('OPENAI_MODEL', 'test-model')
    repo = MongoHistory('mongodb://localhost', 'lumenx_prism')
    repo.client.close()
    repo.collection = Mock()
    repo.collection.update_one.return_value.matched_count = 1
    repo.collection.insert_one.side_effect = lambda row: None
    request = EntityExtractionRequest(text='中文剧本', custom_extraction_prompt='分析 {text}')
    record_id = repo.begin(request, model_snapshot={'id': 'test', 'revision': 1, 'provider': 'openai', 'model': 'test-model'})
    row = deepcopy(repo.collection.insert_one.call_args.args[0])
    assert row['_id'] == record_id
    assert row['request'] == request.model_dump()
    assert row['effective_prompt'] == '分析 中文剧本'
    assert row['requested_model'] == 'test-model'
    assert row['status'] == 'running'
    assert 'secret-key' not in str(row) and 'private.invalid' not in str(row)
    repo.finish(record_id, result={'characters': []}, duration_ms=100)
    fields = repo.collection.update_one.call_args.args[1]['$set']
    assert fields['status'] == 'succeeded' and fields['duration_ms'] == 100
    assert repo.public(row)['id'] == record_id
    with pytest.raises(HistoryUnavailable):
        MongoHistory('mongodb://localhost', 'lumenxdb')
