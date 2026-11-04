"""Long-running recoverable GPU jobs, independent of text consumption."""
import threading
import logging
import httpx
import time
from .production_jobs import ProductionJobs
from .production_schemas import BoardInput,VideoParameters
from .storyboards import parse_board,validate_board
from .comfy_videos import ComfyVideos,build,snapshot
from .llm_adapter import LLMAdapter
from .model_configs import ConfigError
from .video_media import put_video,assemble

class ProductionWorker:
    def __init__(self,jobs,configs,media,worker_id,kinds,lease_seconds=60,adapter_factory=ComfyVideos,llm_factory=LLMAdapter):
        self.jobs=ProductionJobs(jobs.collection.database);self.configs=configs;self.media=media
        self.worker_id=worker_id;self.kinds=kinds;self.lease_seconds=lease_seconds
        self.adapter_factory=adapter_factory;self.llm_factory=llm_factory
    def reconcile_video_fence(self):
        if 'video_generation' not in self.kinds:return
        now=time.monotonic()
        if now<getattr(self,'_next_video_fence_check',0):return
        self._next_video_fence_check=now+10
        db=self.jobs.collection.database
        for lock in db.ai_resources.find({}):
            row=self.jobs.collection.find_one({'_id':lock.get('job_id'),'type':'video_generation','status':'failed'})
            if not row:continue
            ex=row.get('execution') or {}
            if ex.get('state') not in ('submitting','submitted'):continue
            adapter=None
            try:
                config=self.configs.resolve_production_model('video_generation',row['model_config']['id'])
                if config['revision']!=row['model_config']['revision']:continue
                adapter=self.adapter_factory(config)
                pid=ex.get('prompt_id') or adapter.locate(ex['submission_id'])
                if not pid:continue
                history=adapter.history(pid)
                state=(history or {}).get('status',{})
                if state.get('completed') or state.get('status_str')=='error':
                    self.jobs.recover_video(row['_id'])
            except Exception:
                # A failed probe cannot prove the GPU is idle; keep the fence.
                pass
            finally:
                if adapter:adapter.close()

    def run_once(self):
        if 'video_generation' in self.kinds:
            from .h3_versions import sweep_pending
            sweep_pending(self.jobs.collection.database)
        self.reconcile_video_fence()
        job=self.jobs.claim_production(self.worker_id,self.kinds,self.lease_seconds)
        if not job:return False
        done,lost=threading.Event(),threading.Event();adapter=None;group=None;release=False;deferred=False;error=None;result=None
        def heartbeat():
            while not done.wait(self.lease_seconds/3):
                try:
                    if self.jobs.heartbeat(job,self.lease_seconds):continue
                except Exception:pass
                lost.set();return
        def save(**values):
            if lost.is_set():raise ConfigError('job_ownership_lost')
            self.jobs.update_owned(job,**values)
        thread=threading.Thread(target=heartbeat,daemon=True);thread.start()
        try:
            if job.get('completed_result'):
                result=job['completed_result']
            elif job['type']=='video_assembly':
                save(phase='assembling');result={'artifacts':[assemble(self.media,job['reference_snapshot'],job['_id'])],'review_required':True}
            else:
                config=self.configs.get(job['model_config']['id']) if job['type']=='work_entities' else self.configs.resolve_production_model('storyboard_generation' if job['type'] in ('script_planning','work_breakdown','work_entities') else job['type'],job['model_config']['id'])
                if job['type']=='work_entities':self.configs.validate_entity_model(config)
                if config['revision']!=job['model_config']['revision']:raise ConfigError('model_config_changed')
                if job['type'] in ('storyboard_generation','image_polish','script_planning','work_breakdown','work_entities'):
                    raw=job.get('raw_model_output')
                    if raw is None:
                        save(phase='inference');adapter=self.llm_factory(config,timeout=300 if job['type'] in ('script_planning','work_breakdown','work_entities') or job['request'].get('scene_context') else 180,max_retries=0)
                        raw=adapter.chat(messages=[{'role':'user','content':job['effective_prompt']}])
                    save(raw_model_output=raw,phase='validating')
                    if job['type']=='work_entities':
                        from .work_assets import WorkEntityInput,parse_entities
                        result={'content':parse_entities(raw,WorkEntityInput.model_validate(job['request'])).model_dump(mode='json'),'review_required':True}
                    elif job['type']=='work_breakdown':
                        from .works import BreakdownInput,parse_breakdown
                        result={'content':parse_breakdown(raw,BreakdownInput.model_validate(job['request'])).model_dump(mode='json'),'review_required':True}
                    elif job['type']=='script_planning':
                        from .script_plans import PlanInput,parse_plan,declared_duration
                        request=PlanInput.model_validate(job['request']);plan=parse_plan(raw,request)
                        source_time=declared_duration(request.script)
                        if request.work_context:
                            from .works import Works
                            ep=Works(self.jobs.collection.database).episode(request.work_context)['episode']
                            source_time=ep.get('source_duration')
                        result={'content':plan.model_dump(mode='json'),'declared_duration':source_time,'review_required':True}
                    elif job['type']=='image_polish':
                        from .image_polish import ImagePolishInput,parse_polish
                        polished=parse_polish(raw,ImagePolishInput.model_validate(job['request']))
                        result={'suggestion':polished.model_dump(mode='json'),'review_required':True}
                    else:
                        board=BoardInput.model_validate(job['request']);content=parse_board(raw,board)
                        result={'content':content.model_dump(mode='json'),'checks':validate_board(content,board)}
                else:
                    if job['workflow_snapshot']!=snapshot():raise ConfigError('workflow_changed')
                    params=VideoParameters.model_validate(config['parameters']);group=params.resource_group
                    if not self.jobs.acquire(job,group):
                        self.jobs.defer(job,retry_seconds=params.queue_retry_seconds);deferred=True;return True
                    ex=job.get('execution');release=not ex or ex['state']=='prepared'
                    adapter=self.adapter_factory(self.configs.runtime_config(config))
                    if not ex or ex.get('state')=='prepared':
                        adapter.preflight()
                        if not adapter.idle():
                            self.jobs.defer(job,reason='comfy_busy',retry_seconds=params.queue_retry_seconds);deferred=True;return True
                    if not ex:
                        save(phase='uploading',waiting_reason=None,memory_wait=None)
                        uploaded=[adapter.upload(self.media,r) for r in job['reference_snapshot']]
                        ex={'state':'prepared','submission_id':job.get('retry_submission_id') or job['_id'],
                            'workflow':build(job['request'],job['effective_prompt'],params,uploaded,job['_id'])}
                        save(execution=ex)
                    if ex['state']=='submitting':
                        pid=adapter.locate(ex['submission_id'])
                        if not pid:raise ConfigError('execution_outcome_unknown')
                        ex.update(state='submitted',prompt_id=pid);save(execution=ex)
                    if ex['state']=='prepared':
                        from .comfy_control import GPUControl
                        if GPUControl(self.jobs.collection.database).paused():
                            self.jobs.defer(job,reason='gpu_paused',retry_seconds=params.queue_retry_seconds);deferred=True;return True
                        ex['state']='submitting';save(execution=ex,phase='submitting');release=False
                        pid=adapter.submit(ex['workflow'],ex['submission_id'])
                        ex.update(state='submitted',prompt_id=pid);save(execution=ex,phase='inference')
                    deadline=time.monotonic()+params.timeout_seconds
                    while time.monotonic()<deadline:
                        if lost.is_set():raise ConfigError('job_ownership_lost')
                        history=adapter.history(ex['prompt_id'])
                        from .execution_timing import from_history,aggregate
                        timing=from_history(history)
                        if timing:
                            ex['timing']=timing
                            save(execution=ex,execution_timing=aggregate([{'prompt_id':ex['prompt_id'],**timing}],1))
                        if history and history.get('status',{}).get('status_str')=='error':
                            release=True;raise ConfigError('comfy_execution_failed',502)
                        if history and history.get('status',{}).get('completed'):
                            release=True
                            if history['status'].get('status_str')!='success':raise ConfigError('comfy_execution_failed',502)
                            break
                        done.wait(params.poll_seconds)
                    else:raise ConfigError('comfy_execution_timeout')
                    save(phase='downloading')
                    outputs=history.get('outputs',{}).get('92',{})
                    descriptors=outputs.get('images') or outputs.get('videos') or outputs.get('gifs') or []
                    if len(descriptors)!=1:raise ConfigError('comfy_output_invalid',502)
                    for attempt in range(3):
                        try:
                            artifact=put_video(self.media,adapter.download(descriptors[0]),job['_id'])
                            break
                        except Exception:
                            if attempt==2:raise
                            done.wait(1)
                    if not artifact['has_audio']:raise ConfigError('video_audio_missing',502)
                    result={'artifacts':[artifact],'review_required':True}
                    if job['request'].get('prompt_mode')=='raw':
                        result['requested_duration']=job['request']['duration_seconds']
                    else:
                        result.update(shot_id=job['request']['shot_id'],requested_duration=job['request']['shot']['duration'])
            save(completed_result=result,phase='finalizing')
        except ConfigError as exc:
            error=exc.code
            if error=='video_gpu_memory_not_released' and job['type']=='video_generation' and (job.get('execution') or {}).get('state') not in ('submitting','submitted'):
                memory=getattr(adapter,'memory_status',None)
                self.jobs.defer(job,reason='insufficient_vram',retry_seconds=params.memory_check_seconds,memory=memory if isinstance(memory,dict) else None)
                deferred=True;error=None
            if error=='comfy_peer_unavailable' and job['type']=='video_generation' and (job.get('execution') or {}).get('state') not in ('submitting','submitted'):
                self.jobs.defer(job,reason='comfy_peer_unavailable',retry_seconds=params.memory_check_seconds)
                deferred=True;error=None
            if error=='job_ownership_lost':lost.set()
        except httpx.TransportError as exc:
            error='comfy_service_unavailable'
            if job['type']=='video_generation' and (job.get('execution') or {}).get('state') not in ('submitting','submitted'):
                self.jobs.defer(job,reason=error,retry_seconds=params.memory_check_seconds);deferred=True;error=None
            logging.getLogger(__name__).error('Upstream connection task=%s phase=%s exception=%s',job['_id'],job.get('phase'),type(exc).__name__)
        except Exception as exc:
            error='production_execution_error'
            logging.getLogger(__name__).error('Production task=%s phase=%s exception=%s',job['_id'],job.get('phase'),type(exc).__name__)
            if not lost.is_set():
                try:save(error_context={'phase':job.get('phase'),'exception_type':type(exc).__name__})
                except ConfigError:lost.set()
        finally:
            try:
                if not lost.is_set() and not deferred:
                    for attempt in range(3):
                        try:
                            finished=self.jobs.finish(job,result=result,error_code=error,actual_model=getattr(adapter,'actual_model',None) or job['requested_model'],usage=getattr(adapter,'usage',None))
                            if not finished:lost.set()
                            break
                        except Exception:
                            if attempt<2:done.wait(1)
                if group and release and not lost.is_set():self.jobs.release(job,group)
                if job['type']=='video_generation' and not lost.is_set() and not deferred:
                    from .h3_versions import apply_pending
                    apply_pending(self.jobs.collection.database,job['_id'])
            finally:
                done.set();thread.join()
                if adapter:
                    if hasattr(adapter,'close'):adapter.close()
                    elif getattr(adapter,'_client',None):adapter._client.close()
        return True
