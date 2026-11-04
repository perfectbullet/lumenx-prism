import json
from uuid import uuid4
import httpx
import mongomock
import pytest
from fastapi.testclient import TestClient
from lumenx_prism.comfy_control import GPUControl,ComfyControl
from lumenx_prism.image_jobs import ImageJobs
from lumenx_prism.production_jobs import ProductionJobs
from lumenx_prism.jobs import utcnow
from lumenx_prism.model_configs import ConfigError

@pytest.fixture
def setup():
    db=mongomock.MongoClient(tz_aware=True).db
    image,video=str(uuid4()),str(uuid4())
    for id,kind,url in [(image,'image','http://qwen'),(video,'video','http://h3')]:
        db.model_comfigs.insert_one({'_id':id,'record_type':'model','name':kind,'kind':kind,'provider':'comfyui',
            'protocol':'custom_http','base_url':url,'api_key':'private-test-key','parameters':{'resource_group':'gpu0','peer_base_urls':['http://h3' if kind=='image' else 'http://qwen']}})
    state={'queue_running':[],'queue_pending':[]};calls=[]
    def handler(r):
        calls.append(r)
        if r.url.path=='/queue':return httpx.Response(200,json=state)
        if r.url.path=='/system_stats':return httpx.Response(200,json={'devices':[{'name':'GPU','type':'cuda','vram_total':32*1024**3,'vram_free':30*1024**3}]})
        if r.url.path=='/free':return httpx.Response(200,content=b'')
        raise AssertionError('Unexpected operation '+str(r.url))
    factory=lambda **kw:httpx.Client(transport=httpx.MockTransport(handler),**kw)
    return db,GPUControl(db),ComfyControl(db,factory),state,calls,image,video

def row(db,kind='image_generation',status='queued',**values):
    id=str(uuid4());db.ai_jobs.insert_one({'_id':id,'type':kind,'status':status,'phase':'queued','created_at':utcnow(),**values});return id

def test_pause_persists_and_global_resume_preserves_individual_holds(setup):
    db,gpu,*_=setup;id=row(db);gpu.job_pause(id,True);gpu.pause()
    assert GPUControl(db).paused()
    assert gpu.state()['queued']==1 and gpu.state()['individually_paused']==1
    gpu.resume();assert not gpu.paused()
    assert db.ai_jobs.find_one({'_id':id})['scheduling_paused']

def test_paused_gpu_does_not_claim_but_text_and_cpu_continue(setup):
    db,gpu,*_=setup
    row(db);row(db,'video_generation');text=row(db,'script_planning');assembly=row(db,'video_assembly')
    gpu.pause();assert ImageJobs(db).claim_image('worker',60) is None
    repo=ProductionJobs(db)
    assert repo.claim_production('worker',['video_generation'],60) is None
    assert repo.claim_production('text',['script_planning','video_generation'],60)['_id']==text
    assert repo.claim_production('cpu',['video_assembly'],60)['_id']==assembly

def test_individual_hold_is_excluded_and_resume_returns_same_job(setup):
    db,gpu,*_=setup;id=row(db);gpu.job_pause(id,True)
    assert ImageJobs(db).claim_image('worker',60) is None
    gpu.job_pause(id,False)
    claimed=ImageJobs(db).claim_image('worker',60);assert claimed['_id']==id
    with pytest.raises(ConfigError,match='only_queued'):gpu.job_pause(id,True)
    assert db.ai_jobs.find_one({'_id':id})['status']=='running'

def test_individually_paused_video_not_claimed(setup):
    db,gpu,*_=setup;id=row(db,'video_generation');gpu.job_pause(id,True)
    assert ProductionJobs(db).claim_production('worker',['video_generation'],60) is None

def test_text_and_terminal_jobs_cannot_be_held(setup):
    db,gpu,*_=setup
    for kind,status in [('image_generation','succeeded'),('video_generation','running'),('entity_extraction','queued')]:
        with pytest.raises(ConfigError):gpu.job_pause(row(db,kind,status),True)

def test_pause_between_claim_and_acquire_defers_without_gpu_lock(setup):
    db,gpu,*_=setup;id=row(db);job=ImageJobs(db).claim_image('worker',60);gpu.pause()
    assert not ImageJobs(db).acquire(job,'gpu0')
    assert db.ai_resources.count_documents({})==0

def test_free_requires_global_pause(setup):
    db,gpu,comfy,_,calls,*_=setup
    with pytest.raises(ConfigError,match='pause_gpu'):comfy.free()
    assert calls==[]

def test_running_job_blocks_free_even_before_resource_acquisition(setup):
    db,gpu,comfy,_,calls,*_=setup;gpu.pause();row(db,'video_generation','running')
    assert gpu.state()['mode']=='draining'
    with pytest.raises(ConfigError,match='gpu_jobs_still_running'):comfy.free()
    assert calls==[] and not gpu.state()['maintenance']

