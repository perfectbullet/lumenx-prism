import json
from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4
import mongomock
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from lumenx_prism.script_plans import (PlanInput,PlanContent,PlanSave,Plans,SceneBoardInput,
    parse_plan,declared_duration,validate_plan)
from lumenx_prism.production_jobs import ProductionJobs
from lumenx_prism.production_worker import ProductionWorker
from lumenx_prism.production_schemas import BoardInput
from lumenx_prism.model_configs import ModelConfigs,ModelConfigInput,ConfigError
from lumenx_prism.jobs import utcnow

@pytest.fixture
def env():
    db=mongomock.MongoClient(tz_aware=True).db;jobs=ProductionJobs(db);configs=ModelConfigs(db)
    config=configs.save(ModelConfigInput(name='text',kind='text',provider='mock',protocol='openai_chat',model='test',base_url='http://test',api_key='secret'))
    configs.bind_production_model('storyboard_generation',config['id'])
    request=PlanInput(script='第一集：小功能\n预计时长：5分钟\n场一办公室\n小林说你好。\n场二会议室\n小林离开。')
    data={'title':'代码之外','input_kind':'完整单集剧本','style':'写实','shared_context':'小林黑发灰衬衫；同一天上午。',
          'warnings':['时长需试读核对'],'episodes':[{'id':'E01','title':'小功能','summary':'需求变化','target_duration':300}],
          'scenes':[{'id':f'C{i+1:02}','episode_id':'E01','title':title,'start_line':start,'end_line':end,
              'summary':title,'estimated_duration':80,'duration':60,'shot_count':8,'timing_basis':'对白与动作估算',
              'required_assets':['小林']} for i,(title,start,end) in enumerate([('办公室',1,4),('会议室',5,6)])]}
    return db,jobs,configs,request,data

def completed(env):
    db,jobs,configs,request,data=env
    j=jobs.submit_new('script_planning',request,'plan',configs,None)
    llm=Mock();llm.chat.return_value=json.dumps(data);llm.actual_model='test';llm.usage=None
    ProductionWorker(jobs,configs,None,'text',['script_planning'],llm_factory=lambda *a,**k:llm).run_once()
    assert jobs.get(j['id'])['status']=='succeeded'
    saved=Plans(db).save(PlanSave(source_job_id=j['id'],content=PlanContent(**data)))
    return j,saved,llm

def test_declared_time_and_original_source(env):
    _,_,_,request,data=env
    assert declared_duration(request.script)==300
    assert declared_duration('目标时长: 1.5分钟')==90
    assert declared_duration('上午09:30') is None
    assert parse_plan('```json\n'+json.dumps(data)+'\n```',request).scenes[1].start_line==5

@pytest.mark.parametrize('change',['gap','overlap','end','episode','order'])
def test_source_coverage_cannot_be_lost_or_reordered(env,change):
    *_,request,data=env
    if change=='gap':data['scenes'][1]['start_line']=6
    if change=='overlap':data['scenes'][1]['start_line']=4
    if change=='end':data['scenes'][1]['end_line']=5
    if change=='episode':data['scenes'][1]['episode_id']='E02'
    if change=='order':data['scenes'][1]['id']='C03'
    with pytest.raises(ConfigError):parse_plan(json.dumps(data),request)

def test_async_idempotency_and_no_gpu(env):
    db,jobs,configs,request,data=env;j,saved,llm=completed(env)
    assert jobs.submit_new('script_planning',request,'plan',configs,None)['id']==j['id']
    assert jobs.get(j['id'])['result']['declared_duration']==300
    assert db.ai_resources.count_documents({})==0
    assert 'secret' not in json.dumps(j,default=str)
    with pytest.raises(ConfigError,match='idempotency_conflict'):
        jobs.submit_new('script_planning',request.model_copy(update={'script':'different'}),'plan',configs,None)

