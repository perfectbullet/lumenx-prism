"""Durable image work, serial candidates and recoverable ComfyUI submissions."""
from datetime import timedelta
import hashlib
import json
import threading
import time
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from .jobs import Jobs, utcnow
from .style_rules import adapt_visual_style
from .model_configs import ConfigError, ModelConfigs
from .image_schemas import ImageParameters
from .image_prompts import compile_image_prompt, PROMPT_VERSION, SKILL_REVISION
from .comfy_images import ComfyImages, build_workflow, workflow_snapshot


class ImageJobs(Jobs):
    def existing(self,key,request):
        row=self.collection.find_one({'idempotency_key':key})
        if row and (row.get('type')!='image_generation' or row['request_hash']!=self.fingerprint(request)):
            raise ConfigError('idempotency_conflict',409)
        return self.public(row) if row else None

    @staticmethod
    def fingerprint(request):
        value={'type':'image_generation','request':request.model_dump(mode='json')}
        return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

    def submit_image(self, request, key, config, media):
        self.ensure_indexes()
        existing=self.existing(key,request)
        if existing:return existing
        refs=media.snapshot(request.reference_media_ids)
        prompt,negative=compile_image_prompt(request)
        row=dict(_id=str(uuid4()), schema_version=1,type='image_generation',status='queued',phase='queued',
                 created_at=utcnow(),started_at=None,finished_at=None,duration_ms=None,
                 request=request.model_dump(mode='json'),request_hash=self.fingerprint(request),idempotency_key=key,
                 effective_prompt=prompt,negative_prompt=negative,prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                 prompt_version=PROMPT_VERSION,skill_revision=SKILL_REVISION,reference_snapshot=refs,
                 style_snapshot=adapt_visual_style(request.style, request.asset_type),
                 workflow_snapshot=workflow_snapshot(),model_config=ModelConfigs.snapshot(config),
                 provider=config['provider'],requested_model=config['model'],result=None,error_code=None,
                 executions=[],attempt=0,worker_id=None,lease_until=None)
        try:self.collection.insert_one(row)
        except DuplicateKeyError:return self.existing(key,request)
        return self.public(row)

    def claim_image(self,worker_id,lease_seconds):
        now=utcnow()
        row=self.collection.find_one_and_update({'type':'image_generation','$or':[
            {'status':'queued','$or':[{'not_before':{'$exists':False}},{'not_before':{'$lte':now}}]},
            {'status':'running','lease_until':{'$lte':now}}]},
            {'$set':{'status':'running','worker_id':worker_id,'claim_token':str(uuid4()),
                     'lease_until':now+timedelta(seconds=lease_seconds)},'$inc':{'attempt':1}},
            sort=[('created_at',1)],return_document=ReturnDocument.AFTER)
        if row and not row.get('started_at'):
            self.update_owned(row,started_at=now)
        return row

    def update_owned(self,job,**values):
        if self.collection.update_one(self.ownership(job),{'$set':values}).matched_count!=1:
            raise ConfigError('job_ownership_lost')
        job.update(values)

    def acquire(self,job,group):
        resources=self.collection.database['ai_resources']
        try:
            resources.update_one({'_id':group,'job_id':job['_id']},
                {'$setOnInsert':{'job_id':job['_id'],'created_at':utcnow()}},upsert=True)
            return True
        except DuplicateKeyError:return False

    def release(self,job,group):
        self.collection.database['ai_resources'].delete_one({'_id':group,'job_id':job['_id']})

    def defer(self,job):
        self.update_owned(job,status='queued',phase='waiting_for_resource',lease_until=None,
                          not_before=utcnow()+timedelta(seconds=5))


