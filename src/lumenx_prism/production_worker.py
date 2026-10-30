"""Long-running recoverable GPU jobs, independent of text consumption."""
import threading
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
    def run_once(self):
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
                config=self.configs.resolve_production_model(job['type'],job['model_config']['id'])
                if config['revision']!=job['model_config']['revision']:raise ConfigError('model_config_changed')
                if job['type'] in ('storyboard_generation','image_polish'):
                    raw=job.get('raw_model_output')
                    if raw is None:
                        save(phase='inference');adapter=self.llm_factory(config,timeout=180,max_retries=0)
                        raw=adapter.chat(messages=[{'role':'user','content':job['effective_prompt']}])
                    save(raw_model_output=raw,phase='validating')
                    if job['type']=='image_polish':
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
                        self.jobs.defer(job);deferred=True;return True
                    ex=job.get('execution');release=not ex or ex['state']=='prepared'
                    adapter=self.adapter_factory(config)
                    if not ex:
                        adapter.preflight()
                        if not adapter.idle():
                            self.jobs.defer(job);deferred=True;return True
                        save(phase='uploading')
                        uploaded=[adapter.upload(self.media,r) for r in job['reference_snapshot']]
                        ex={'state':'prepared','submission_id':job['_id'],
                            'workflow':build(job['request'],job['effective_prompt'],params,uploaded,job['_id'])}
                        save(execution=ex)
                    if ex['state']=='submitting':
                        pid=adapter.locate(ex['submission_id'])
                        if not pid:raise ConfigError('execution_outcome_unknown')
                        ex.update(state='submitted',prompt_id=pid);save(execution=ex)
                    if ex['state']=='prepared':
                        ex['state']='submitting';save(execution=ex,phase='submitting');release=False
                        pid=adapter.submit(ex['workflow'],ex['submission_id'])
                        ex.update(state='submitted',prompt_id=pid);save(execution=ex,phase='inference')
                    deadline=time.monotonic()+params.timeout_seconds
                    while time.monotonic()<deadline:
                        if lost.is_set():raise ConfigError('job_ownership_lost')
                        history=adapter.history(ex['prompt_id'])
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
                    result={'artifacts':[artifact],'shot_id':job['request']['shot_id'],'review_required':True,
                            'requested_duration':job['request']['shot']['duration']}
            save(completed_result=result,phase='finalizing')
        except ConfigError as exc:
            error=exc.code
            if error=='job_ownership_lost':lost.set()
        except Exception:error='production_execution_error'
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
            finally:
                done.set();thread.join()
                if adapter:
                    if hasattr(adapter,'close'):adapter.close()
                    elif getattr(adapter,'_client',None):adapter._client.close()
        return True
