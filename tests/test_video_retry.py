from unittest.mock import Mock
from datetime import timedelta
import pytest
from test_production import setup
from test_h3_reference import request_for
from lumenx_prism.model_configs import ConfigError
from lumenx_prism.production_worker import ProductionWorker
from lumenx_prism.jobs import utcnow


def failed(setup,execution=None):
 db,jobs,cfg,media,*_=setup
 j=jobs.submit_new('video_generation',request_for(media),'retry-test',cfg,media)
 db.ai_jobs.update_one({'_id':j['id']},{'$set':{'status':'failed','error_code':'comfy_execution_timeout','attempt':1,'execution':execution}})
 return jobs.get(j['id'])


def test_retry_keeps_id_created_input_order_and_archives_failure(setup):
 db,jobs,cfg,media,*_=setup;j=failed(setup)
 out=jobs.retry_video(j['id'],'retry-key',cfg,media)
 assert out['id']==j['id'] and out['created_at']==j['created_at'] and out['request']==j['request']
 assert out['status']=='queued' and out['retry_mode']=='rerun'
 row=db.ai_jobs.find_one({'_id':j['id']});assert row['retry_events'][0]['error_code']=='comfy_execution_timeout'
 assert row['retry_submission_id'].startswith(j['id']+':retry:')
 assert jobs.retry_video(j['id'],'retry-key',cfg,media)['retry_count']==1
 db.ai_jobs.update_one({'_id':j['id']},{'$set':{'status':'failed','attempt':2}})
 assert jobs.retry_video(j['id'],'retry-key',cfg,media)['retry_count']==1
 assert jobs.retry_video(j['id'],'different-key',cfg,media)['retry_count']==2


def test_timeout_retries_original_upstream_without_resubmit(setup):
 db,jobs,cfg,media,*_=setup
 ex={'state':'submitted','prompt_id':'original','submission_id':'old','workflow':{}}
 j=failed(setup,ex);adapter=Mock();adapter.history.return_value={'status':{'completed':True,'status_str':'success'}}
 out=jobs.retry_video(j['id'],'recover-key',cfg,media,adapter_factory=lambda _:adapter)
 assert out['retry_mode']=='recover'
 assert db.ai_jobs.find_one({'_id':j['id']})['execution']['prompt_id']=='original'
 adapter.submit.assert_not_called()


def test_confirmed_upstream_error_gets_new_submission_token(setup):
 db,jobs,cfg,media,*_=setup;j=failed(setup,{'state':'submitted','prompt_id':'old','submission_id':'old'})
 adapter=Mock();adapter.history.return_value={'status':{'completed':True,'status_str':'error'}}
 out=jobs.retry_video(j['id'],'rerun-key',cfg,media,adapter_factory=lambda _:adapter)
 assert out['retry_mode']=='rerun'
 assert db.ai_jobs.find_one({'_id':j['id']})['execution'] is None


def test_uncertain_submission_must_not_clear_fence(setup):
 db,jobs,cfg,media,*_=setup;j=failed(setup,{'state':'submitting','submission_id':'uncertain'})
 adapter=Mock();adapter.locate.return_value=None
 with pytest.raises(ConfigError,match='previous_video_outcome_unknown'):jobs.retry_video(j['id'],'unknown',cfg,media,adapter_factory=lambda _:adapter)
 assert jobs.get(j['id'])['status']=='failed'
 assert 'retry_events' not in db.ai_jobs.find_one({'_id':j['id']})


def test_successful_job_is_not_overwritten(setup):
 db,jobs,cfg,media,*_=setup;j=failed(setup)
 db.ai_jobs.update_one({'_id':j['id']},{'$set':{'status':'succeeded'}})
 with pytest.raises(ConfigError,match='only_failed'):jobs.retry_video(j['id'],'bad',cfg,media)


def test_fresh_worker_retry_uses_new_upstream_token(setup,monkeypatch):
 db,jobs,cfg,media,*_=setup;j=failed(setup)
 jobs.retry_video(j['id'],'new',cfg,media)
 adapter=Mock();adapter.idle.return_value=True;adapter.upload.return_value='reference.png';adapter.submit.return_value='new-prompt';adapter.history.return_value={'status':{'completed':True,'status_str':'success'},'outputs':{'92':{'videos':[{'filename':'video.mp4'}]}}};adapter.actual_model='h3';adapter.usage=None
 monkeypatch.setattr('lumenx_prism.production_worker.put_video',lambda *a,**kw:{'id':'video','has_audio':True})
 ProductionWorker(jobs,cfg,media,'retry-worker',['video_generation'],adapter_factory=lambda _:adapter).run_once()
 assert jobs.get(j['id'])['status']=='succeeded'
 assert adapter.submit.call_args.args[1].startswith(j['id']+':retry:')
 assert jobs.get(j['id'])['created_at']==j['created_at']


def test_retry_http_is_authenticated_and_idempotent(setup,monkeypatch):
 from fastapi.testclient import TestClient
 from lumenx_prism.api import app,get_jobs,get_model_configs,get_media
 db,jobs,cfg,media,*_=setup;j=failed(setup)
 monkeypatch.setenv('AI_SERVICE_API_KEY','retry-secret')
 app.dependency_overrides[get_jobs]=lambda:jobs
 app.dependency_overrides[get_model_configs]=lambda:cfg
 app.dependency_overrides[get_media]=lambda:media
 try:
  with TestClient(app) as c:
   url='/v1/jobs/'+j['id']+'/retry-video';h={'Idempotency-Key':'retry-http'}
   assert c.post(url,headers=h).status_code==401
   c.headers['Authorization']='Bearer retry-secret'
   a=c.post(url,headers=h);assert a.status_code==200,a.text
   assert a.json()['id']==j['id'] and a.json()['retry_count']==1
   assert c.post(url,headers=h).json()['retry_count']==1
 finally:app.dependency_overrides.clear()


def test_terminal_timed_out_lock_is_recovered_without_duplicate_inference(setup,monkeypatch):
 db,jobs,cfg,media,*_=setup
 j=failed(setup,{'state':'submitted','prompt_id':'existing','submission_id':'existing'})
 db.ai_resources.insert_one({'_id':'gpu0','job_id':j['id']})
 adapter=Mock();adapter.history.return_value={'status':{'completed':True,'status_str':'success'},'outputs':{'92':{'videos':[{'filename':'video.mp4'}]}}};adapter.actual_model='h3';adapter.usage=None
 monkeypatch.setattr('lumenx_prism.production_worker.put_video',lambda *a,**kw:{'id':'video','has_audio':True})
 worker=ProductionWorker(jobs,cfg,media,'recover',['video_generation'],adapter_factory=lambda _:adapter)
 assert worker.run_once()
 assert jobs.get(j['id'])['status']=='succeeded'
 assert db.ai_resources.count_documents({})==0
 adapter.submit.assert_not_called()


def test_still_running_upstream_keeps_fence(setup):
 db,jobs,cfg,media,*_=setup
 j=failed(setup,{'state':'submitted','prompt_id':'existing','submission_id':'existing'})
 db.ai_resources.insert_one({'_id':'gpu0','job_id':j['id']})
 adapter=Mock();adapter.history.return_value=None
 worker=ProductionWorker(jobs,cfg,media,'recover',['video_generation'],adapter_factory=lambda _:adapter)
 worker.reconcile_video_fence()
 assert jobs.get(j['id'])['status']=='failed'
 assert db.ai_resources.count_documents({})==1
 adapter.submit.assert_not_called()
