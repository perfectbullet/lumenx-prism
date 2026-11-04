from uuid import uuid4
from datetime import timedelta
import mongomock
from fastapi.testclient import TestClient
from lumenx_prism.jobs import Jobs,utcnow
from lumenx_prism.api import app,get_jobs


def add(db,kind,state,name,**fields):
 id=str(uuid4());db.ai_jobs.insert_one({'_id':id,'type':kind,'status':state,'created_at':utcnow()+timedelta(seconds=db.ai_jobs.count_documents({})),'request':{'name':name,**fields},'effective_prompt':'private detail only','result':{'artifacts':[]}});return id


def test_history_filters_gpu_terminal_jobs_before_pagination():
 db=mongomock.MongoClient(tz_aware=True).db;jobs=Jobs(db)
 image=add(db,'image_generation','succeeded','完成图片',prompt_mode='raw')
 video=add(db,'video_generation','failed','失败视频')
 add(db,'image_generation','queued','队列图片');add(db,'video_generation','running','执行视频');add(db,'storyboard_generation','succeeded','文字任务')
 result=jobs.list(1,0,gpu_history=True)
 assert result['total']==2 and result['has_more'] is True and result['items'][0]['id']==video
 second=jobs.list(1,1,gpu_history=True)
 assert second['items'][0]['id']==image and second['has_more'] is False
 assert second['items'][0]['prompt_mode']=='raw'
 assert 'request' not in second['items'][0] and 'effective_prompt' not in second['items'][0]
 assert jobs.list(20,0,status='succeeded',job_type='image_generation',gpu_history=True)['total']==1
 assert jobs.list(20,0,name_query='失败',gpu_history=True)['total']==1
 assert jobs.list(20,0,name_query=video,gpu_history=True)['items'][0]['id']==video
 # Existing job listings still include live jobs and have no history-only restriction.
 assert jobs.list(20,0,job_type='image_generation')['total']==2


def test_history_search_matches_shot_and_escapes_regex():
 db=mongomock.MongoClient(tz_aware=True).db;jobs=Jobs(db)
 add(db,'video_generation','succeeded','',shot={'title':'穿过办公室'},board={'title':'第 2 场'},shot_id='S02')
 add(db,'image_generation','succeeded','字面.*样本')
 assert jobs.list(20,0,name_query='办公室',gpu_history=True)['total']==1
 assert jobs.list(20,0,name_query='S02',gpu_history=True)['total']==1
 assert jobs.list(20,0,name_query='.*',gpu_history=True)['total']==1


def test_history_http_auth_paging_and_validation(monkeypatch):
 db=mongomock.MongoClient(tz_aware=True).db;jobs=Jobs(db)
 id=add(db,'image_generation','succeeded','提示词图片',prompt_mode='raw')
 add(db,'video_generation','failed','视频');add(db,'image_generation','queued','live')
 monkeypatch.setenv('AI_SERVICE_API_KEY','history-token')
 app.dependency_overrides[get_jobs]=lambda:jobs
 try:
  with TestClient(app) as c:
   assert c.get('/v1/gpu/jobs/history').status_code==401
   c.headers['Authorization']='Bearer history-token'
   r=c.get('/v1/gpu/jobs/history?type=image_generation&status=succeeded&name_query=提示词&limit=1')
   assert r.status_code==200 and r.json()['total']==1 and r.json()['items'][0]['id']==id
   assert r.headers['cache-control']=='no-store'
   assert c.get('/v1/gpu/jobs/history?status=queued').status_code==422
   assert c.get('/v1/gpu/jobs/history?type=storyboard_generation').status_code==422
 finally:app.dependency_overrides.clear()