def test_plan_revision_edit_and_provenance(env):
    db,*_=env;j,p,_=completed(env);store=Plans(db)
    content=PlanContent(**p['content']);content.scenes[0].duration=64
    update=PlanSave(source_job_id=j['id'],revision=1,content=content)
    assert store.save(update,p['id'])['revision']==2
    with pytest.raises(ConfigError,match='revision_conflict'):store.save(update,p['id'])
    content.scenes[0].end_line=3;content.scenes[1].start_line=4
    with pytest.raises(ConfigError,match='mapping_changed'):store.save(PlanSave(source_job_id=j['id'],revision=2,content=content),p['id'])

def test_scene_input_freezes_episode_context_and_exact_source(env):
    db,jobs,configs,request,_=env;j,p,_=completed(env);store=Plans(db)
    payload=SceneBoardInput(revision=1,assets=[{'name':'小林','kind':'character','media_id':str(uuid4()),'description':'已确认服装'}])
    board=store.scene_board(p['id'],'C02',payload)
    assert board.script=='场二会议室\n小林离开。'
    assert board.total_duration==60 and board.shot_count==8
    assert board.scene_context['previous_scene']=='办公室'
    assert board.scene_context['episode']['title']=='小功能'
    assert board.scene_context['shared_context']==p['content']['shared_context']
    assert board.assets[0].description=='已确认服装'
    from lumenx_prism.storyboards import planning_prompt
    assert 'THIS scene faithfully' in planning_prompt(board)
    payload.revision=2
    with pytest.raises(ConfigError,match='revision_conflict'):store.scene_board(p['id'],'C02',payload)

def test_missing_assets_and_demo_compatibility(env):
    db,*_=env;j,p,_=completed(env)
    assets=[{'name':'其他人','kind':'character','media_id':str(uuid4())}]
    with pytest.raises(ConfigError,match='scene_assets_missing'):Plans(db).scene_board(p['id'],'C01',SceneBoardInput(revision=1,assets=assets))
    with pytest.raises(ValidationError):BoardInput(script='test',assets=assets,total_duration=60,shot_count=8)

def test_each_scene_retry_is_independent_and_old_input_stays_frozen(env):
    db,jobs,configs,*_=env;j,p,_=completed(env);store=Plans(db)
    media=Mock();media.snapshot.return_value=[{'id':'image','kind':'image','sha256':'hash'}]
    payload=SceneBoardInput(revision=1,assets=[{'name':'小林','kind':'character','media_id':str(uuid4())}])
    first=jobs.submit_new('storyboard_generation',store.scene_board(p['id'],'C01',payload),'scene1',configs,media)
    second=jobs.submit_new('storyboard_generation',store.scene_board(p['id'],'C02',payload),'scene2',configs,media)
    retry=jobs.submit_new('storyboard_generation',store.scene_board(p['id'],'C01',payload),'retry1',configs,media)
    assert first['id']!=retry['id']
    assert jobs.get(second['id'])['status']=='queued'
    assert len(store.scene_jobs(p['id'],1)['items'])==3
    content=PlanContent(**p['content']);content.shared_context='改穿蓝衬衣'
    store.save(PlanSave(source_job_id=j['id'],revision=1,content=content),p['id'])
    assert jobs.get(first['id'])['request']['scene_context']['shared_context']!='改穿蓝衬衣'

def test_received_output_recovers_without_model_replay(env):
    db,jobs,configs,request,data=env;j=jobs.submit_new('script_planning',request,'plan',configs,None)
    jobs.claim_production('old',['script_planning'],60)
    db.ai_jobs.update_one({'_id':j['id']},{'$set':{'phase':'validating','raw_model_output':json.dumps(data),'lease_until':utcnow()-timedelta(seconds=1)}})
    factory=Mock();ProductionWorker(jobs,configs,None,'new',['script_planning'],llm_factory=factory).run_once()
    assert jobs.get(j['id'])['status']=='succeeded';factory.assert_not_called()

