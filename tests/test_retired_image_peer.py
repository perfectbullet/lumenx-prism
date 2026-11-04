from copy import deepcopy
from unittest.mock import Mock
import httpx
import pytest
from test_production import setup
from test_h3_reference import request_for
from lumenx_prism.comfy_images import ComfyImages
from lumenx_prism.model_configs import ConfigError,ModelConfigInput
from lumenx_prism.production_worker import ProductionWorker


def test_only_old_unbound_managed_images_without_live_jobs_are_optional(setup):
 db,jobs,cfg,media,*_=setup
 old=cfg.save(ModelConfigInput(name='old image',kind='image',provider='comfyui',protocol='custom_http',model='qwen-image-2.1',base_url='http://old-image:8190',parameters={'resource_group':'gpu0'}))
 new=cfg.save(ModelConfigInput(name='new image',kind='image',provider='comfyui',protocol='custom_http',model='qwen-image-2.1',base_url='http://new-image:8190',parameters={'resource_group':'gpu1'}))
 cfg.bind_image_model(new['id'])
 video=cfg.resolve_production_model('video_generation');video['parameters']['peer_base_urls']=['http://old-image:8190','http://unknown-peer:8190']
 original=deepcopy(video);runtime=cfg.runtime_config(video)
 assert runtime['_optional_peer_base_urls']==['http://old-image:8190']
 assert video==original
 db.ai_jobs.insert_one({'_id':'old-live','type':'image_generation','status':'queued','model_config':{'id':old['id']}})
 assert cfg.runtime_config(video)['_optional_peer_base_urls']==[]


def test_optional_disconnected_peer_is_skipped_but_unknown_peer_is_not(monkeypatch):
 original_client=httpx.Client;calls=[]
 def handler(req):calls.append(req);raise httpx.ConnectError('offline',request=req)
 adapter=ComfyImages({'base_url':'http://target','parameters':{'resource_group':'gpu0','peer_base_urls':['http://old:8190']},'_optional_peer_base_urls':['http://old:8190']})
 adapter.json=lambda *a,**kw:{'queue_running':[],'queue_pending':[]}
 monkeypatch.setattr('lumenx_prism.comfy_images.httpx.Client',lambda **kw:original_client(transport=httpx.MockTransport(handler),**kw))
 try:
  assert adapter.idle() is True
  adapter.optional_peer_base_urls=set()
  with pytest.raises(ConfigError,match='comfy_peer_unavailable'):adapter.idle()
  assert all(req.method=='GET' for req in calls)
 finally:adapter.close()


def test_optional_online_busy_peer_still_prevents_inference(monkeypatch):
 original_client=httpx.Client
 def handler(req):return httpx.Response(200,json={'queue_running':[['busy']],'queue_pending':[]})
 adapter=ComfyImages({'base_url':'http://target','parameters':{'resource_group':'gpu0','peer_base_urls':['http://old:8190']},'_optional_peer_base_urls':['http://old:8190']})
 adapter.json=lambda *a,**kw:{'queue_running':[],'queue_pending':[]}
 monkeypatch.setattr('lumenx_prism.comfy_images.httpx.Client',lambda **kw:original_client(transport=httpx.MockTransport(handler),**kw))
 try:assert adapter.idle() is False
 finally:adapter.close()


def test_unavailable_required_peer_waits_instead_of_generic_failure(setup):
 db,jobs,cfg,media,*_=setup
 j=jobs.submit_new('video_generation',request_for(media),'peer-unavailable',cfg,media)
 adapter=Mock();adapter.idle.side_effect=ConfigError('comfy_peer_unavailable',503)
 ProductionWorker(jobs,cfg,media,'peer-test',['video_generation'],adapter_factory=lambda _:adapter).run_once()
 result=jobs.get(j['id']);assert result['status']=='queued' and result['waiting_reason']=='comfy_peer_unavailable'
 assert db.ai_resources.count_documents({})==0
 adapter.submit.assert_not_called()


def test_optional_peer_timeout_is_not_mistaken_for_a_stopped_service(monkeypatch):
 original_client=httpx.Client
 def handler(req):raise httpx.ReadTimeout('unresponsive',request=req)
 adapter=ComfyImages({'base_url':'http://target','parameters':{'resource_group':'gpu0','peer_base_urls':['http://old:8190']},'_optional_peer_base_urls':['http://old:8190']})
 adapter.json=lambda *a,**kw:{'queue_running':[],'queue_pending':[]}
 monkeypatch.setattr('lumenx_prism.comfy_images.httpx.Client',lambda **kw:original_client(transport=httpx.MockTransport(handler),**kw))
 try:
  with pytest.raises(ConfigError,match='comfy_peer_unavailable'):adapter.idle()
 finally:adapter.close()
