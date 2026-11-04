from datetime import timedelta
from unittest.mock import Mock
import httpx
import pytest
from test_production import setup
from test_h3_reference import request_for
from lumenx_prism.jobs import utcnow,Jobs
from lumenx_prism.comfy_control import GPUControl
from lumenx_prism.production_worker import ProductionWorker
from lumenx_prism.comfy_videos import ComfyVideos
from lumenx_prism.model_configs import ConfigError
MIN_H3_FREE_BYTES=30*1024**3


def add_video(setup,key):
 db,jobs,cfg,media,*_=setup
 return jobs.submit_new('video_generation',request_for(media,name=key),key,cfg,media)


def test_memory_shortage_defers_same_job_and_blocks_younger_same_gpu(setup):
 db,jobs,cfg,media,*_=setup
 first=add_video(setup,'first');second=add_video(setup,'second')
 db.ai_jobs.update_one({'_id':first['id']},{'$set':{'created_at':utcnow()-timedelta(minutes=1)}})
 adapter=Mock();adapter.idle.side_effect=ConfigError('video_gpu_memory_not_released',503)
 adapter.memory_status={'required_bytes':MIN_H3_FREE_BYTES,'available_bytes':29*1024**3}
 worker=ProductionWorker(jobs,cfg,media,'memory-worker',['video_generation'],adapter_factory=lambda _:adapter)
 assert worker.run_once()
 row=jobs.get(first['id']);assert row['status']=='queued' and row['phase']=='waiting_for_memory'
 assert row['waiting_reason']=='insufficient_vram' and row['memory_wait']['check_interval_seconds']==30
 assert row['memory_wait']['required_bytes']==30*1024**3
 assert jobs.claim_production('other',['video_generation'],60) is None
 assert db.ai_resources.count_documents({})==0
 adapter.submit.assert_not_called()
 # The same head is eligible again only when its persisted check time is due.
 db.ai_jobs.update_one({'_id':first['id']},{'$set':{'not_before':utcnow()-timedelta(seconds=1)}})
 head=jobs.claim_production('other',['video_generation'],60)
 assert head['_id']==first['id'] and jobs.get(second['id'])['status']=='queued'


def test_memory_becomes_available_then_original_id_runs(setup,monkeypatch):
 db,jobs,cfg,media,*_=setup;first=add_video(setup,'resume-memory')
 db.ai_jobs.update_one({'_id':first['id']},{'$set':{'phase':'waiting_for_memory','waiting_reason':'insufficient_vram','not_before':utcnow()-timedelta(seconds=1)}})
 adapter=Mock();adapter.idle.return_value=True;adapter.upload.return_value='ref.png';adapter.submit.return_value='new-prompt';adapter.history.return_value={'status':{'completed':True,'status_str':'success'},'outputs':{'92':{'videos':[{'filename':'video.mp4'}]}}};adapter.actual_model='h3';adapter.usage=None
 monkeypatch.setattr('lumenx_prism.production_worker.put_video',lambda *a,**kw:{'id':'video','has_audio':True})
 ProductionWorker(jobs,cfg,media,'memory-worker',['video_generation'],adapter_factory=lambda _:adapter).run_once()
 assert jobs.get(first['id'])['status']=='succeeded' and jobs.get(first['id'])['waiting_reason'] is None
 adapter.submit.assert_called_once()


def test_paused_memory_head_is_not_resumed_and_does_not_block_next_job(setup):
 db,jobs,cfg,media,*_=setup
 first=add_video(setup,'held');second=add_video(setup,'next')
 db.ai_jobs.update_one({'_id':first['id']},{'$set':{'created_at':utcnow()-timedelta(minutes=1),'phase':'waiting_for_memory','waiting_reason':'insufficient_vram','not_before':utcnow()-timedelta(seconds=1)}})
 gpu=GPUControl(db);gpu.job_pause(first['id'],True)
 gpu.pause();assert jobs.claim_production('worker',['video_generation'],60) is None
 gpu.resume();claimed=jobs.claim_production('worker',['video_generation'],60)
 assert claimed['_id']==second['id'] and jobs.get(first['id'])['scheduling_paused'] is True
 assert jobs.list(20,0,job_type='video_generation',scheduling_paused=True)['items'][0]['id']==first['id']
 assert jobs.list(20,0,job_type='video_generation',scheduling_paused=False)['total']==1


