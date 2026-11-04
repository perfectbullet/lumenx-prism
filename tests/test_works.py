import json
from copy import deepcopy
from unittest.mock import Mock
from uuid import uuid4
import mongomock
import pytest
from fastapi.testclient import TestClient
from lumenx_prism.works import (Works,WorkCreate,WorkRename,WorkContext,BreakdownOptions,
    Breakdown,EditionConfirm,parse_breakdown,validate_breakdown)
from lumenx_prism.model_configs import ConfigError,ModelConfigs,ModelConfigInput
from lumenx_prism.production_jobs import ProductionJobs
from lumenx_prism.production_worker import ProductionWorker
from lumenx_prism.schemas import EntityExtractionRequest
from lumenx_prism.jobs import Jobs

@pytest.fixture
def env():
    db=mongomock.MongoClient(tz_aware=True).db;store=Works(db);jobs=ProductionJobs(db);configs=ModelConfigs(db)
    config=configs.save(ModelConfigInput(name='mock text',kind='text',provider='mock',protocol='openai_chat',model='mock',base_url='http://mock',api_key='not-public'))
    configs.bind_production_model('storyboard_generation',config['id'])
    work=store.create(WorkCreate(title='原名',kind='novel',text='第一章\n小林上班。\n第二章\n小林回家。'))
    content={'title_suggestions':['一天'],'brief':'一天的故事','story_outline':'上班到回家','shared_context':'小林保持灰衣',
        'style_recommendations':[{'name':'写实','description':'电影写实','reason':'都市题材'}],'selected_style':'电影写实',
        'episodes':[{'id':f'E{i+1:02}','title':t,'summary':t,'start_line':i*2+1,'end_line':i*2+2,'target_duration':300} for i,t in enumerate(['上班','回家'])]}
    return db,store,jobs,configs,work,content

def complete(env):
    db,store,jobs,cfg,w,data=env
    request=store.request(w['id'],BreakdownOptions())
    job=jobs.submit_new('work_breakdown',request,'breakdown',cfg,None)
    llm=Mock();llm.chat.return_value=json.dumps(data);llm.actual_model='mock';llm.usage=None
    ProductionWorker(jobs,cfg,None,'text',['work_breakdown'],llm_factory=lambda *a,**k:llm).run_once()
    result=jobs.get(job['id']);assert result['status']=='succeeded'
    return result

def test_breakdown_requires_explicit_confirmation_and_no_gpu(env):
    db,store,jobs,cfg,w,data=env;j=complete(env)
    assert store.list_editions(w['id'])['items']==[]
    assert db.ai_resources.count_documents({})==0
    confirmed=store.confirm(w['id'],EditionConfirm(source_job_id=j['id'],content=Breakdown(**data)))
    assert confirmed['work_id']==w['id']
    assert 'not-public' not in json.dumps(j,default=str)

@pytest.mark.parametrize('case',['gap','overlap','truncated','reverse','unknown-id'])
def test_episode_source_coverage(env,case):
    db,store,jobs,cfg,w,data=env
    if case=='gap':data['episodes'][1]['start_line']=4
    if case=='overlap':data['episodes'][1]['start_line']=2
    if case=='truncated':data['episodes'][1]['end_line']=3
    if case=='reverse':data['episodes'][0]['end_line']=0
    if case=='unknown-id':data['episodes'][1]['id']='E03'
    with pytest.raises((ConfigError,ValueError)):validate_breakdown(Breakdown(**data),w['text'])

def test_confirm_idempotent_and_edits_never_mutate_old_version(env):
    db,store,jobs,cfg,w,data=env;j=complete(env)
    payload=EditionConfirm(source_job_id=j['id'],content=Breakdown(**data));a=store.confirm(w['id'],payload)
    assert store.confirm(w['id'],payload)['id']==a['id']
    payload.content.episodes[0].title='新标题';b=store.confirm(w['id'],payload)
    assert a['id']!=b['id'] and store.edition(w['id'],a['id'])['content']['episodes'][0]['title']=='上班'
    other=store.create(WorkCreate(title='另一个作品',kind='story',text=w['text']))
    with pytest.raises(ConfigError):store.confirm(other['id'],payload)

def test_context_protects_source_and_work_identity(env):
    db,store,jobs,cfg,w,data=env;j=complete(env);ed=store.confirm(w['id'],EditionConfirm(source_job_id=j['id'],content=Breakdown(**data)))
    ctx=WorkContext(work_id=w['id'],edition_id=ed['id'],episode_id='E02')
    assert store.validate_context(ctx,'第二章\n小林回家。')['episode']['title']=='回家'
    with pytest.raises(ConfigError,match='text_changed'):store.validate_context(ctx,'第一章\n小林上班。')
    before=w['source_sha256'];renamed=store.rename(w['id'],WorkRename(title='新名字',revision=1))
    assert renamed['source_sha256']==before and store.episode(ctx)['work_title']=='新名字'
    with pytest.raises(ConfigError,match='revision_conflict'):store.rename(w['id'],WorkRename(title='覆盖',revision=1))

def test_scoped_entity_request_serializes_uuid_and_filters(env):
    db,store,jobs,cfg,w,data=env;j=complete(env);ed=store.confirm(w['id'],EditionConfirm(source_job_id=j['id'],content=Breakdown(**data)))
    ctx=WorkContext(work_id=w['id'],edition_id=ed['id'],episode_id='E01')
    request=EntityExtractionRequest(text=store.episode(ctx)['text'],work_context=ctx)
    config=cfg.get(j['model_config']['id']);r=Jobs(db).submit(request,'entity',config)
    assert r['request']['work_context']['work_id']==w['id']
    assert Jobs(db).list(20,0,work_id=w['id'],episode_id='E01')['items'][0]['id']==r['id']
    assert not Jobs(db).list(20,0,work_id=w['id'],episode_id='E02')['items']

def test_unscoped_entity_fingerprint_remains_compatible():
    import hashlib
    request=EntityExtractionRequest(text='原文')
    legacy=hashlib.sha256(json.dumps({'text':'原文','custom_extraction_prompt':''},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    assert Jobs.fingerprint(request)==legacy

def test_work_http_and_auth(env,monkeypatch):
    from lumenx_prism.api import app,get_jobs,get_model_configs,get_media
    db,store,jobs,cfg,w,data=env
    monkeypatch.setenv('PRISM_SERVICE_AUTH_ENABLED','true');monkeypatch.setenv('AI_SERVICE_API_KEY','test-service-key')
    app.dependency_overrides.update({get_jobs:lambda:jobs,get_model_configs:lambda:cfg,get_media:lambda:None})
    try:
        with TestClient(app) as client:
            assert client.get('/v1/works').status_code==401
            headers={'Authorization':'Bearer test-service-key'}
            assert client.get('/tools/works').status_code==200
            assert 'text' not in client.get('/v1/works',headers=headers).json()['items'][0]
            assert client.get('/v1/works/'+w['id'],headers=headers).json()['text']==w['text']
            url=f"/v1/works/{w['id']}/breakdown/jobs";h=headers|{'Idempotency-Key':'http-breakdown'}
            a=client.post(url,json={},headers=h);assert a.status_code==202
            store.rename(w['id'],WorkRename(title='renamed',revision=1))
            b=client.post(url,json={},headers=h);assert b.json()['id']==a.json()['id']
    finally:app.dependency_overrides.clear()
