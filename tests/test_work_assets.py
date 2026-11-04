import json
from copy import deepcopy
from unittest.mock import Mock
from uuid import uuid4
import mongomock
import pytest
from lumenx_prism.work_assets import WorkAssets,EntityProposals,ConfirmEntities,BatchRequest,SelectCandidate,advance_batches,parse_entities
from lumenx_prism.works import Works,WorkCreate,EpisodeSelection,BreakdownOptions
from lumenx_prism.model_configs import ConfigError,ModelConfigs,ModelConfigInput
from lumenx_prism.production_jobs import ProductionJobs
from lumenx_prism.production_worker import ProductionWorker

@pytest.fixture
def env():
 db=mongomock.MongoClient(tz_aware=True).db;works=Works(db);store=WorkAssets(db);cfg=ModelConfigs(db);jobs=ProductionJobs(db)
 text=cfg.save(ModelConfigInput(name='text',kind='text',provider='mock',protocol='openai_chat',model='test',base_url='http://mock',api_key='secret'))
 cfg.bind_entity_model(text['id']);cfg.bind_production_model('image_polish',text['id'])
 image=cfg.save(ModelConfigInput(name='image',kind='image',provider='comfyui',protocol='custom_http',model='qwen-image-2.1',base_url='http://mock',parameters={'resource_group':'gpu0'}));cfg.bind_image_model(image['id'])
 w=works.create(WorkCreate(title='测试',kind='script',text='小林穿灰衣进门。\n小林穿灰衣回家。'));edition=str(uuid4())
 db.prism_work_editions.insert_one({'_id':edition,'work_id':w['id'],'content':{'episodes':[{'id':'E01','title':'上班','start_line':1,'end_line':1},{'id':'E02','title':'回家','start_line':2,'end_line':2}],'shared_context':'同一人物','selected_style':'写实'},'specification':{},'source_sha256':w['source_sha256']})
 selection=EpisodeSelection(edition_id=edition,episode_ids=['E01','E02'])
 data={'entities':[{'kind':'character','name':'小林','new_identity_key':'lin','description':'小林穿灰衣','costume':'灰衣','identity_traits':['小林'],'evidence':[{'episode_id':'E01','quote':'小林穿灰衣进门。'},{'episode_id':'E02','quote':'小林穿灰衣回家。'}]}],'warnings':[]}
 return db,store,cfg,jobs,w,selection,data

def complete(env,key='entity'):
 db,store,cfg,jobs,w,selection,data=env;r=store.request(w['id'],selection);j=jobs.submit_new('work_entities',r,key,cfg,None)
 llm=Mock();llm.chat.return_value=json.dumps(data);llm.actual_model='test';llm.usage=None
 ProductionWorker(jobs,cfg,None,'text',['work_entities'],llm_factory=lambda *a,**k:llm).run_once()
 assert jobs.get(j['id'])['status']=='succeeded'
 return j,store.confirm(w['id'],ConfirmEntities(source_job_id=j['id'],content=EntityProposals(**data)))['items']

def test_multi_episode_confirm_reuses_one_appearance_and_evidence(env):
 db,store,cfg,jobs,w,s,data=env;j,rows=complete(env)
 assert len(rows)==1 and len(rows[0]['appearances'])==2
 for e in ['E01','E02']:assert store.list(w['id'],s.edition_id,[e])[0]['id']==rows[0]['id']
 assert store.confirm(w['id'],ConfirmEntities(source_job_id=j['id'],content=EntityProposals(**data)))['items'][0]['id']==rows[0]['id']
 assert db.prism_work_assets.count_documents({})==1

def test_reanalysis_explicit_registry_reference_preserves_selected(env):
 db,store,cfg,jobs,w,s,data=env;j,rows=complete(env);old=rows[0]
 db.prism_work_variants.update_one({'_id':old['id']},{'$set':{'selected':{'manual':'keep'}}})
 data['entities'][0].update(asset_id=old['asset_id'],variant_id=old['id'])
 _,again=complete(env,'reextract')
 assert again[0]['id']==old['id'] and again[0]['selected']=={'manual':'keep'}
 assert db.prism_work_variants.count_documents({})==1

