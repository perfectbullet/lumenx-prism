import io,json
from unittest.mock import Mock
from uuid import uuid4
import pytest
from PIL import Image
from pydantic import ValidationError
from fastapi.testclient import TestClient
from test_production import setup
from lumenx_prism.h3_requests import H3ReferenceInput
from lumenx_prism.comfy_videos import build
from lumenx_prism.production_schemas import VideoParameters,VideoInput
from lumenx_prism.production_worker import ProductionWorker
from lumenx_prism.model_configs import ConfigError
from lumenx_prism.api import app,get_jobs,get_model_configs,get_media
from lumenx_prism.comfy_control import GPUControl


def request_for(media,**values):
    f=io.BytesIO();Image.new('RGB',(80,80),'blue').save(f,format='PNG');a=media.put(f.getvalue())
    return H3ReferenceInput(prompt='参考 <Picture 1> 中的画风，镜头缓慢向前。自然环境声，无音乐。',reference_media_ids=[a['id']],**values)


@pytest.mark.parametrize('values',[{'duration_seconds':3.9},{'duration_seconds':15.1},{'seed':2**53},{'steps':51},{'aspect_ratio':'2:1'},{'output_megapixels':2.1},{'prompt':'  '},{'duration_seconds':float('nan')},{'draft_id':str(uuid4())}])
def test_invalid_standalone_parameters(values):
    data=dict(prompt='test',reference_media_ids=[uuid4()]);data.update(values)
    with pytest.raises(ValidationError):H3ReferenceInput(**data)


def test_no_refs_and_duplicate_refs_rejected():
    id=uuid4()
    for ids in [[],[id,id],[uuid4() for _ in range(10)]]:
        with pytest.raises(ValidationError):H3ReferenceInput(prompt='test',reference_media_ids=ids)


def test_raw_graph_uses_request_values_and_reference_order():
    p=H3ReferenceInput(prompt='原提示词 <Picture 2>',reference_media_ids=[uuid4(),uuid4()],duration_seconds=5.5,aspect_ratio='1:1',steps=12,output_megapixels=.6,seed=2**53-1,ref_image_size='最高保真（更慢）')
    g=build(p.model_dump(mode='json'),p.prompt,VideoParameters(resource_group='gpu'),['two.png','one.png'],'raw')
    d=g['136']['inputs'];items=json.loads(d['timeline_data'])['items']
    assert d['duration_seconds']==5.5 and d['aspect_ratio']=='1:1 方形'
    assert d['global_prompt']==p.prompt and d['output_megapixels']==.6
    assert d['ref_image_size']=='最高保真（更慢）'
    assert [i['file'] for i in items]==['two.png','one.png'] and all(i['duration']==5.5 for i in items)
    assert g['124']['inputs']['steps']==12 and g['129']['inputs']['noise_seed']==2**53-1
    assert g['130']['inputs']['fps']==24 and d['mode']=='全能参考'
    assert '__REFERENCE_IMAGE__' not in json.dumps(g)


def test_job_is_durable_independent_idempotent_and_filtered(setup):
    db,jobs,cfg,media,_,_,draft=setup;p=request_for(media,steps=13)
    j=jobs.submit_new('video_generation',p,'raw-h3',cfg,media)
    assert j['request']['reference_media_ids']==[r['id'] for r in j['reference_snapshot']]
    assert 'draft_id' not in j['request'] and j['effective_prompt']==p.prompt
    assert j['workflow_snapshot']['version']=='h3-ref2va-v1'
    assert jobs.submit_new('video_generation',p,'raw-h3',cfg,media)['id']==j['id']
    with pytest.raises(ConfigError,match='idempotency_conflict'):jobs.submit_new('video_generation',p.model_copy(update={'steps':14}),'raw-h3',cfg,media)
    old=jobs.submit_new('video_generation',VideoInput(draft_id=draft['id'],revision=1,shot_id='S01'),'old-shot',cfg,media)
    assert jobs.list(20,0,job_type='video_generation',prompt_mode='raw')['items'][0]['id']==j['id']
    assert jobs.list(20,0,job_type='video_generation',prompt_mode='asset')['items'][0]['id']==old['id']
    assert jobs.list(20,0,job_type='video_generation')['total']==2