@pytest.mark.parametrize('key',['queue_running','queue_pending'])
def test_busy_comfy_queue_does_not_receive_any_unload(setup,key):
    db,gpu,comfy,state,calls,*_=setup;gpu.pause();state[key]=[[1]]
    with pytest.raises(ConfigError,match='comfy_queue_not_idle'):comfy.free()
    assert not [r for r in calls if r.method=='POST']
    assert db.ai_resources.count_documents({})==0 and gpu.paused()

def test_unknown_resource_owner_preserved_and_unload_refused(setup):
    db,gpu,comfy,_,calls,*_=setup;gpu.pause();db.ai_resources.insert_one({'_id':'gpu0','job_id':'unknown-submission'})
    with pytest.raises(ConfigError,match='gpu_resource_busy'):comfy.free()
    assert db.ai_resources.find_one({'_id':'gpu0'})['job_id']=='unknown-submission'
    assert not calls

def test_empty_200_free_is_accepted_and_queue_stays_paused(setup):
    db,gpu,comfy,_,calls,image,video=setup;gpu.pause()
    result=comfy.free();assert result['status']=='requested' and set(result['instances'])=={image,video}
    posts=[r for r in calls if r.method=='POST'];assert len(posts)==2
    assert all(r.url.path=='/free' and json.loads(r.content)=={'unload_models':True,'free_memory':True} for r in posts)
    assert db.ai_resources.count_documents({})==0 and gpu.paused() and not gpu.state()['maintenance']

def test_target_only_free_checks_target_process(setup):
    db,gpu,comfy,_,calls,image,video=setup;gpu.pause();comfy.free(image)
    assert {str(r.url.host) for r in calls if r.url.path=='/queue'}=={'qwen'}
    assert [r.url.host for r in calls if r.method=='POST']==['qwen']

def test_maintenance_and_resume_are_mutually_exclusive(setup):
    db,gpu,comfy,_,calls,*_=setup;gpu.pause()
    original=comfy.request
    def request(item,method,path,**kw):
        if path=='/free':
            with pytest.raises(ConfigError,match='maintenance'):gpu.resume()
            assert db.ai_resources.count_documents({})==1
        return original(item,method,path,**kw)
    comfy.request=request;comfy.free();assert gpu.paused()

def test_probe_error_does_not_unload_or_leave_maintenance_lock(setup):
    db,gpu,comfy,state,calls,*_=setup;gpu.pause();state.clear()
    with pytest.raises(ConfigError,match='comfy_queue_invalid'):comfy.free()
    assert not [r for r in calls if r.method=='POST']
    assert not gpu.state()['maintenance'] and db.ai_resources.count_documents({})==0

def test_status_never_exposes_model_keys(setup):
    db,gpu,comfy,*_=setup
    result=comfy.status();assert len(result['instances'])==2
    assert all(r['online'] for r in result['instances'])
    assert 'private-test-key' not in json.dumps(result,default=str)
    assert result['instances'][0]['devices'][0]['vram_free']==30*1024**3

def test_http_pause_and_resume_contract_and_auth(setup,monkeypatch):
    from lumenx_prism.api import app,get_jobs
    db,gpu,*_=setup;repo=ImageJobs(db);id=row(db)
    app.dependency_overrides[get_jobs]=lambda:repo
    try:
        monkeypatch.setenv('PRISM_SERVICE_AUTH_ENABLED','false')
        with TestClient(app) as c:
            assert c.get('/tools/comfyui').status_code==200
            assert c.post('/v1/gpu/pause').json()['paused']
            assert c.post('/v1/jobs/'+id+'/pause').json()['scheduling_paused']
            assert repo.get(id)['scheduling_paused']
            assert c.post('/v1/gpu/resume').json()['paused'] is False
            assert c.post('/v1/jobs/'+id+'/resume').json()['scheduling_paused'] is False
            monkeypatch.setenv('PRISM_SERVICE_AUTH_ENABLED','true');monkeypatch.setenv('AI_SERVICE_API_KEY','test-service-key')
            assert c.post('/v1/gpu/pause').status_code==401
    finally:app.dependency_overrides.clear()


def test_native_comfy_queue_counts_do_not_include_prism_backlog(setup):
 db,gpu,comfy,state,*_=setup
 for _ in range(17):row(db,'video_generation')
 state['queue_running']=[[1,'external-or-prism-prompt']]
 state['queue_pending']=[[2,'external-prompt'],[3,'external-prompt']]
 result=comfy.status()
 assert result['gpu']['queued']==17
 # Both configured instances report their own queue, independently of Prism.
 assert all({k:i['queue'][k] for k in ('running','pending','total')}=={'running':1,'pending':2,'total':3} for i in result['instances'])
 assert result['comfy_queue']['total']==6
 assert result['comfy_queue']['available_instances']==2

