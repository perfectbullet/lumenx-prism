import json
from datetime import timedelta
from unittest.mock import Mock
import mongomock
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from lumenx_prism.image_polish import ImagePolishInput,parse_polish
from lumenx_prism.image_prompts import compile_image_prompt
from lumenx_prism.image_schemas import ImageRequest
from lumenx_prism.production_jobs import ProductionJobs
from lumenx_prism.production_worker import ProductionWorker
from lumenx_prism.model_configs import ModelConfigs,ModelConfigInput,ConfigError
from lumenx_prism.jobs import utcnow

@pytest.fixture
def polish_input():
    return ImagePolishInput(asset_type='character',name='周屿',description='32 男性 黑色短发，戴黑框眼镜。深灰色连帽卫衣。',
        character_card={'name':'周屿','role':'主角','age_range':'23','body_type':'中等','hairstyle':'短发',
            'costume':'浅灰色衬衣、黑色长裤、黑色运动鞋','props':['眼镜','不喜欢刮胡子','喜欢喝咖啡'],
            'immutable_traits':['黑色短发，戴黑框眼镜。'],'expressions':['neutral','happy']})

def suggestion(polish_input):
    card=polish_input.character_card.model_dump();card['props']=['眼镜']
    return {'description':'23岁男性，黑色短发、黑框眼镜，中等体型，穿浅灰色衬衣、黑色长裤、黑色运动鞋。',
        'character_card':card,'warnings':['已按角色细节采用23岁和浅灰衬衣。','是否需要胡茬、是否手持咖啡杯请明确，暂未添加。']}

def test_current_character_details_take_priority_in_compiled_prompt(polish_input):
    r=ImageRequest(**polish_input.model_dump(),generation_type='full_body')
    prompt,_=compile_image_prompt(r)
    assert prompt.index('CURRENT REQUIRED APPEARANCE')<prompt.index('32 男性')
    assert 'Secondary background description' in prompt and prompt.count('浅灰色衬衣')>=3
    assert prompt.rfind('CURRENT REQUIRED APPEARANCE')>prompt.index('32 男性')
    assert 'override conflicting older age' in prompt
    assert r.description==polish_input.description

def test_polish_preserves_constraints_and_keeps_preferences_pending(polish_input):
    result=parse_polish(json.dumps(suggestion(polish_input),ensure_ascii=False),polish_input)
    assert result.character_card.age_range=='23' and result.character_card.props==['眼镜']
    assert '胡茬' not in result.description and '咖啡杯' not in result.description
    assert result.warnings and polish_input.character_card.props==['眼镜','不喜欢刮胡子','喜欢喝咖啡']

@pytest.mark.parametrize('field,value',[('age_range','32'),('costume','深灰色卫衣'),('immutable_traits',[])])
def test_polish_rejects_changed_explicit_constraints(polish_input,field,value):
    data=suggestion(polish_input);data['character_card'][field]=value
    with pytest.raises(ConfigError,match='changed_constraints'):parse_polish(json.dumps(data),polish_input)

def test_polish_rejects_invented_prop_and_invalid_json(polish_input):
    data=suggestion(polish_input);data['character_card']['props']=['咖啡杯']
    with pytest.raises(ConfigError,match='invented_props'):parse_polish(json.dumps(data),polish_input)
    with pytest.raises(ConfigError,match='output_invalid'):parse_polish('not-json',polish_input)

def test_non_character_output_cannot_inject_a_character(polish_input):
    prop=ImagePolishInput(asset_type='prop',name='杯子',description='白色陶瓷杯')
    with pytest.raises(ConfigError,match='output_invalid'):parse_polish(json.dumps(suggestion(polish_input)),prop)

def test_blank_character_input_rejected():
    with pytest.raises(ValidationError):ImagePolishInput(asset_type='character',character_card={})

def setup_db():
    db=mongomock.MongoClient(tz_aware=True)['lumenx_prism'];configs=ModelConfigs(db)
    row=configs.save(ModelConfigInput(name='Polish mock',kind='text',provider='mock',protocol='openai_chat',model='mock',base_url='http://mock',api_key='fake'))
    configs.bind_production_model('image_polish',row['id'])
    return db,ProductionJobs(db),configs

def test_async_polish_idempotency_and_no_image_job(polish_input):
    db,jobs,configs=setup_db();row=jobs.submit_new('image_polish',polish_input,'polish',configs,None)
    assert jobs.submit_new('image_polish',polish_input,'polish',configs,None)['id']==row['id']
    llm=Mock();llm.chat.return_value=json.dumps(suggestion(polish_input));llm.actual_model='mock';llm.usage=None
    assert ProductionWorker(jobs,configs,None,'text',['image_polish'],llm_factory=lambda *a,**k:llm).run_once()
    result=jobs.get(row['id']);assert result['status']=='succeeded' and result['result']['review_required']
    assert db.ai_jobs.count_documents({'type':'image_generation'})==0
    assert db.ai_resources.count_documents({})==0
    assert 'api_key' not in json.dumps(result,default=str)
    with pytest.raises(ConfigError,match='idempotency_conflict'):jobs.submit_new('image_polish',polish_input.model_copy(update={'description':'changed'}),'polish',configs,None)

def test_received_polish_output_recovers_without_another_model_call(polish_input):
    db,jobs,configs=setup_db();row=jobs.submit_new('image_polish',polish_input,'polish',configs,None)
    old=jobs.claim_production('dead',['image_polish'],60)
    db.ai_jobs.update_one({'_id':row['id']},{'$set':{'phase':'validating','raw_model_output':json.dumps(suggestion(polish_input)),
        'lease_until':utcnow()-timedelta(seconds=1)}})
    factory=Mock();ProductionWorker(jobs,configs,None,'new',['image_polish'],llm_factory=factory).run_once()
    assert jobs.get(row['id'])['status']=='succeeded';factory.assert_not_called()
    assert not jobs.finish(old,result={})

def test_interrupted_polish_inference_is_not_replayed(polish_input):
    db,jobs,configs=setup_db();row=jobs.submit_new('image_polish',polish_input,'polish',configs,None)
    jobs.claim_production('dead',['image_polish'],60)
    db.ai_jobs.update_one({'_id':row['id']},{'$set':{'phase':'inference','lease_until':utcnow()-timedelta(seconds=1)}})
    factory=Mock();ProductionWorker(jobs,configs,None,'new',['image_polish'],llm_factory=factory).run_once()
    assert jobs.get(row['id'])['error_code']=='execution_outcome_unknown';factory.assert_not_called()

def test_polish_http_does_not_generate_image(polish_input,monkeypatch):
    from lumenx_prism.api import app,get_jobs,get_model_configs,get_media
    db,jobs,configs=setup_db();monkeypatch.setenv('PRISM_SERVICE_AUTH_ENABLED','false')
    app.dependency_overrides[get_jobs]=lambda:jobs
    app.dependency_overrides[get_model_configs]=lambda:configs
    app.dependency_overrides[get_media]=lambda:None
    try:
        with TestClient(app) as client:
            response=client.post('/v1/images/polish/jobs',json=polish_input.model_dump(),headers={'Idempotency-Key':'http-polish'})
            assert response.status_code==202 and response.headers['location'].endswith(response.json()['id'])
            assert client.get('/v1/jobs?type=image_polish').json()['items']
            assert db.ai_jobs.count_documents({'type':'image_generation'})==0
    finally:app.dependency_overrides.clear()