def test_raw_worker_uses_gpu_pause_and_saves_audio_video(setup,monkeypatch):
    db,jobs,cfg,media,*_=setup;p=request_for(media,steps=11)
    j=jobs.submit_new('video_generation',p,'raw-worker',cfg,media)
    adapter=Mock();adapter.idle.return_value=True;adapter.upload.return_value='ref.png';adapter.submit.return_value='prompt-id'
    adapter.history.return_value={'status':{'completed':True,'status_str':'success'},'outputs':{'92':{'videos':[{'filename':'x.mp4'}]}}}
    adapter.actual_model='h3';adapter.usage=None
    monkeypatch.setattr('lumenx_prism.production_worker.put_video',lambda *a,**kw:{'id':'video','has_audio':True})
    worker=ProductionWorker(jobs,cfg,media,'raw-worker',['video_generation'],adapter_factory=lambda _:adapter)
    GPUControl(db).pause();assert worker.run_once() is False;adapter.submit.assert_not_called()
    GPUControl(db).resume();assert worker.run_once()
    result=jobs.get(j['id']);assert result['status']=='succeeded' and result['result']['requested_duration']==4
    assert result['result']['artifacts'][0]['has_audio'] is True
    assert 'shot_id' not in result['result']
    graph=adapter.submit.call_args.args[0];assert graph['124']['inputs']['steps']==11
    assert graph['136']['inputs']['global_prompt']==p.prompt
    assert db.ai_resources.count_documents({})==0


def test_missing_reference_never_creates_task(setup):
    _,jobs,cfg,media,*_=setup;p=H3ReferenceInput(prompt='test',reference_media_ids=[uuid4()])
    with pytest.raises(ConfigError):jobs.submit_new('video_generation',p,'bad-reference',cfg,media)
    assert not jobs.collection.find_one({'idempotency_key':'bad-reference'})


def test_http_submission_history_auth_and_validation(setup,monkeypatch):
    _,jobs,cfg,media,*_=setup;p=request_for(media)
    monkeypatch.setenv('AI_SERVICE_API_KEY','test-token')
    app.dependency_overrides[get_jobs]=lambda:jobs;app.dependency_overrides[get_model_configs]=lambda:cfg;app.dependency_overrides[get_media]=lambda:media
    try:
        with TestClient(app) as c:
            assert c.get('/tools/h3-ref2va-v1').status_code==200
            url='/v1/videos/h3-ref2va-v1/jobs';headers={'Idempotency-Key':'http-h3'};data=p.model_dump(mode='json')
            assert c.post(url,json=data,headers=headers).status_code==401
            c.headers['Authorization']='Bearer test-token'
            a=c.post(url,json=data,headers=headers);assert a.status_code==202,a.text
            assert c.post(url,json=data,headers=headers).json()['id']==a.json()['id']
            assert c.get('/v1/jobs?type=video_generation&prompt_mode=raw').json()['total']==1
            assert c.get('/v1/jobs/'+a.json()['id']).json()['request']['prompt']==p.prompt
            assert c.post(url,json={**data,'duration_seconds':16},headers={'Idempotency-Key':'bad'}).status_code==422
    finally:app.dependency_overrides.clear()


def test_h3_mp_defaults_and_boundary_match_request_and_model_config():
    from lumenx_prism.production_schemas import VideoParameters
    p=H3ReferenceInput(prompt='test',reference_media_ids=[uuid4()])
    assert p.output_megapixels==1.8
    assert H3ReferenceInput.model_validate({**p.model_dump(),'output_megapixels':2}).output_megapixels==2
    assert VideoParameters(resource_group='gpu').output_megapixels==1.8
    assert VideoParameters(resource_group='gpu',output_megapixels=2).output_megapixels==2
    with pytest.raises(ValidationError):VideoParameters(resource_group='gpu',output_megapixels=2.1)
