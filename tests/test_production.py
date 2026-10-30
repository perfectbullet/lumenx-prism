from datetime import timedelta
import io,json
from unittest.mock import Mock
from uuid import uuid4
import mongomock
import pytest
from PIL import Image
from pydantic import ValidationError
from lumenx_prism.production_schemas import BoardInput,BoardContent,DraftInput,VideoInput,AssemblyInput
from lumenx_prism.storyboards import Drafts,parse_board,compile_video
from lumenx_prism.production_jobs import ProductionJobs
from lumenx_prism.production_worker import ProductionWorker
from lumenx_prism.model_configs import ModelConfigs,ModelConfigInput,ConfigError
from lumenx_prism.media import MediaStore
from lumenx_prism.jobs import utcnow

@pytest.fixture
def setup(tmp_path):
    db=mongomock.MongoClient(tz_aware=True)['lumenx_prism'];media=MediaStore(db,tmp_path)
    f=io.BytesIO();Image.new('RGB',(64,64),'green').save(f,format='PNG');a=media.put(f.getvalue())
    configs=ModelConfigs(db)
    text=configs.save(ModelConfigInput(name='text',kind='text',provider='mock',protocol='openai_chat',model='test',base_url='http://test',api_key='secret'))
    video=configs.save(ModelConfigInput(name='H3',kind='video',provider='comfyui',protocol='custom_http',model='minimax-h3-ref2va',base_url='http://comfy',parameters={'resource_group':'gpu0','poll_seconds':.05}))
    configs.bind_production_model('storyboard_generation',text['id']);configs.bind_production_model('video_generation',video['id'])
    request=BoardInput(script='小林看到杯子，说你好。',total_duration=10,shot_count=2,assets=[{'name':'小林','kind':'character','media_id':a['id']}])
    shots=[]
    for n in range(2):
        shots.append({'id':f'S{n+1:02}','title':'镜头','duration':5,'continuity':'Continues from previous pose',
            'landmarks':'door right','positions':'Lin center','lighting':'warm left','hook':'reveal','asset_names':['小林'],
            'beats':[{'start':i,'end':i+1,'action':'looks up','camera':'locked','spatial':'center','audio':'quiet room','handoff':'holds pose'} for i in range(5)],
            'dialogue':[{'speaker':'小林','text':'你好。','start':1,'end':3}]})
    content=BoardContent(shots=shots);jobs=ProductionJobs(db)
    j=jobs.submit_new('storyboard_generation',request,'board',configs,media)
    db.ai_jobs.update_one({'_id':j['id']},{'$set':{'status':'succeeded','result':{'content':content.model_dump()}}})
    draft=Drafts(db).save(DraftInput(source_job_id=j['id'],content=content))
    return db,jobs,configs,media,request,content,draft

def test_board_time_gaps_rejected(setup):
    data=setup[5].model_dump();data['shots'][0]['beats'][1]['start']=1.1
    with pytest.raises(ValidationError):BoardContent.model_validate(data)

def test_unknown_speaker_or_asset_rejected(setup):
    data=setup[5].model_dump();data['shots'][0]['asset_names']=['not-selected']
    with pytest.raises(ConfigError,match='asset_binding'):parse_board(json.dumps(data),setup[4])

def test_reference_labels_and_order(setup):
    prompt=compile_video(setup[4],setup[5].shots[0])
    sections=['subject_definitions:','summary:','retention_analysis:','detailed_description:','overall_soundscape:','non_diegetic_music:']
    assert [prompt.index(s) for s in sections]==sorted(prompt.index(s) for s in sections)
    assert '<Picture 1>' in prompt and '<d>你好。</d>' in prompt and 'N/A' in prompt

def test_draft_revision_conflict_and_frozen_video(setup):
    db,jobs,cfg,media,request,content,draft=setup
    payload=VideoInput(draft_id=draft['id'],revision=1,shot_id='S01')
    video=jobs.submit_new('video_generation',payload,'video',cfg,media)
    content.shots[0].title='新版'
    Drafts(db).save(DraftInput(source_job_id=draft['source_job_id'],revision=1,content=content),draft['id'])
    assert jobs.get(video['id'])['request']['shot']['title']=='镜头'
    assert jobs.submit_new('video_generation',payload,'video',cfg,media)['id']==video['id']
    with pytest.raises(ConfigError,match='revision_conflict'):jobs.submit_new('video_generation',payload,'new',cfg,media)
    with pytest.raises(ConfigError,match='idempotency_conflict'):jobs.submit_new('video_generation',payload.model_copy(update={'seed':1}),'video',cfg,media)

