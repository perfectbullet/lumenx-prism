from copy import deepcopy
import mongomock
import pytest
from uuid import uuid4
from fastapi.testclient import TestClient
from lumenx_prism.comfy_api_configs import ComfyApiConfigs,ComfyApiInput
from lumenx_prism.model_configs import ModelConfigs,ModelConfigInput,ConfigError
from lumenx_prism.comfy_control import ComfyControl
from lumenx_prism.comfy_videos import ComfyVideos


def payload(**values):
 return ComfyApiInput(name='service',base_url='http://comfy:8188',resource_group='gpu0',min_free_vram_mb=30720,memory_check_seconds=17,queue_retry_seconds=7,http_timeout_seconds=45,poll_seconds=2,execution_timeout_seconds=1800,**values)


def test_model_references_one_authoritative_instance_without_duplicate_connection():
 db=mongomock.MongoClient(tz_aware=True).db;services=ComfyApiConfigs(db);cfg=ModelConfigs(db)
 server=services.save(payload(api_key='private-service-secret'))
 model=cfg.save(ModelConfigInput(name='video',kind='video',provider='comfyui',protocol='custom_http',model='minimax-h3-ref2va',comfyui_api_config_id=server['id'],parameters={}))
 cfg.bind_production_model('video_generation',model['id'])
 raw=cfg.get(model['id']);assert raw['base_url'] is None and raw['parameters']=={}
 runtime=cfg.resolve_production_model('video_generation');assert runtime['base_url']=='http://comfy:8188'
 assert runtime['parameters']['min_free_vram_mb']==30720 and runtime['parameters']['memory_check_seconds']==17
 assert runtime['parameters']['resource_group']=='gpu0' and runtime['parameters']['queue_retry_seconds']==7
 assert runtime['api_key']=='private-service-secret'
 snapshot=cfg.snapshot(runtime);assert snapshot['comfyui_api_config_id']==server['id']
 assert 'private-service-secret' not in str(snapshot) and 'private-service-secret' not in str(services.list())
 assert cfg.get(model['id'])==raw
 changed=payload(api_key=None).model_dump();changed.update(revision=1,min_free_vram_mb=28672,memory_check_seconds=9)
 services.save(ComfyApiInput.model_validate(changed),server['id'])
 assert cfg.resolve_production_model('video_generation')['parameters']['min_free_vram_mb']==28672
 assert cfg.get(model['id'])['revision']==1


def test_three_hour_video_timeout_survives_instance_resolution():
 from lumenx_prism.production_schemas import VideoParameters
 from pydantic import ValidationError
 assert VideoParameters(resource_group='gpu0').timeout_seconds==10800
 db=mongomock.MongoClient(tz_aware=True).db;s=ComfyApiConfigs(db);cfg=ModelConfigs(db)
 data=payload().model_dump();data['execution_timeout_seconds']=10800
 server=s.save(ComfyApiInput.model_validate(data))
 model=cfg.save(ModelConfigInput(name='video',kind='video',provider='comfyui',protocol='custom_http',model='minimax-h3-ref2va',comfyui_api_config_id=server['id'],parameters={}))
 runtime=cfg.resolve_production_model('video_generation',model['id'])
 assert VideoParameters.model_validate(runtime['parameters']).timeout_seconds==10800
 with pytest.raises(ValidationError):VideoParameters(resource_group='gpu0',timeout_seconds=10801)
 data['execution_timeout_seconds']=10801
 with pytest.raises(ValidationError):ComfyApiInput.model_validate(data)


def test_management_uses_only_enabled_instances_and_legacy_free_ids_are_aliases():
 db=mongomock.MongoClient(tz_aware=True).db;services=ComfyApiConfigs(db);cfg=ModelConfigs(db)
 server=services.save(payload());disabled=payload().model_dump();disabled.update(name='retired',base_url='http://retired:8190',enabled=False);services.save(ComfyApiInput.model_validate(disabled))
 model=cfg.save(ModelConfigInput(name='image',kind='image',provider='comfyui',protocol='custom_http',model='qwen-image-2.1',comfyui_api_config_id=server['id'],parameters={}))
 instances=ComfyControl(db).instances();assert len(instances)==1 and instances[0]['id']==server['id']
 assert model['id'] in instances[0]['model_config_ids']


def test_active_work_prevents_server_move_and_model_revision_is_not_rewritten():
 db=mongomock.MongoClient(tz_aware=True).db;s=ComfyApiConfigs(db);cfg=ModelConfigs(db);server=s.save(payload())
 m=cfg.save(ModelConfigInput(name='image',kind='image',provider='comfyui',protocol='custom_http',model='qwen-image-2.1',comfyui_api_config_id=server['id']))
 db.ai_jobs.insert_one({'_id':'live','status':'queued','model_config':{'id':m['id']}})
 data=payload().model_dump();data.update(base_url='http://moved:8188',revision=1)
 with pytest.raises(ConfigError,match='comfyui_config_in_use'):s.save(ComfyApiInput.model_validate(data),server['id'])
 data=payload().model_dump();data.update(revision=1,min_free_vram_mb=32000)
 s.save(ComfyApiInput.model_validate(data),server['id'])
 assert cfg.get(m['id'])['revision']==1


def test_disabled_peers_are_not_mandatory_and_cross_gpu_peers_are_rejected():
 db=mongomock.MongoClient(tz_aware=True).db;s=ComfyApiConfigs(db)
 old=payload().model_dump();old.update(base_url='http://retired:8190',enabled=False);old=s.save(ComfyApiInput.model_validate(old))
 data=payload().model_dump();data['peer_ids']=[old['id']];server=s.save(ComfyApiInput.model_validate(data))
 assert s.connection(s.get(server['id']))['parameters']['peer_base_urls']==[]
 data=payload().model_dump();data.update(base_url='http://other:8190',resource_group='other-gpu',peer_ids=[old['id']])
 with pytest.raises(ConfigError,match='comfyui_peer_resource_mismatch'):s.save(ComfyApiInput.model_validate(data))


def test_instance_http_auth_revision_and_secret_redaction(monkeypatch):
 from lumenx_prism.api import app,get_jobs
 from lumenx_prism.jobs import Jobs
 db=mongomock.MongoClient(tz_aware=True).db
 monkeypatch.setenv('AI_SERVICE_API_KEY','test-token');app.dependency_overrides[get_jobs]=lambda:Jobs(db)
 try:
  with TestClient(app) as c:
   data=payload(api_key='private-service-secret').model_dump(mode='json');data['api_key']='private-service-secret'
   assert c.post('/v1/comfyui-api-configs',json=data).status_code==401
   c.headers['Authorization']='Bearer test-token'
   response=c.post('/v1/comfyui-api-configs',json=data);assert response.status_code==201,response.text
   assert 'private-service-secret' not in response.text
   id=response.json()['id'];assert c.get('/v1/comfyui-api-configs/'+id).json()['api_key_set']
   bad=c.post('/v1/comfyui-api-configs',json={**data,'base_url':'http://user:private-service-secret@host'})
   assert bad.status_code==422 and 'private-service-secret' not in bad.text
   update=c.put('/v1/comfyui-api-configs/'+id,json={**data,'api_key':None,'revision':1});assert update.status_code==200
   assert c.put('/v1/comfyui-api-configs/'+id,json={**data,'revision':1}).status_code==409
 finally:app.dependency_overrides.clear()
