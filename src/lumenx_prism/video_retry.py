"""Retry failed video work in place, preserving queue order and inference fences."""
from uuid import uuid4
from .jobs import utcnow
from .model_configs import ConfigError
from .comfy_videos import ComfyVideos


def retry_video(repo,id,key,configs,media,adapter_factory=ComfyVideos):
    row=repo.collection.find_one({'_id':str(id),'type':'video_generation'})
    if not row:raise ConfigError('job_not_found',404)
    if row.get('last_retry_key')==key:return repo.public(row)
    if row['status'] in ('queued','running'):return repo.public(row)
    if row['status']!='failed':raise ConfigError('only_failed_video_can_retry',409)
    config=configs.resolve_production_model('video_generation',row['model_config']['id'])
    if config['revision']!=row['model_config']['revision']:raise ConfigError('model_config_changed',409)
    for reference in row['reference_snapshot']:media.read_verified(reference['id'],reference['sha256'])
    ex=row.get('execution');mode='rerun'
    if ex and ex.get('state') in ('submitting','submitted'):
        adapter=adapter_factory(config)
        try:
            pid=ex.get('prompt_id') or adapter.locate(ex['submission_id'])
            if not pid:raise ConfigError('previous_video_outcome_unknown',409)
            history=adapter.history(pid)
            if history and history.get('status',{}).get('status_str')=='error':mode='rerun'
            else:
                # Absence from history does not prove that the upstream execution is absent.
                # Preserve its prompt ID and let the worker wait/recover; never duplicate submission.
                mode='recover'
                ex={**ex,'state':'submitted','prompt_id':pid}
        finally:adapter.close()
    if row.get('completed_result'):mode='recover'
    stamp=utcnow();event={'at':stamp,'mode':mode,'attempt':row.get('attempt',0),
        'error_code':row.get('error_code'),'finished_at':row.get('finished_at'),
        'execution':row.get('execution'),'result':row.get('result'),'execution_timing':row.get('execution_timing')}
    fields={'status':'queued','phase':'recovering' if mode=='recover' else 'queued',
        'error_code':None,'finished_at':None,'lease_until':None,'worker_id':None,
        'last_retry_key':key,'last_retry_at':stamp,'retry_mode':mode,'audit_synced':False,'not_before':stamp}
    unset={}
    if mode=='recover':
        if ex:fields['execution']=ex
    else:
        fields.update(execution=None,result=None,started_at=None,duration_ms=None,
            retry_submission_id=str(id)+':retry:'+str(uuid4()))
        unset={'completed_result':'','actual_model':'','usage':'','execution_timing':''}
    update={'$set':fields,'$inc':{'retry_count':1},'$push':{'retry_events':event}}
    if unset:update['$unset']=unset
    repo.collection.update_one({'_id':row['_id'],'status':'failed','attempt':row.get('attempt',0),
        'last_retry_key':row.get('last_retry_key')},update)
    return repo.get(str(id))