def test_storyboard_worker_model_and_result(setup):
    db,jobs,cfg,media,request,content,draft=setup
    row=jobs.submit_new('storyboard_generation',request,'new-board',cfg,media)
    llm=Mock();llm.chat.return_value=content.model_dump_json()
    worker=ProductionWorker(jobs,cfg,media,'text',['storyboard_generation'],llm_factory=lambda *a,**k:llm)
    assert worker.run_once();assert jobs.get(row['id'])['status']=='succeeded';llm.chat.assert_called_once()

def test_interrupted_text_never_replays_inference(setup):
    db,jobs,cfg,media,request,content,draft=setup
    row=jobs.submit_new('storyboard_generation',request,'new-board',cfg,media)
    job=jobs.claim_production('dead',['storyboard_generation'],60)
    db.ai_jobs.update_one({'_id':job['_id']},{'$set':{'phase':'inference','lease_until':utcnow()-timedelta(seconds=1)}})
    llm=Mock();worker=ProductionWorker(jobs,cfg,media,'text',['storyboard_generation'],llm_factory=llm)
    worker.run_once();assert jobs.get(row['id'])['error_code']=='execution_outcome_unknown';llm.assert_not_called()

def test_video_resume_does_not_resubmit(setup,monkeypatch):
    db,jobs,cfg,media,_,_,draft=setup
    row=jobs.submit_new('video_generation',VideoInput(draft_id=draft['id'],revision=1,shot_id='S01'),'video',cfg,media)
    old=jobs.claim_production('dead',['video_generation'],60)
    db.ai_jobs.update_one({'_id':old['_id']},{'$set':{'execution':{'state':'submitted','prompt_id':'existing','submission_id':old['_id']},'lease_until':utcnow()-timedelta(seconds=1)}})
    adapter=Mock();adapter.history.return_value={'status':{'completed':True,'status_str':'success'},'outputs':{'92':{'images':[{'filename':'x.mp4','type':'output','subfolder':''}]}}}
    monkeypatch.setattr('lumenx_prism.production_worker.put_video',lambda *a,**k:{'id':'video','has_audio':True})
    ProductionWorker(jobs,cfg,media,'replacement',['video_generation'],adapter_factory=lambda _:adapter).run_once()
    assert jobs.get(row['id'])['status']=='succeeded';adapter.submit.assert_not_called();assert not jobs.finish(old,result={})

def test_uncertain_video_keeps_gpu_lock(setup):
    db,jobs,cfg,media,_,_,draft=setup
    row=jobs.submit_new('video_generation',VideoInput(draft_id=draft['id'],revision=1,shot_id='S01'),'video',cfg,media)
    old=jobs.claim_production('dead',['video_generation'],60)
    db.ai_jobs.update_one({'_id':old['_id']},{'$set':{'execution':{'state':'submitting','submission_id':old['_id']},'lease_until':utcnow()-timedelta(seconds=1)}})
    adapter=Mock();adapter.locate.return_value=None
    ProductionWorker(jobs,cfg,media,'replacement',['video_generation'],adapter_factory=lambda _:adapter).run_once()
    assert jobs.get(row['id'])['error_code']=='execution_outcome_unknown';adapter.submit.assert_not_called()
    assert db.ai_resources.find_one() is not None
    jobs.recover_video(row['id']);assert jobs.get(row['id'])['status']=='queued'

def test_assembly_rejects_missing_and_wrong_candidates(setup):
    db,jobs,cfg,media,_,_,draft=setup
    payload=AssemblyInput(draft_id=draft['id'],revision=1,video_job_ids=[uuid4(),uuid4()])
    with pytest.raises(ConfigError):jobs.submit_new('video_assembly',payload,'assemble',cfg,media)