def test_queue_counts_remain_available_when_only_memory_probe_fails(setup):
 db,gpu,comfy,state,*_=setup
 state['queue_running']=[[1]];original=comfy.request
 def request(item,method,path,**kwargs):
  if path=='/system_stats':raise RuntimeError('unreachable')
  return original(item,method,path,**kwargs)
 comfy.request=request;result=comfy.status()
 assert all(not i['online'] and i['queue_readable'] and not i['stats_readable'] for i in result['instances'])
 assert result['comfy_queue']['running']==2

def test_unreadable_native_queue_is_unknown_not_zero(setup):
 db,gpu,comfy,state,*_=setup
 state.clear();result=comfy.status()
 assert all(not i['queue_readable'] and 'queue' not in i for i in result['instances'])
 assert result['comfy_queue']['unavailable_instances']==2
 assert result['comfy_queue']['available_instances']==0

def test_gpu_job_list_exposes_readable_video_scope_without_raw_request(setup):
 db,gpu,*_=setup
 id=row(db,'video_generation',request={'shot_id':'S02','shot':{'title':'检查显存'},'board':{'title':'代码之外 · C02 关掉窗口','scene_context':{'scene_id':'C02','work_context':{'work_id':'work','edition_id':'edition','episode_id':'E02'}}}})
 item=ProductionJobs(db).list(10,0,job_type='video_generation')['items'][0]
 assert item['id']==id and item['display_title']=='检查显存'
 assert item['shot_id']=='S02' and item['scene_id']=='C02'
 assert item['source_context']['episode_id']=='E02' and 'request' not in item


def test_status_includes_pid_memory_independently_of_global_device_stats(setup,monkeypatch):
    _,_,control,_,_,image,_=setup
    monkeypatch.setattr('lumenx_prism.gpu_processes.collect',lambda:{'available':True,'processes':[]})
    monkeypatch.setattr('lumenx_prism.gpu_processes.memory_for',lambda url,sample:{'available':True,'source':'nvidia-smi','pid':101,'vram_used':808*1024**2,'gpu_count':1})
    status=control.status();row=next(i for i in status['instances'] if i['id']==image)
    assert row['process_memory']['vram_used']==808*1024**2
    assert row['process_memory']['pid']==101
    assert row['devices'][0]['vram_total']-row['devices'][0]['vram_free']==2*1024**3


def test_native_queue_ownership_uses_prompt_id_and_preserves_external_tasks(setup):
    db,_,control,state,_,_,_=setup
    id=row(db,'video_generation',request={'name':'Prism 视频'},execution={'prompt_id':'our-prompt','submission_id':'our-token'})
    state['queue_running']=[[0,'our-prompt',{'private':'prompt must not leak'},{},[]]]
    state['queue_pending']=[[1,'their-prompt',{}, {'client_id':'other-program'},[]],[2,'spoofed',{}, {'prism_submission_id':'made-up'},[]]]
    result=control.status();tasks=result['instances'][0]['queue']['tasks']
    assert len(tasks)==3
    assert tasks[0]['origin']=='prism' and tasks[0]['prism_job_id']==id
    assert tasks[1]['origin']=='external' and tasks[2]['origin']=='unknown'
    assert 'private' not in json.dumps(tasks)


def test_ownership_finds_submitting_image_by_verified_token(setup):
    db,_,control,*_=setup
    id=row(db,'image_generation',request={'name':'图片'},executions=[{'state':'submitting','submission_id':'placeholder'}])
    token=id+':0';db.ai_jobs.update_one({'_id':id},{'$set':{'executions':[{'state':'submitting','submission_id':token}]}})
    task=control.identify_task([1,'new-prompt',{}, {'prism_submission_id':token}], 'running')
    assert task['origin']=='prism' and task['prism_job_id']==id
    fake=control.identify_task([1,'fake-prompt',{}, {'prism_submission_id':id+':99'}],'pending')
    assert fake['origin']=='unknown'


def test_offline_other_instance_does_not_block_idle_target_release(setup):
    _,gpu,comfy,_,calls,image,video=setup
    gpu.pause();original=comfy.request
    def request(item,method,path,**kwargs):
        if item['url']=='http://qwen':raise httpx.ConnectError('offline')
        return original(item,method,path,**kwargs)
    comfy.request=request
    assert comfy.free(video)['instances']==[video]
    assert [r.url.host for r in calls if r.method=='POST']==['h3']
    with pytest.raises(ConfigError,match='comfy_cache_release_unavailable'):comfy.free()


def test_busy_target_still_blocks_single_instance_release(setup):
    _,gpu,comfy,state,calls,_,video=setup
    gpu.pause();state['queue_running']=[[1,'external-task']]
    with pytest.raises(ConfigError,match='comfy_queue_not_idle'):comfy.free(video)
    assert not [r for r in calls if r.method=='POST']