def test_new_costume_same_new_identity_key(env):
 db,store,cfg,jobs,w,s,data=env
 other=deepcopy(data['entities'][0]);other['costume']='外套';other['variant_name']='外套版';data['entities'].append(other)
 _,rows=complete(env)
 assert rows[0]['asset_id']==rows[1]['asset_id'] and rows[0]['id']!=rows[1]['id']

def test_invalid_evidence_or_cross_work_reference_rejected(env):
 db,store,cfg,jobs,w,s,data=env;r=store.request(w['id'],s)
 data['entities'][0]['evidence'][0]['quote']='并不存在'
 with pytest.raises(ConfigError,match='evidence'):parse_entities(json.dumps(data),r)
 data['entities'][0]['evidence'][0]['quote']='小林穿灰衣进门。';data['entities'][0]['asset_id']=str(uuid4())
 with pytest.raises(ConfigError,match='reference'):parse_entities(json.dumps(data),r)

def setup_batch(env):
 db,store,cfg,jobs,w,s,data=env;_,rows=complete(env);v=rows[0]
 batch=store.batch(w['id'],BatchRequest(**s.model_dump(),variant_ids=[v['id']]),'batch')
 media=Mock();media.snapshot.return_value=[]
 return batch,v,media

def test_batch_polish_then_four_images_and_first_selected_without_overwrite(env):
 db,store,cfg,jobs,w,s,data=env;b,v,media=setup_batch(env)
 advance_batches(db,cfg,media);p=db.ai_jobs.find_one({'type':'image_polish'})
 assert p and db.ai_jobs.count_documents({'type':'image_generation'})==0
 suggestion={'description':'小林穿灰衣','character_card':{**p['request']['character_card']},'warnings':[]}
 db.ai_jobs.update_one({'_id':p['_id']},{'$set':{'status':'succeeded','result':{'suggestion':suggestion}}})
 advance_batches(db,cfg,media);image=db.ai_jobs.find_one({'type':'image_generation'})
 assert image['request']['batch_size']==4
 outputs=[{'id':str(uuid4()),'download_url':'/media/'+str(i)} for i in range(4)]
 db.ai_jobs.update_one({'_id':image['_id']},{'$set':{'status':'succeeded','result':{'artifacts':outputs}}})
 advance_batches(db,cfg,media);row=store.list(w['id'])[0]
 assert row['selected']['media']['id']==outputs[0]['id'] and row['selected']['automatic']
 manual=store.choose(w['id'],v['id'],SelectCandidate(job_id=image['_id'],media_id=outputs[2]['id'],revision=row['revision']))
 assert not manual['selected']['automatic']
 again=store.batch(w['id'],BatchRequest(**s.model_dump()),'batch-again')
 assert again['items']==[];advance_batches(db,cfg,media)
 assert store.list(w['id'])[0]['selected']['media']['id']==outputs[2]['id']
 assert db.ai_jobs.count_documents({'type':'image_generation'})==1

def test_duplicate_batches_reserve_same_missing_asset_only_once(env):
 db,store,cfg,jobs,w,s,data=env;b,v,media=setup_batch(env)
 assert store.batch(w['id'],BatchRequest(**s.model_dump(),variant_ids=[v['id']]),'batch')['id']==b['id']
 advance_batches(db,cfg,media)
 b2=store.batch(w['id'],BatchRequest(**s.model_dump()),'second')
 # Move the original to the back of the scheduler for this deterministic contention check.
 db.prism_image_batches.update_one({'_id':b['id']},{'$set':{'lease_until':__import__('datetime').datetime.now(__import__('datetime').timezone.utc)+__import__('datetime').timedelta(hours=1)}})
 advance_batches(db,cfg,media)
 assert db.ai_jobs.count_documents({'type':'image_polish'})==1
 assert db.prism_image_batches.find_one({'_id':b2['id']})['items'][0]['reason']=='already_generating'