def test_text_and_gpu_workers_do_not_claim_each_others_tasks(setup):
    db,jobs,cfg,media,request,content,draft=setup
    jobs.submit_new('video_generation',VideoInput(draft_id=draft['id'],revision=1,shot_id='S01'),'video',cfg,media)
    assert jobs.claim_production('text',['storyboard_generation'],60) is None
    assert jobs.claim_production('gpu',['video_generation'],60)['type']=='video_generation'


def test_production_http_contract_and_stale_version(setup,monkeypatch):
    from fastapi.testclient import TestClient
    from lumenx_prism.api import app,get_jobs,get_media,get_model_configs
    db,jobs,cfg,media,request,content,draft=setup
    monkeypatch.setenv('AI_SERVICE_API_KEY','test-token');monkeypatch.setenv('PRISM_SERVICE_AUTH_ENABLED','true')
    app.dependency_overrides[get_jobs]=lambda:jobs
    app.dependency_overrides[get_media]=lambda:media
    app.dependency_overrides[get_model_configs]=lambda:cfg
    try:
        with TestClient(app) as client:
            payload={'draft_id':draft['id'],'revision':1,'shot_id':'S01'}
            assert client.post('/v1/videos/generate/jobs',json=payload).status_code==401
            client.headers['Authorization']='Bearer test-token'
            a=client.post('/v1/videos/generate/jobs',json=payload,headers={'Idempotency-Key':'http-video'})
            assert a.status_code==202
            b=client.post('/v1/videos/generate/jobs',json=payload,headers={'Idempotency-Key':'http-video'})
            assert b.json()['id']==a.json()['id']
            assert client.get(a.headers['Location']).json()['reference_snapshot']
            assert client.get('/v1/jobs?type=video_generation').json()['items']
            assert client.get('/v1/storyboards/'+draft['id']+'/shots/S01/prompt').status_code==200
            assert client.get('/tools/storyboards').status_code==200
            assert client.get('/tools/videos').status_code==200
            payload['revision']=2
            assert client.post('/v1/videos/generate/jobs',json=payload,headers={'Idempotency-Key':'stale'}).status_code==409
    finally:app.dependency_overrides.clear()

def test_video_storage_and_audio_preserving_assembly(setup,tmp_path):
    import subprocess,shutil
    from lumenx_prism.video_media import put_video,assemble
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):pytest.skip('ffmpeg required')
    path=tmp_path/'tiny.mp4'
    subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-f','lavfi','-i','color=c=blue:s=64x64:r=24',
        '-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','0.5','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(path)],check=True)
    media=setup[3];a=put_video(media,path.read_bytes(),'test-clip')
    assert a['has_audio'] and a['kind']=='video'
    result=assemble(media,[a,a],'test-assembly')
    assert result['duration_seconds']>=1 and result['has_audio']
    with pytest.raises(ConfigError,match='video_file_invalid'):put_video(media,b'not-a-video','bad')


def test_absolute_film_clock_normalized_only_when_unambiguous(setup):
    request,content=setup[4:6];data=content.model_dump()
    for b in data['shots'][1]['beats']+data['shots'][1]['dialogue']:
        b['start']+=5;b['end']+=5
    result=parse_board(json.dumps(data),request)
    assert result.shots[1].beats[0].start==0
    assert result.shots[1].dialogue[0].start==1
    data['shots'][1]['beats'][2]['start']+=.2
    with pytest.raises(ConfigError,match='output_invalid'):parse_board(json.dumps(data),request)


def test_video_unload_accepts_empty_comfy_response():
    import httpx
    from lumenx_prism.comfy_videos import ComfyVideos
    calls=[]
    def handle(request):
        calls.append((request.method,request.url.path))
        if request.url.path=='/queue':return httpx.Response(200,json={'queue_running':[],'queue_pending':[]})
        if request.url.path=='/free':return httpx.Response(200,content=b'')
        if request.url.path=='/system_stats':return httpx.Response(200,json={'devices':[{'type':'cuda','vram_free':30*1024**3}]})
        raise AssertionError(request.url)
    adapter=ComfyVideos({'base_url':'http://comfy','parameters':{'resource_group':'gpu0'}})
    adapter.client.close();adapter.client=httpx.Client(base_url='http://comfy',transport=httpx.MockTransport(handle))
    try:assert adapter.idle()
    finally:adapter.close()
    assert calls==[('GET','/queue'),('POST','/free'),('GET','/system_stats')]