def test_unknown_inference_outcome_not_retried(env):
    db,jobs,configs,request,data=env;j=jobs.submit_new('script_planning',request,'plan',configs,None)
    jobs.claim_production('old',['script_planning'],60)
    db.ai_jobs.update_one({'_id':j['id']},{'$set':{'phase':'inference','lease_until':utcnow()-timedelta(seconds=1)}})
    factory=Mock();ProductionWorker(jobs,configs,None,'new',['script_planning'],llm_factory=factory).run_once()
    assert jobs.get(j['id'])['error_code']=='execution_outcome_unknown';factory.assert_not_called()

def test_http_plan_and_scene_endpoints(env,monkeypatch):
    from lumenx_prism.api import app,get_jobs,get_model_configs,get_media
    db,jobs,configs,request,data=env;j,p,_=completed(env)
    media=Mock();media.snapshot.return_value=[{'id':'image','kind':'image','sha256':'hash'}]
    monkeypatch.setenv('PRISM_SERVICE_AUTH_ENABLED','false')
    app.dependency_overrides[get_jobs]=lambda:jobs;app.dependency_overrides[get_model_configs]=lambda:configs;app.dependency_overrides[get_media]=lambda:media
    try:
        with TestClient(app) as c:
            r=c.post('/v1/scripts/plan/jobs',json=request.model_dump(mode='json'),headers={'Idempotency-Key':'http'})
            assert r.status_code==202 and r.headers['location'].endswith(r.json()['id'])
            assert c.get('/v1/jobs?type=script_planning').status_code==200
            assert c.get('/tools/scripts').status_code==200
            payload={'revision':1,'assets':[{'name':'小林','kind':'character','media_id':str(uuid4())}]}
            r=c.post(f"/v1/script-plans/{p['id']}/scenes/C01/storyboard/jobs",json=payload,headers={'Idempotency-Key':'http-scene'})
            assert r.status_code==202
            board=r.json()['request']
            assert board['scene_context']['scene_id']=='C01'
            assert c.post('/v1/storyboards/generate/jobs',json=board,headers={'Idempotency-Key':'forged'}).status_code==422
            assert len(c.get(f"/v1/script-plans/{p['id']}/scene-jobs?revision=1").json()['items'])==1
    finally:app.dependency_overrides.clear()


def test_explicit_reference_mapping_is_validated_and_frozen(env):
    db,*_=env;j,p,_=completed(env)
    assets=[{'name':'小林定妆图','kind':'character','media_id':str(uuid4())}]
    payload=SceneBoardInput(revision=1,assets=assets,asset_bindings={'小林':'小林定妆图'})
    board=Plans(db).scene_board(p['id'],'C01',payload)
    assert board.scene_context['asset_bindings']=={'小林':'小林定妆图'}
    payload.asset_bindings={'小林':'不存在'}
    with pytest.raises(ConfigError,match='scene_asset_binding_invalid'):Plans(db).scene_board(p['id'],'C01',payload)

def test_multiple_episode_times_are_not_mistaken_for_total():
    assert declared_duration('第一集\n预计时长：5分钟\n第二集\n预计时长：5分钟') is None
    assert declared_duration('总时长：10分钟\n第一集\n预计时长：5分钟\n第二集\n预计时长：5分钟')==600


def test_source_second_episode_normalizes_only_matching_number(env):
    *_,request,data=env
    request=request.model_copy(update={'work_context':__import__('lumenx_prism.works',fromlist=['WorkContext']).WorkContext(work_id=uuid4(),edition_id=uuid4(),episode_id='E02')})
    data['episodes'][0]['id']='E02'
    for scene in data['scenes']:scene['episode_id']='E02'
    result=parse_plan(json.dumps(data),request)
    assert result.episodes[0].id=='E01'
    assert all(s.episode_id=='E01' for s in result.scenes)
    data['episodes'][0]['id']='E03'
    with pytest.raises(ConfigError):parse_plan(json.dumps(data),request)