def test_failed_polish_never_generates_and_new_batch_can_retry(env):
 db,store,cfg,jobs,w,s,data=env;b,v,media=setup_batch(env);advance_batches(db,cfg,media)
 db.ai_jobs.update_one({'type':'image_polish'},{'$set':{'status':'failed','error_code':'upstream_error'}})
 advance_batches(db,cfg,media)
 assert db.ai_jobs.count_documents({'type':'image_generation'})==0
 assert store.list(w['id'])[0]['active_batch'] is None
 assert db.prism_image_batches.find_one({'_id':b['id']})['status']=='failed'
 store.batch(w['id'],BatchRequest(**s.model_dump()),'retry');advance_batches(db,cfg,media)
 assert db.ai_jobs.count_documents({'type':'image_polish'})==2

def test_timing_default_is_not_fixed_300():
 assert BreakdownOptions().episode_duration is None


def test_image_history_episode_filter_includes_reused_variant(env):
 db,store,cfg,jobs,w,selection,data=env
 _,rows=complete(env);variant=rows[0]
 for key,context in [('reused',{'work_id':w['id'],'edition_id':str(selection.edition_id),'variant_id':variant['id']}),('manual',{'work_id':w['id'],'edition_id':str(selection.edition_id),'episode_ids':'E01,E02'}),('other',{'work_id':w['id'],'edition_id':str(selection.edition_id),'episode_ids':'E03'})]:
  db.ai_jobs.insert_one({'_id':key,'idempotency_key':key,'type':'image_generation','request':{'name':'小林','source_context':context}})
 result=jobs.list(30,0,job_type='image_generation',work_id=w['id'],edition_id=selection.edition_id,episode_ids='E02')
 assert {r['id'] for r in result['items']}=={'reused','manual'}
 assert all(r['display_title']=='小林' and 'request' not in r for r in result['items'])


def test_image_history_counts_search_and_pagination_are_scoped(env):
 db,store,cfg,jobs,w,selection,data=env
 for i,(kind,name,episode) in enumerate([('character','周屿','E01'),('scene','办公室','E01'),('prop','周屿的杯子','E01'),('prop','其他作品杯子','E02')]):
  db.ai_jobs.insert_one({'_id':str(i),'idempotency_key':'history'+str(i),'type':'image_generation','request':{'name':name,'asset_type':kind,'generation_type':'image','source_context':{'work_id':w['id'],'edition_id':str(selection.edition_id),'episode_ids':episode}}})
 def query(**kwargs):return jobs.list(1,0,job_type='image_generation',work_id=w['id'],edition_id=selection.edition_id,episode_ids='E01',**kwargs)
 result=query(asset_type='prop',name_query='周屿')
 assert result['total']==1 and result['items'][0]['display_title']=='周屿的杯子'
 assert result['asset_counts']=={'all':2,'character':1,'scene':0,'prop':1}
 assert result['items'][0]['asset_type']=='prop'
 assert query()['has_more'] and query()['total']==3
 assert query(name_query='.*')['total']==0 # Search is literal, never a user regex.


def test_entity_history_filters_episode_overlap_and_returns_readable_scope(env):
 from lumenx_prism.jobs import utcnow
 db,store,cfg,jobs,w,selection,data=env
 for key,ids,work,edition in [('both',['E01','E02'],w['id'],str(selection.edition_id)),('second',['E02'],w['id'],str(selection.edition_id)),('other',['E03'],w['id'],str(selection.edition_id)),('wrong-work',['E01'],'other',str(selection.edition_id)),('wrong-edition',['E01'],w['id'],'other')]:
  db.ai_jobs.insert_one({'_id':key,'type':'work_entities','status':'succeeded','created_at':utcnow(),'request':{'work_id':work,'edition_id':edition,'episode_ids':ids,'registry':[{'private':'not returned'}]},'raw_model_output':'not returned'})
 rows=store.history(w['id'],selection.edition_id,['E01'])['items']
 assert [j['id'] for j in rows]==['both']
 assert rows[0]['episode_ids']==['E01','E02']
 assert 'request' not in rows[0] and 'raw_model_output' not in rows[0]
 assert {j['id'] for j in store.history(w['id'],selection.edition_id,['E02'])['items']}=={'both','second'}
