import httpx,mongomock
from lumenx_prism.execution_timing import from_history,aggregate,refresh_many


def history(start,end):return {'status':{'completed':True,'messages':[['execution_start',{'timestamp':start}],['execution_success',{'timestamp':end}]]}}


def test_native_timing_excludes_queue_and_counts_whole_workflow():
 result=from_history(history(100000,112500));assert result['duration_ms']==12500
 assert from_history({'status':{'messages':[['execution_start',{'timestamp':100}]]}}) is None
 assert from_history(history(200,100)) is None
 assert from_history(history(float('nan'),200)) is None


def test_multi_image_execution_sums_each_unique_prompt_not_wall_time():
 a={'prompt_id':'a',**from_history(history(1000,2000))};b={'prompt_id':'b',**from_history(history(10000,12000))}
 result=aggregate([a,a,b],2);assert result['duration_ms']==3000 and result['complete']
 assert aggregate([a],2)['complete'] is False


def test_timing_cache_uses_only_readonly_provider_history_and_survives_late_updates():
 db=mongomock.MongoClient(tz_aware=True).db
 db.model_comfigs.insert_one({'_id':'cfg','revision':1,'base_url':'http://test'})
 db.ai_jobs.insert_one({'_id':'timing-test','type':'video_generation','status':'succeeded','attempt':1,'request':{},'model_config':{'id':'cfg','revision':1},'execution':{'prompt_id':'prompt'},'duration_ms':999999})
 calls=[]
 def handle(r):calls.append(r);return httpx.Response(200,json={'prompt':history(100000,112500)})
 refresh_many(db,['timing-test'],lambda **kw:httpx.Client(transport=httpx.MockTransport(handle),**kw))
 row=db.ai_jobs.find_one({'_id':'timing-test'});assert row['execution_timing']['duration_ms']==12500
 assert row['duration_ms']==999999 and all(r.method=='GET' for r in calls)