class ImageWorker:
    def __init__(self,jobs,configs,media,worker_id,lease_seconds=60,adapter_factory=ComfyImages):
        self.jobs=ImageJobs(jobs.collection.database)
        self.configs,self.media,self.worker_id=configs,media,worker_id
        self.lease_seconds,self.adapter_factory=lease_seconds,adapter_factory

    def run_once(self):
        job=self.jobs.claim_image(self.worker_id,self.lease_seconds)
        if not job:return False
        done,lost=threading.Event(),threading.Event()
        def heartbeat():
            while not done.wait(self.lease_seconds/3):
                try:
                    if self.jobs.heartbeat(job,self.lease_seconds):continue
                except Exception:pass
                lost.set();return
        thread=threading.Thread(target=heartbeat,daemon=True);thread.start()
        adapter=None; group=None; release=False; result=None; error=None; deferred=False
        def save(**values):
            if lost.is_set():raise ConfigError('job_ownership_lost')
            self.jobs.update_owned(job,**values)
        try:
            config=self.configs.get(job['model_config']['id'])
            self.configs.validate_image_model(config)
            if config['revision']!=job['model_config']['revision']:raise ConfigError('model_config_changed')
            if job['workflow_snapshot']!=workflow_snapshot():raise ConfigError('workflow_changed')
            params=ImageParameters.model_validate(config['parameters']);group=params.resource_group
            if not self.jobs.acquire(job,group):
                self.jobs.defer(job);deferred=True;return True
            release=not any(e.get('state') in ('submitting','submitted') for e in job['executions'])
            adapter=self.adapter_factory(config)
            if not job['executions']:
                adapter.preflight(bool(job['reference_snapshot']))
                if not adapter.idle():
                    self.jobs.defer(job);deferred=True;return True
            artifacts=[]
            for index in range(job['request']['batch_size']):
                if index < len(job['executions']):
                    execution=job['executions'][index]
                else:
                    # Persist exact reference upload order and workflow before any inference submission.
                    save(phase='uploading')
                    uploaded=[adapter.upload(self.media,ref) for ref in job['reference_snapshot']]
                    graph=build_workflow(job['request'],job['effective_prompt'],job['negative_prompt'],params,uploaded,job['_id'],index)
                    execution={'index':index,'state':'prepared','submission_id':f"{job['_id']}:{index}",'workflow':graph}
                    save(executions=job['executions']+[execution])
                if execution['state']=='completed':
                    artifacts.extend(execution['artifacts']);continue
                if execution['state']=='submitting':
                    pid=adapter.locate(execution['submission_id'])
                    if not pid:raise ConfigError('execution_outcome_unknown')
                    execution.update(state='submitted',prompt_id=pid);save(executions=job['executions'])
                if execution['state']=='prepared':
                    execution['state']='submitting';save(executions=job['executions'],phase='submitting')
                    release=False
                    # Never auto retry POST /prompt after a timeout or response persistence failure.
                    pid=adapter.submit(execution['workflow'],execution['submission_id'])
                    execution.update(state='submitted',prompt_id=pid);save(executions=job['executions'],phase='inference')
                deadline=time.monotonic()+params.timeout_seconds
                while time.monotonic()<deadline:
                    if lost.is_set():raise ConfigError('job_ownership_lost')
                    row=adapter.history(execution['prompt_id'])
                    if row and row.get('status',{}).get('status_str')=='error':
                        release=True;raise ConfigError('comfy_execution_failed',502)
                    if row and row.get('status',{}).get('completed'):
                        if row['status'].get('status_str')!='success':
                            release=True;raise ConfigError('comfy_execution_failed',502)
                        break
                    done.wait(params.poll_seconds)
                else:raise ConfigError('comfy_execution_timeout')
                release=True
                save(phase='downloading')
                descriptors=row.get('outputs',{}).get('43',{}).get('images',[])
                if len(descriptors)!=1:raise ConfigError('comfy_output_invalid',502)
                outputs=[]
                for n,descriptor in enumerate(descriptors):
                    for attempt in range(3):
                        try:
                            content=adapter.download(descriptor)
                            artifact=self.media.put(content,job_id=job['_id'],artifact_key=f'{index}:{n}')
                            break
                        except Exception:
                            if attempt==2:raise
                            done.wait(1)
                    artifact.update(candidate_index=index,generation_type=job['request']['generation_type'],
                                    download_url='/v1/media/'+artifact['id']+'/download')
                    outputs.append(artifact)
                execution.update(state='completed',artifacts=outputs)
                save(executions=job['executions']);artifacts.extend(outputs)
            result={'artifacts':artifacts,'review_required':True,'prompt_version':job['prompt_version'],
                    'character_card':job['request'].get('character_card')}
        except ConfigError as exc:
            error=exc.code
            if error=='job_ownership_lost':lost.set()
        except Exception:
            error='image_execution_error'
        finally:
            try:
                if not lost.is_set() and not deferred:
                    # Retry result persistence, never inference. Expired image claims resume provider IDs.
                    for attempt in range(3):
                        try:
                            if self.jobs.finish(job,result=result,error_code=error,actual_model=job['requested_model']):
                                break
                        except Exception:
                            if attempt<2:done.wait(1)
                if group and release and not lost.is_set():self.jobs.release(job,group)
                self.jobs.mirror_attempts()
            finally:
                done.set();thread.join()
                if adapter:adapter.close()
        return True
