"""Additional job types sharing existing ownership, idempotency and GPU fences."""
from datetime import timedelta
import hashlib
import json
from uuid import uuid4
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from .image_jobs import ImageJobs
from .jobs import utcnow
from .model_configs import ConfigError,ModelConfigs
from .production_schemas import BoardInput,BoardContent
from .storyboards import Drafts,compile_video,planning_prompt,PROMPT_VERSION
from .comfy_videos import snapshot

class ProductionJobs(ImageJobs):
    @staticmethod
    def digest(kind,payload):
        return hashlib.sha256(json.dumps({'type':kind,'input':payload.model_dump(mode='json')},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    def existing_for(self,kind,key,payload):
        row=self.collection.find_one({'idempotency_key':key})
        if row and (row['type']!=kind or row['request_hash']!=self.digest(kind,payload)): raise ConfigError('idempotency_conflict',409)
        return self.public(row) if row else None
    def submit_new(self,kind,payload,key,configs,media):
        self.ensure_indexes();old=self.existing_for(kind,key,payload)
        if old:return old
        request=payload.model_dump(mode='json');config=None;refs=[];prompt='';workflow=None
        if kind=='image_polish':
            from .image_polish import polish_prompt
            config=configs.resolve_production_model('image_polish');prompt=polish_prompt(payload)
        elif kind=='storyboard_generation':
            config=configs.resolve_production_model('storyboard_generation')
            if payload.source_entity_job_id:
                source=self.get(str(payload.source_entity_job_id))
                if source['type']!='entity_extraction' or source['status']!='succeeded': raise ConfigError('entity_source_not_ready',422)
            refs=media.snapshot([a.media_id for a in payload.assets])
            if any(r['kind']!='image' for r in refs):raise ConfigError('image_reference_required',422)
            for r in refs:media.read_verified(r['id'],r['sha256'])
            prompt=planning_prompt(payload)
        elif kind in ('video_generation','video_assembly'):
            draft=Drafts(self.collection.database).get(payload.draft_id)
            if draft['revision']!=payload.revision: raise ConfigError('storyboard_revision_conflict',409)
            if kind=='video_generation':
                config=configs.resolve_production_model(kind,payload.model_config_id)
                board=BoardInput.model_validate(draft['input']);shots=BoardContent.model_validate(draft['content']).shots
                shot=next((s for s in shots if s.id==payload.shot_id),None)
                if not shot: raise ConfigError('shot_not_found',404)
                ids={a.name:str(a.media_id) for a in board.assets};refmap={r['id']:r for r in draft['reference_snapshot']}
                refs=[refmap[ids[n]] for n in shot.asset_names]
                for r in refs:media.read_verified(r['id'],r['sha256'])
                request.update(board=board.model_dump(mode='json'),shot=shot.model_dump(mode='json'))
                prompt=compile_video(board,shot);workflow=snapshot()
            else:
                if len(payload.video_job_ids)!=len(draft['content']['shots']): raise ConfigError('assembly_shots_incomplete',422)
                for id,shot in zip(payload.video_job_ids,draft['content']['shots']):
                    job=self.get(str(id));r=job['request']
                    if job['type']!='video_generation' or job['status']!='succeeded' or r['draft_id']!=str(payload.draft_id) or r['revision']!=payload.revision or r['shot_id']!=shot['id']:
                        raise ConfigError('assembly_candidate_mismatch',422)
                    refs.extend(job['result']['artifacts'])
        else: raise ConfigError('task_type_unsupported',422)
        row=dict(_id=str(uuid4()),schema_version=1,type=kind,status='queued',phase='queued',created_at=utcnow(),
            started_at=None,finished_at=None,duration_ms=None,request=request,idempotency_key=key,
            request_hash=self.digest(kind,payload),effective_prompt=prompt,prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
            prompt_version='asset-description-polish-v1' if kind=='image_polish' else PROMPT_VERSION,reference_snapshot=refs,workflow_snapshot=workflow,
            model_config=ModelConfigs.snapshot(config) if config else None,provider=config['provider'] if config else 'ffmpeg',
            requested_model=config['model'] if config else 'preview-assembly',result=None,error_code=None,
            attempt=0,worker_id=None,lease_until=None,execution=None)
        try:self.collection.insert_one(row)
        except DuplicateKeyError:return self.existing_for(kind,key,payload)
        return self.public(row)
    def claim_production(self,worker_id,kinds,lease_seconds):
        now=utcnow()
        self.collection.update_many({'type':{'$in':['storyboard_generation','image_polish']},'status':'running','lease_until':{'$lte':now},'phase':'inference'},
            {'$set':{'status':'failed','phase':'interrupted','error_code':'execution_outcome_unknown','finished_at':now}})
        row=self.collection.find_one_and_update({'type':{'$in':kinds},'$or':[
            {'status':'queued','$or':[{'not_before':{'$exists':False}},{'not_before':{'$lte':now}}]},
            {'status':'running','lease_until':{'$lte':now}}]},
            {'$set':{'status':'running','worker_id':worker_id,'claim_token':str(uuid4()),'lease_until':now+timedelta(seconds=lease_seconds)},'$inc':{'attempt':1}},
            sort=[('created_at',1)],return_document=ReturnDocument.AFTER)
        if row and not row.get('started_at'):self.update_owned(row,started_at=now)
        return row

    def recover_video(self,id):
        row=self.collection.find_one({'_id':str(id),'type':'video_generation'})
        if not row:raise ConfigError('job_not_found',404)
        if row['status'] in ('queued','running'):return self.public(row)
        if row['status']!='failed' or (row.get('execution') or {}).get('state') not in ('submitting','submitted'):
            raise ConfigError('video_not_recoverable',409)
        self.collection.update_one({'_id':str(id),'status':'failed'},
            {'$set':{'status':'queued','phase':'recovering','error_code':None,'finished_at':None,'lease_until':None,'audit_synced':False}})
        return self.get(str(id))

    def recover_board(self,id):
        row=self.collection.find_one({'_id':str(id),'type':'storyboard_generation'})
        if not row:raise ConfigError('job_not_found',404)
        if row['status']!='failed' or not row.get('raw_model_output'):raise ConfigError('storyboard_not_recoverable',409)
        self.collection.update_one({'_id':str(id),'status':'failed'},{'$set':{'status':'queued','phase':'validating',
            'error_code':None,'finished_at':None,'lease_until':None,'audit_synced':False}})
        return self.get(str(id))
