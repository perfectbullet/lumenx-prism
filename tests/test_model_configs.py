"""Model repository/API behavior with a fake database, no paid provider requests."""
from unittest.mock import Mock
import json

import mongomock
import pytest
from fastapi.testclient import TestClient

from lumenx_prism.api import app, get_model_configs, get_history
from lumenx_prism.model_configs import ModelConfigs, ModelConfigInput, ConfigError

PAYLOAD = dict(name='实体模型', kind='text', provider='custom', protocol='openai_chat',
               model='test-model', base_url='https://provider.invalid/v1', api_key='test-secret-key',
               parameters={'temperature': 0})


@pytest.fixture
def repo():
    return ModelConfigs(mongomock.MongoClient()['lumenx_prism'])


def test_definitions_support_all_kinds_and_secrets_are_write_only(repo):
    for kind, protocol in [('text','openai_chat'), ('speech','openai_audio'),
                           ('image','openai_images'), ('video','custom_http')]:
        row=repo.save(ModelConfigInput(**(PAYLOAD | {'kind':kind, 'protocol':protocol})))
        assert row['kind']==kind and row['api_key_set']
        assert 'api_key' not in row and 'test-secret-key' not in json.dumps(row,default=str)
    assert len(repo.list()['items'])==4
    assert repo.collection.name=='model_comfigs'


def test_versioned_update_preserves_key_and_rejects_stale_writes(repo):
    row=repo.save(ModelConfigInput(**PAYLOAD))
    payload=ModelConfigInput(**(PAYLOAD | {'name':'改名','api_key':'','revision':1}))
    updated=repo.save(payload,row['id'])
    assert updated['revision']==2
    assert repo.get(row['id'])['api_key']=='test-secret-key'
    with pytest.raises(ConfigError,match='model_revision_conflict'):
        repo.save(payload,row['id'])


def test_binding_is_separate_from_model_and_runtime_rechecks_enabled(repo):
    row=repo.save(ModelConfigInput(**PAYLOAD))
    with pytest.raises(ConfigError,match='model_not_configured'):
        repo.resolve_entity_model()
    repo.bind_entity_model(row['id'])
    assert repo.resolve_entity_model()['_id']==row['id']
    assert len(repo.list()['items'])==1  # Binding singleton is not a model.
    repo.save(ModelConfigInput(**(PAYLOAD | {'revision':1,'enabled':False})),row['id'])
    with pytest.raises(ConfigError,match='model_config_disabled'):
        repo.resolve_entity_model()


def test_only_text_chat_can_be_bound(repo):
    row=repo.save(ModelConfigInput(**(PAYLOAD | {'kind':'image','protocol':'openai_images'})))
    with pytest.raises(ConfigError,match='model_config_incompatible'):
        repo.bind_entity_model(row['id'])


@pytest.mark.parametrize('change',[{'base_url':'ftp://invalid'}, {'parameters':{'model':'override'}},
                                  {'parameters':{'temperature':3}}, {'parameters':{'unexpected':1}},
                                  {'parameters':{'extra_body':{'model':'override'}}},
                                  {'parameters':{'extra_body':{'messages':[]}}}])
def test_invalid_configuration_rejected(change):
    with pytest.raises(ValueError):
        ModelConfigInput(**(PAYLOAD|change))


def test_crud_and_binding_are_authenticated_and_extraction_uses_saved_model(repo,monkeypatch):
    monkeypatch.setenv('AI_SERVICE_API_KEY','token')
    app.dependency_overrides[get_model_configs]=lambda:repo
    app.dependency_overrides[get_history]=lambda:None
    try:
        with TestClient(app) as client:
            assert client.post('/v1/model-configs',json=PAYLOAD).status_code==401
            client.headers['Authorization']='Bearer token'
            created=client.post('/v1/model-configs',json=PAYLOAD)
            assert created.status_code==201
            cid=created.json()['id']
            assert 'test-secret-key' not in created.text
            assert client.get('/v1/model-configs').json()['items'][0]['id']==cid
            assert client.put(f'/v1/model-configs/{cid}/entity-extraction').status_code==200
            assert client.get('/v1/test-console/config').json()['model_config_id']==cid
            assert client.get(f'/v1/model-configs/{cid}').json()['api_key_set']
            updated=client.put(f'/v1/model-configs/{cid}',json=PAYLOAD|{'revision':1,'api_key':'','model':'new-model'})
            assert updated.status_code==200
            assert client.get('/v1/test-console/config').json()['model']=='new-model'
            assert client.put(f'/v1/model-configs/{cid}',json=PAYLOAD|{'revision':1}).status_code==409
            invalid=client.post('/v1/model-configs',json=PAYLOAD|{'parameters':{'model':'override'}})
            assert invalid.status_code==422 and 'test-secret-key' not in invalid.text
    finally:
        app.dependency_overrides.clear()


def test_no_environment_fallback_when_database_missing(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','would-have-worked-before')
    monkeypatch.setenv('OPENAI_MODEL','wrong-model')
    monkeypatch.delenv('AI_SERVICE_API_KEY',raising=False)
    with TestClient(app) as client:
        response=client.post('/v1/scripts/extract-entities',json={'text':'剧本'})
    assert response.status_code==503
    assert response.json()['detail']['code']=='model_database_not_configured'
