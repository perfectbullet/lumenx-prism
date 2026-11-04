from unittest.mock import Mock
import pytest
from test_production import setup
from test_h3_reference import request_for
from lumenx_prism.h3_versions import update_task,apply_pending,versions
from lumenx_prism.model_configs import ConfigError
from lumenx_prism.production_worker import ProductionWorker


def new(setup):
 db,jobs,cfg,media,*_=setup;p=request_for(media,name='原输入')
 j=jobs.submit_new('video_generation',p,'versions-base',cfg,media)
 return j,p


def test_queued_update_retains_id_order_and_hold_and_is_idempotent(setup):
 db,jobs,cfg,media,*_=setup;j,p=new(setup);before=jobs.get(j['id'])
 db.ai_jobs.update_one({'_id':j['id']},{'$set':{'scheduling_paused':True}})
 out=update_task(jobs,j['id'],p.model_copy(update={'prompt':'修改后的提示词'}),'update-key',1,cfg,media)
 assert out['id']==j['id'] and out['created_at']==before['created_at']
 assert out['input_revision']==2 and out['request']['prompt']=='修改后的提示词' and out['status']=='queued'
 assert out['scheduling_paused'] is True
 again=update_task(jobs,j['id'],p.model_copy(update={'prompt':'修改后的提示词'}),'update-key',1,cfg,media)
 assert again['input_revision']==2
 v=versions(db,j['id']);assert len(v['items'])==2 and v['items'][1]['request']['prompt']==p.prompt
 with pytest.raises(ConfigError,match='input_revision_conflict'):update_task(jobs,j['id'],p,'stale',1,cfg,media)
 with pytest.raises(ConfigError,match='idempotency_conflict'):update_task(jobs,j['id'],p,'update-key',1,cfg,media)


def test_running_input_is_frozen_until_completion_and_old_result_is_retained(setup):
 db,jobs,cfg,media,*_=setup;j,p=new(setup)
 db.ai_jobs.update_one({'_id':j['id']},{'$set':{'status':'running','execution':{'state':'submitted','prompt_id':'old'}}})
 db.ai_resources.insert_one({'_id':'gpu0','job_id':j['id']})
 out=update_task(jobs,j['id'],p.model_copy(update={'prompt':'下一版'}),'pending',1,cfg,media)
 assert out['input_revision']==1 and out['pending_input_revision']==2 and out['request']['prompt']==p.prompt
 assert apply_pending(db,j['id']) is False
 db.ai_jobs.update_one({'_id':j['id']},{'$set':{'status':'succeeded','result':{'artifacts':[{'id':'old-video'}]},'execution_timing':{'duration_ms':10000}}})
 assert apply_pending(db,j['id']) is False
 db.ai_resources.delete_many({});assert apply_pending(db,j['id'])
 assert jobs.get(j['id'])['input_revision']==2 and jobs.get(j['id'])['status']=='queued'
 old=versions(db,j['id'])['items'][1]
 assert old['result']['artifacts'][0]['id']=='old-video' and old['execution_timing']['duration_ms']==10000
 assert 'execution_timing' not in jobs.get(j['id'])


def test_later_pending_update_supersedes_earlier_without_interruption(setup):
 db,jobs,cfg,media,*_=setup;j,p=new(setup)
 db.ai_jobs.update_one({'_id':j['id']},{'$set':{'status':'running'}})
 update_task(jobs,j['id'],p.model_copy(update={'prompt':'second'}),'second',1,cfg,media)
 update_task(jobs,j['id'],p.model_copy(update={'prompt':'third'}),'third',1,cfg,media)
 assert jobs.get(j['id'])['pending_input_revision']==3
 v=versions(db,j['id'])['items'];assert v[1]['state']=='superseded' and v[0]['pending']
 db.ai_jobs.update_one({'_id':j['id']},{'$set':{'status':'succeeded'}})
 assert apply_pending(db,j['id'])
 assert jobs.get(j['id'])['request']['prompt']=='third'


def test_unknown_timeout_is_recovered_before_new_input_can_run(setup):
 db,jobs,cfg,media,*_=setup;j,p=new(setup)
 db.ai_jobs.update_one({'_id':j['id']},{'$set':{'status':'failed','error_code':'comfy_execution_timeout','execution':{'state':'submitted','prompt_id':'old'}}})
 out=update_task(jobs,j['id'],p.model_copy(update={'prompt':'new'}),'after-timeout',1,cfg,media)
 assert out['phase']=='recovering' and out['input_revision']==1 and out['pending_input_revision']==2
 assert not apply_pending(db,j['id'])


def test_worker_saves_old_result_then_promotes_new_version(setup,monkeypatch):
 db,jobs,cfg,media,*_=setup;j,p=new(setup)
 # A pending update created while the original execution was in flight.
 db.ai_jobs.update_one({'_id':j['id']},{'$set':{'status':'running'}})
 update_task(jobs,j['id'],p.model_copy(update={'prompt':'next'}),'deferred',1,cfg,media)
 db.ai_jobs.update_one({'_id':j['id']},{'$set':{'status':'queued'}})
 adapter=Mock();adapter.idle.return_value=True;adapter.upload.return_value='ref.png';adapter.submit.return_value='prompt';adapter.history.return_value={'status':{'completed':True,'status_str':'success'},'outputs':{'92':{'videos':[{'filename':'video.mp4'}]}}};adapter.actual_model='h3';adapter.usage=None
 monkeypatch.setattr('lumenx_prism.production_worker.put_video',lambda *a,**kw:{'id':'new-video','has_audio':True})
 ProductionWorker(jobs,cfg,media,'version-worker',['video_generation'],adapter_factory=lambda _:adapter).run_once()
 assert jobs.get(j['id'])['input_revision']==2
 assert jobs.get(j['id'])['request']['prompt']=='next'
 assert versions(db,j['id'])['items'][0]['result']['artifacts'][0]['id']=='new-video'


def test_update_and_versions_http_require_auth_and_expected_revision(setup,monkeypatch):
 from fastapi.testclient import TestClient
 from lumenx_prism.api import app,get_jobs,get_model_configs,get_media
 db,jobs,cfg,media,*_=setup;j,p=new(setup)
 monkeypatch.setenv('AI_SERVICE_API_KEY','versions-token')
 app.dependency_overrides[get_jobs]=lambda:jobs;app.dependency_overrides[get_model_configs]=lambda:cfg;app.dependency_overrides[get_media]=lambda:media
 try:
  with TestClient(app) as c:
   url='/v1/videos/h3-ref2va-v1/jobs/'+j['id'];headers={'Idempotency-Key':'http-update','X-Input-Revision':'1'}
   assert c.post(url+'/update',json=p.model_dump(mode='json'),headers=headers).status_code==401
   c.headers['Authorization']='Bearer versions-token'
   response=c.post(url+'/update',json=p.model_dump(mode='json'),headers=headers)
   assert response.status_code==200,response.text
   assert response.json()['input_revision']==2 and response.json()['id']==j['id']
   assert c.get(url+'/versions').json()['current_version']==2
   assert c.post(url+'/update',json=p.model_dump(mode='json'),headers={'Idempotency-Key':'missing-revision'}).status_code==422
 finally:app.dependency_overrides.clear()
