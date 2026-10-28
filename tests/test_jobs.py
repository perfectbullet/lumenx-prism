from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from unittest.mock import Mock

import mongomock
import pytest
from fastapi.testclient import TestClient

from lumenx_prism.api import app, get_jobs, get_model_configs
from lumenx_prism.jobs import Jobs, utcnow
from lumenx_prism.model_configs import ModelConfigs, ModelConfigInput, ConfigError
from lumenx_prism.schemas import EntityExtractionRequest
from lumenx_prism.worker import Worker


@pytest.fixture
def setup_queue():
    db = mongomock.MongoClient(tz_aware=True)['lumenx_prism']
    jobs, configs = Jobs(db), ModelConfigs(db)
    config = configs.save(ModelConfigInput(name='测试', kind='text', provider='mock',
        protocol='openai_chat', model='test', base_url='https://example.invalid/v1', api_key='SECRET'))
    configs.bind_entity_model(config['id'])
    request = EntityExtractionRequest(text='林岚站在车站。')
    adapter = Mock(_client=None, actual_model='test', usage={'total_tokens': 3})
    adapter.chat.return_value = '{"characters":[],"scenes":[],"props":[]}'
    factory = Mock(return_value=adapter)
    return jobs, configs, request, factory


def submit(setup_queue, key='one'):
    jobs, configs, request, _ = setup_queue
    return jobs.submit(request, key, configs.resolve_entity_model())


def test_concurrent_idempotency_and_claim(setup_queue):
    jobs, _, request, _ = setup_queue
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda _: submit(setup_queue), range(16)))
    assert len({row['id'] for row in rows}) == 1
    with pytest.raises(ConfigError, match='idempotency_conflict'):
        jobs.existing('one', EntityExtractionRequest(text='不同输入'))
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda i: jobs.claim(str(i)), range(8)))
    assert sum(row is not None for row in claims) == 1
    assert 'SECRET' not in json.dumps(rows, default=str)


def test_worker_result_and_restart_without_duplicate_call(setup_queue):
    jobs, configs, _, factory = setup_queue
    row = submit(setup_queue)
    # API lifetime is irrelevant: a new repository/consumer reads the durable queue.
    new_jobs = Jobs(jobs.collection.database)
    assert Worker(new_jobs, configs, factory).run_once()
    result = new_jobs.get(row['id'])
    assert result['status'] == 'succeeded' and result['usage']['total_tokens'] == 3
    assert not Worker(new_jobs, configs, factory).run_once()
    factory.return_value.chat.assert_called_once()
    assert jobs.attempts.find_one()['status'] == 'succeeded'


def test_interrupted_lease_is_not_replayed_and_late_result_fenced(setup_queue):
    jobs, configs, _, factory = setup_queue
    row = submit(setup_queue)
    old = jobs.claim('killed-worker')
    jobs.collection.update_one({'_id': row['id']}, {'$set': {'lease_until': utcnow()-timedelta(seconds=1)}})
    assert not jobs.heartbeat(old)
    assert not jobs.finish(old, result={})
    assert not Worker(jobs, configs, factory).run_once()
    assert jobs.get(row['id'])['error_code'] == 'execution_outcome_unknown'
    assert jobs.attempts.find_one()['status'] == 'failed'
    factory.assert_not_called()


def test_heartbeat_extends_lease_and_foreign_token_cannot_finish(setup_queue):
    jobs, _, _, _ = setup_queue
    submit(setup_queue)
    job = jobs.claim('owner', 1)
    assert jobs.heartbeat(job, 60)
    assert jobs.collection.find_one()['lease_until'] > job['lease_until']
    assert not jobs.finish(job | {'claim_token': 'intruder'}, result={})


@pytest.mark.parametrize('failure,code', [('bad_json','invalid_model_output'),('network','upstream_error')])
def test_failed_inference_is_durable_and_not_retried(setup_queue, failure, code):
    jobs, configs, _, factory = setup_queue
    row = submit(setup_queue)
    if failure == 'bad_json':
        factory.return_value.chat.return_value = 'bad JSON SECRET'
    else:
        factory.return_value.chat.side_effect = RuntimeError('SECRET')
    worker = Worker(jobs, configs, factory)
    assert worker.run_once()
    assert not worker.run_once()
    record = jobs.get(row['id'])
    assert record['status'] == 'failed' and record['error_code'] == code
    assert 'SECRET' not in json.dumps(record, default=str)
    factory.return_value.chat.assert_called_once()


def test_model_revision_change_fails_before_inference(setup_queue):
    jobs, configs, _, factory = setup_queue
    row = submit(setup_queue)
    configs.collection.update_one({'_id': row['model_config']['id']}, {'$inc': {'revision': 1}})
    Worker(jobs, configs, factory).run_once()
    assert jobs.get(row['id'])['error_code'] == 'model_config_changed'
    factory.assert_not_called()


def test_result_write_retry_does_not_repeat_inference(setup_queue, monkeypatch):
    jobs, configs, _, factory = setup_queue
    row = submit(setup_queue)
    finish = jobs.finish
    wrapper = Mock(side_effect=[RuntimeError('db down'), True])
    def retry(*args, **kwargs):
        wrapper()
        return finish(*args, **kwargs)
    monkeypatch.setattr(jobs, 'finish', retry)
    Worker(jobs, configs, factory).run_once()
    assert jobs.get(row['id'])['status'] == 'succeeded'
    factory.return_value.chat.assert_called_once()


def test_async_http_submission_auth_pagination_and_retry(setup_queue, monkeypatch):
    jobs, configs, _, factory = setup_queue
    monkeypatch.setenv('AI_SERVICE_API_KEY', 'service-token')
    app.dependency_overrides[get_jobs] = lambda: jobs
    app.dependency_overrides[get_model_configs] = lambda: configs
    try:
        with TestClient(app) as client:
            path = '/v1/scripts/extract-entities/jobs'
            assert client.post(path, json={'text':'剧本'}).status_code == 401
            client.headers['Authorization'] = 'Bearer service-token'
            assert client.post(path, json={'text':'剧本'}).status_code == 422
            client.headers['Idempotency-Key'] = 'same-request'
            response = client.post(path, json={'text':'剧本'})
            assert response.status_code == 202
            location = response.headers['location']
            assert client.get(location).json()['status'] == 'queued'
            configs.collection.delete_one({'_id':'bindings'})
            assert client.post(path, json={'text':'剧本'}).json()['id'] == response.json()['id']
            assert client.post(path, json={'text':'不同剧本'}).status_code == 409
            assert client.get('/v1/jobs?status=queued').json()['items'][0]['id'] == response.json()['id']
            assert client.get('/v1/jobs?status=other').status_code == 422
            Worker(jobs, configs, factory).run_once()
            assert client.get(location).json()['status'] == 'succeeded'
    finally:
        app.dependency_overrides.clear()
