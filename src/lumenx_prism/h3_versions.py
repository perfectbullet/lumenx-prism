"""Stable task IDs with immutable H3 input versions and fenced deferred updates."""
import hashlib
from uuid import uuid4
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from .jobs import utcnow
from .model_configs import ConfigError,ModelConfigs
from .comfy_videos import snapshot

FIELDS=('request','effective_prompt','prompt_sha256','prompt_version','reference_snapshot','workflow_snapshot','model_config','provider','requested_model')


def initial(db,row):
    col=db.ai_job_input_versions
    col.create_index([('job_id',1),('key',1)],unique=True)
    version=row.get('input_revision',1)
    try:col.insert_one({'_id':row['_id']+':'+str(version),'job_id':row['_id'],'version':version,'key':'__initial__',
        'created_at':row['created_at'],'state':'applied','input':{k:row.get(k) for k in FIELDS}})
    except DuplicateKeyError:pass
    db.ai_jobs.update_one({'_id':row['_id'],'input_revision':{'$exists':False}},{'$set':{'input_revision':1}})
    db.ai_jobs.update_one({'_id':row['_id'],'revision_sequence':{'$exists':False}},{'$set':{'revision_sequence':version}})


def apply_pending(db,id):
    row=db.ai_jobs.find_one({'_id':str(id),'type':'video_generation','pending_update_id':{'$exists':True}})
    if not row or row['status']=='running' or db.ai_resources.find_one({'job_id':str(id)}):return False
    if row['status']=='queued' and (row.get('execution') or {}).get('state') in ('submitted','submitting'):return False
    if row['status']=='failed' and (row.get('execution') or {}).get('state') in ('submitted','submitting'):return False
    record=db.ai_job_input_versions.find_one({'_id':row['pending_update_id']})
    if not record:return False
    current=row.get('input_revision',1)
    previous={k:row.get(k) for k in ('status','error_code','execution_timing','execution','retry_events','finished_at')}
    previous['result']=row.get('result') or row.get('completed_result')
    db.ai_job_input_versions.update_one({'_id':str(id)+':'+str(current)},{'$set':{'previous_run':previous}})
    values={**record['input'],'input_revision':record['version'],'status':'queued','phase':'queued',
        'started_at':None,'finished_at':None,'duration_ms':None,'result':None,'error_code':None,
        'execution':None,'lease_until':None,'worker_id':None,'not_before':utcnow(),'audit_synced':False,
        'retry_submission_id':str(id)+':input:'+str(record['version'])+':'+str(uuid4())}
    changed=db.ai_jobs.update_one({'_id':str(id),'status':row['status'],'attempt':row.get('attempt',0),
        'pending_update_id':record['_id'],'input_revision':current},
        {'$set':values,'$unset':{'pending_update_id':'','pending_input_revision':'','completed_result':'','execution_timing':'','actual_model':'','usage':''}})
    if changed.modified_count:
        db.ai_job_input_versions.update_one({'_id':record['_id']},{'$set':{'state':'applied','applied_at':utcnow()}})
        return True
    return False


def update_task(repo,id,payload,key,expected,configs,media):
    db=repo.collection.database
    row=repo.collection.find_one({'_id':str(id),'type':'video_generation','request.prompt_mode':'raw'})
    if not row:raise ConfigError('h3_task_not_found',404)
    initial(db,row)
    record=db.ai_job_input_versions.find_one({'job_id':str(id),'key':key})
    digest=repo.digest('video_generation',payload)
    if record:
        if record.get('request_hash')!=digest:raise ConfigError('idempotency_conflict',409)
    else:
        row=repo.collection.find_one({'_id':str(id)})
        if row.get('input_revision',1)!=expected:raise ConfigError('input_revision_conflict',409)
        config=configs.resolve_production_model('video_generation',payload.model_config_id)
        refs=media.snapshot(payload.reference_media_ids)
        if any(r['kind']!='image' for r in refs):raise ConfigError('image_reference_required',422)
        for r in refs:media.read_verified(r['id'],r['sha256'])
        fresh={'request':payload.model_dump(mode='json'),'effective_prompt':payload.prompt,
            'prompt_sha256':hashlib.sha256(payload.prompt.encode()).hexdigest(),'prompt_version':'h3-ref2va-raw-v1',
            'reference_snapshot':refs,'workflow_snapshot':snapshot(),'model_config':ModelConfigs.snapshot(config),
            'provider':config['provider'],'requested_model':config['model']}
        counter=repo.collection.find_one_and_update({'_id':str(id),'input_revision':expected},
            {'$inc':{'revision_sequence':1}},return_document=ReturnDocument.AFTER)
        if not counter:raise ConfigError('input_revision_conflict',409)
        version=counter['revision_sequence'];record={'_id':str(id)+':'+str(version),'job_id':str(id),'version':version,
            'key':key,'created_at':utcnow(),'state':'pending','request_hash':digest,'input':fresh}
        try:db.ai_job_input_versions.insert_one(record)
        except DuplicateKeyError:record=db.ai_job_input_versions.find_one({'job_id':str(id),'key':key})
    version=record['version']
    result=repo.collection.update_one({'_id':str(id),'input_revision':{'$lt':version},'$or':[
        {'pending_input_revision':{'$exists':False}},{'pending_input_revision':{'$lte':version}}]},
        {'$set':{'pending_update_id':record['_id'],'pending_input_revision':version}})
    if result.matched_count:
        db.ai_job_input_versions.update_many({'job_id':str(id),'state':'pending','version':{'$lt':version}}, {'$set':{'state':'superseded'}})
        current=repo.collection.find_one({'_id':str(id)})
        if current['status']=='failed' and (current.get('execution') or {}).get('state') in ('submitting','submitted'):
            repo.recover_video(str(id))
        apply_pending(db,str(id))
    return repo.get(str(id))


def versions(db,id):
    row=db.ai_jobs.find_one({'_id':str(id),'type':'video_generation','request.prompt_mode':'raw'})
    if not row:raise ConfigError('h3_task_not_found',404)
    initial(db,row);current=row.get('input_revision',1);items=[]
    for item in db.ai_job_input_versions.find({'job_id':str(id)}).sort('version',-1):
        run={k:row.get(k) for k in ('status','result','error_code','execution_timing','finished_at')} if item['version']==current else {k:item.get('previous_run',{}).get(k) for k in ('status','result','error_code','execution_timing','finished_at')}
        items.append({'version':item['version'],'created_at':item['created_at'],'state':item['state'],
            'current':item['version']==current,'pending':item['version']==row.get('pending_input_revision'),
            'request':item['input']['request'],**run})
    return {'job_id':str(id),'current_version':current,'pending_version':row.get('pending_input_revision'),'items':items}


def sweep_pending(db):
    for row in db.ai_jobs.find({'type':'video_generation','pending_update_id':{'$exists':True},'status':{'$ne':'running'}},{'_id':1}).limit(100):
        apply_pending(db,row['_id'])