def test_missing_scene_assets_error_identifies_names_and_explicit_alias_binding(env):
 db,*_=env;j,p,_=completed(env);store=Plans(db)
 payload=SceneBoardInput(revision=1,assets=[{'name':'小林的定妆照','kind':'character','media_id':str(uuid4())}])
 with pytest.raises(ConfigError) as exc:store.scene_board(p['id'],'C01',payload)
 assert exc.value.details['missing_assets']==['小林']
 payload.asset_bindings={'小林':'小林的定妆照'}
 board=store.scene_board(p['id'],'C01',payload)
 assert board.scene_context['asset_bindings']=={'小林':'小林的定妆照'}


def test_scene_history_exposes_readable_snapshot_and_source_episode(env):
 db,*_=env;j,p,_=completed(env)
 from lumenx_prism.jobs import utcnow
 row={'_id':str(uuid4()),'type':'storyboard_generation','status':'succeeded','created_at':utcnow(),'request':{'title':'作品第二集 · C02 会议室','scene_context':{'plan_id':p['id'],'revision':1,'scene_id':'C02','episode_id':'E01','work_context':{'episode_id':'E02'}}}}
 db.ai_jobs.insert_one(row)
 item=Plans(db).scene_jobs(p['id'],1)['items'][0]
 assert item['scene_title']=='会议室' and item['episode_id']=='E02'
 assert item['scene_id']=='C02' and item['status']=='succeeded'
 assert 'request' not in item
 assert Plans(db).scene_jobs(p['id'],2)['items']==[]


def test_screen_content_uses_carrier_and_is_preserved_without_extra_reference(env):
 db,jobs,configs,request,data=env
 quote='小林打开屏幕中的方案文档，标题写下“共享调度”。'
 request.script=request.script.replace('小林说你好。',quote)
 data['scenes'][0]['required_assets']=['小林','显示器']
 data['scenes'][0]['screen_contents']=[{'name':'方案文档','carrier':'显示器','description':'打开电子方案，文字后期合成','source_quote':quote,'overlay_text':'共享调度'}]
 j,p,_=completed(env)
 payload=SceneBoardInput(revision=1,assets=[{'name':n,'kind':k,'media_id':str(uuid4())} for n,k in [('小林','character'),('显示器','prop')]])
 board=Plans(db).scene_board(p['id'],'C01',payload)
 assert '方案文档' not in board.scene_context['required_assets']
 assert board.scene_context['screen_contents'][0]['overlay_text']=='共享调度'
 from lumenx_prism.storyboards import planning_prompt
 assert 'screen_contents' in planning_prompt(board)

@pytest.mark.parametrize('bad',['carrier','quote','overlay','double'])
def test_screen_classification_cannot_skip_physical_reference_or_invent_evidence(env,bad):
 *_,request,data=env
 note={'name':'方案文档','carrier':'小林','description':'屏幕内容','source_quote':'小林说你好。','overlay_text':''}
 data['scenes'][0]['screen_contents']=[note]
 if bad=='carrier':note['carrier']='不存在的显示器'
 if bad=='quote':note['source_quote']='原文没有这句话'
 if bad=='overlay':note['overlay_text']='原文没有的文字'
 if bad=='double':data['scenes'][0]['required_assets'].append('方案文档')
 with pytest.raises(ConfigError,match='screen_content_invalid'):parse_plan(json.dumps(data),request)

def test_plan_update_archives_old_revision_without_mutating_existing_jobs(env):
 db,*_=env;j,p,_=completed(env);before=json.loads(json.dumps(db.ai_jobs.find_one({'_id':j['id']})['result']))
 content=PlanContent(**p['content']);content.scenes[0].summary='修正素材分类'
 result=Plans(db).save(PlanSave(source_job_id=j['id'],revision=1,content=content),p['id'])
 archived=db.script_plan_revisions.find_one({'_id':p['id']+':1'})
 assert result['revision']==2 and archived['content']==p['content']
 assert db.ai_jobs.find_one({'_id':j['id']})['result']==before