def test_other_gpu_group_is_not_blocked_by_delayed_head(setup):
 db,jobs,cfg,media,*_=setup
 first=add_video(setup,'delayed-group');second=add_video(setup,'different-group')
 db.ai_jobs.update_one({'_id':first['id']},{'$set':{'created_at':utcnow()-timedelta(minutes=1),'not_before':utcnow()+timedelta(seconds=30)}})
 db.ai_jobs.update_one({'_id':second['id']},{'$set':{'model_config.parameters.resource_group':'gpu1'}})
 assert jobs.claim_production('worker',['video_generation'],60)['_id']==second['id']


def test_h3_requires_configured_30_gib_threshold(monkeypatch):
 monkeypatch.setattr('lumenx_prism.comfy_images.ComfyImages.idle',lambda _:True)
 available=[MIN_H3_FREE_BYTES-1]
 def handle(req):
  if req.url.path=='/free':return httpx.Response(200)
  return httpx.Response(200,json={'devices':[{'type':'cuda','vram_free':available[0]}]})
 adapter=ComfyVideos({'base_url':'http://test','parameters':{'resource_group':'gpu0','min_free_vram_mb':30720}})
 adapter.client.close();adapter.client=httpx.Client(base_url='http://test',transport=httpx.MockTransport(handle))
 try:
  with pytest.raises(ConfigError,match='video_gpu_memory_not_released'):adapter.idle()
  assert adapter.memory_status['required_bytes']==MIN_H3_FREE_BYTES
  available[0]=MIN_H3_FREE_BYTES;assert adapter.idle()
 finally:adapter.close()


def test_pause_during_preparation_prevents_prompt_submission(setup):
 db,jobs,cfg,media,*_=setup;first=add_video(setup,'pause-before-submit')
 adapter=Mock();adapter.idle.return_value=True
 def upload(*a):GPUControl(db).pause();return 'ref.png'
 adapter.upload.side_effect=upload
 ProductionWorker(jobs,cfg,media,'memory-worker',['video_generation'],adapter_factory=lambda _:adapter).run_once()
 assert jobs.get(first['id'])['status']=='queued'
 adapter.submit.assert_not_called()


def test_existing_expired_gpu_owner_is_recovered_before_new_fifo_work(setup):
 db,jobs,cfg,media,*_=setup
 older=add_video(setup,'older-wait');owner=add_video(setup,'existing-inference')
 db.ai_jobs.update_one({'_id':older['id']},{'$set':{'created_at':utcnow()-timedelta(minutes=10)}})
 db.ai_jobs.update_one({'_id':owner['id']},{'$set':{'status':'running','lease_until':utcnow()-timedelta(seconds=1)}})
 db.ai_resources.insert_one({'_id':'gpu0','job_id':owner['id']})
 assert jobs.claim_production('replacement',['video_generation'],60)['_id']==owner['id']


def test_paused_filter_http_keeps_held_and_plain_queued_separate(setup,monkeypatch):
 from fastapi.testclient import TestClient
 from lumenx_prism.api import app,get_jobs
 db,jobs,cfg,media,*_=setup
 held=add_video(setup,'held-http');waiting=add_video(setup,'waiting-http')
 GPUControl(db).job_pause(held['id'],True)
 monkeypatch.setenv('AI_SERVICE_API_KEY','memory-token');app.dependency_overrides[get_jobs]=lambda:jobs
 try:
  with TestClient(app) as c:
   c.headers['Authorization']='Bearer memory-token'
   a=c.get('/v1/jobs?type=video_generation&status=queued&scheduling_paused=true').json()
   b=c.get('/v1/jobs?type=video_generation&status=queued&scheduling_paused=false').json()
   assert a['total']==1 and a['items'][0]['id']==held['id']
   assert b['total']==1 and b['items'][0]['id']==waiting['id']
 finally:app.dependency_overrides.clear()
