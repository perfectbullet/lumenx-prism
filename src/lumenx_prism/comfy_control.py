"""Persistent GPU scheduling controls and queue-protected ComfyUI cache release."""
from uuid import uuid4
from urllib.parse import urlsplit
import httpx
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from .jobs import utcnow
from .model_configs import ConfigError

GPU_TYPES=['image_generation','video_generation']

class GPUControl:
    def __init__(self,db): self.db=db;self.collection=db['ai_runtime_controls']
    def paused(self): return bool((self.collection.find_one({'_id':'gpu'}) or {}).get('paused'))
    def state(self):
        row=self.collection.find_one({'_id':'gpu'}) or {}
        running=list(self.db.ai_jobs.find({'type':{'$in':GPU_TYPES},'status':'running'},
            {'_id':1,'type':1,'phase':1,'request.name':1,'request.shot_id':1}))
        queued=self.db.ai_jobs.count_documents({'type':{'$in':GPU_TYPES},'status':'queued'})
        held=self.db.ai_jobs.count_documents({'type':{'$in':GPU_TYPES},'status':'queued','scheduling_paused':True})
        return {'paused':bool(row.get('paused')),'updated_at':row.get('updated_at'),'reason':row.get('reason',''),
            'maintenance':bool(row.get('maintenance')),'last_release':row.get('last_release'),
            'mode':'draining' if row.get('paused') and running else 'paused' if row.get('paused') else 'accepting',
            'queued':queued,'individually_paused':held,
            'running':[{'id':r['_id'],'type':r['type'],'phase':r['phase'],'name':r.get('request',{}).get('name') or r.get('request',{}).get('shot_id','')} for r in running],
            'resource_holders':[{'group':r['_id'],'job_id':r.get('job_id')} for r in self.db.ai_resources.find({})]}
    def pause(self,reason='人工暂停GPU任务领取'):
        self.collection.update_one({'_id':'gpu'},{'$set':{'paused':True,'reason':reason,'updated_at':utcnow()}},upsert=True)
        return self.state()
    def resume(self):
        self.collection.update_one({'_id':'gpu'},{'$setOnInsert':{'paused':False}},upsert=True)
        row=self.collection.find_one_and_update({'_id':'gpu','maintenance':None},
            {'$set':{'paused':False,'reason':'','updated_at':utcnow()}},return_document=ReturnDocument.AFTER)
        if not row:raise ConfigError('gpu_maintenance_in_progress',409)
        return self.state()
    def job_pause(self,id,paused):
        row=self.db.ai_jobs.find_one_and_update({'_id':str(id),'type':{'$in':GPU_TYPES},'status':'queued'},
            {'$set':{'scheduling_paused':paused,'scheduling_updated_at':utcnow()}},return_document=ReturnDocument.AFTER)
        if not row:raise ConfigError('only_queued_gpu_job_can_pause',409)
        return {'id':row['_id'],'status':row['status'],'scheduling_paused':bool(row.get('scheduling_paused'))}

class ComfyControl:
    def __init__(self,db,client_factory=None):
        self.db=db;self.gpu=GPUControl(db)
        self.client_factory=client_factory or (lambda **kw:httpx.Client(trust_env=False,follow_redirects=False,**kw))
    def instances(self):
        if self.db.comfyui_api_config.count_documents({}):
            from .comfy_api_configs import ComfyApiConfigs
            services=ComfyApiConfigs(self.db);items=[]
            for server in services.collection.find({'enabled':True}).sort('_id',1):
                runtime=services.connection(server)
                aliases=[m['_id'] for m in self.db.model_comfigs.find({'comfyui_api_config_id':server['_id']},{'_id':1})]
                items.append({'id':server['_id'],'name':server['name'],'url':server['base_url'],
                    'group':server['resource_group'],'config':runtime,'model_config_ids':aliases})
            return items
        rows=self.db.model_comfigs.find({'record_type':'model','provider':'comfyui','kind':{'$in':['image','video']},'protocol':'custom_http'}).sort('_id',1)
        seen=set();items=[]
        for r in rows:
            url=r['base_url'].rstrip('/');group=r.get('parameters',{}).get('resource_group')
            if not group or url in seen:continue
            parts=urlsplit(url)
            if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
                continue
            seen.add(url);items.append({'id':r['_id'],'name':r['name'],'url':url,'group':group,'config':r})
        return items
    def request(self,item,method,path,**kw):
        key=item.get('config',{}).get('api_key')
        with self.client_factory(timeout=item.get('config',{}).get('_comfyui_runtime',{}).get('http_timeout_seconds',10),base_url=item['url'],headers={'Authorization':'Bearer '+key} if key else {}) as client:
            r=client.request(method,path,**kw);r.raise_for_status();return r
    def queue(self,item,include_tasks=False):
        q=self.request(item,'GET','/queue').json()
        if not isinstance(q.get('queue_running'),list) or not isinstance(q.get('queue_pending'),list):raise ConfigError('comfy_queue_invalid',503)
        result={'running':len(q['queue_running']),'pending':len(q['queue_pending']),'total':len(q['queue_running'])+len(q['queue_pending'])}
        if include_tasks:
            result['tasks']=[self.identify_task(row,state) for key,state in [('queue_running','running'),('queue_pending','pending')] for row in q[key] if isinstance(row,list) and len(row)>1]
        return result

    def identify_task(self,record,state):
        prompt_id=str(record[1]);extra=record[3] if len(record)>3 and isinstance(record[3],dict) else {}
        token=extra.get('prism_submission_id');task={'prompt_id':prompt_id,'state':state,'origin':'external'}
        candidates=list(self.db.ai_jobs.find({'type':{'$in':GPU_TYPES},'$or':[
            {'execution.prompt_id':prompt_id},{'executions.prompt_id':prompt_id},{'retry_events.execution.prompt_id':prompt_id}]}).limit(2))
        if not candidates and isinstance(token,str):
            candidate=self.db.ai_jobs.find_one({'_id':token.split(':',1)[0],'type':{'$in':GPU_TYPES}})
            if candidate:
                executions=[candidate.get('execution') or {}]+candidate.get('executions',[])+[e.get('execution') or {} for e in candidate.get('retry_events',[])]
                if any(e.get('submission_id')==token for e in executions):candidates=[candidate]
        if len(candidates)==1:
            job=candidates[0];request=job.get('request',{});task.update(origin='prism',prism_job_id=job['_id'],
                name=request.get('name') or request.get('shot',{}).get('title') or request.get('title') or 'Prism 生成任务')
        elif candidates or token:task['origin']='unknown'
        return task
    def status(self):
        from .gpu_processes import collect,memory_for
        process_sample=collect()
        items=[]
        for r in self.instances():
            item={k:r[k] for k in ('id','name','url','group')}
            item.update(online=False,queue_readable=False,stats_readable=False,process_memory=memory_for(r['url'],process_sample))
            try:item.update(queue=self.queue(r,include_tasks=True),queue_readable=True)
            except Exception:item['error_code']='comfy_queue_unavailable'
            try:
                stats=self.request(r,'GET','/system_stats').json()
                item['devices']=[{k:d.get(k) for k in ('name','type','vram_total','vram_free','torch_vram_total','torch_vram_free')} for d in stats.get('devices',[])]
                item['stats_readable']=True
            except Exception:item['error_code']='comfy_stats_unavailable'
            item['online']=item['queue_readable'] and item['stats_readable']
            items.append(item)
        readable=[i for i in items if i['queue_readable']]
        summary={key:sum(i['queue'][key] for i in readable) for key in ('running','pending','total')}
        summary.update(available_instances=len(readable),unavailable_instances=len(items)-len(readable))
        return {'gpu':self.gpu.state(),'instances':items,'comfy_queue':summary}
    def free(self,instance_id=None):
        instances=self.instances();targets=[i for i in instances if instance_id is None or i['id']==str(instance_id) or str(instance_id) in i.get('model_config_ids',[])]
        if not targets:raise ConfigError('comfy_instance_not_found',404)
        token=str(uuid4());stamp=utcnow()
        owned=self.gpu.collection.find_one_and_update({'_id':'gpu','paused':True,'maintenance':None},
            {'$set':{'maintenance':{'token':token,'started_at':stamp}}},return_document=ReturnDocument.AFTER)
        if not owned:raise ConfigError('pause_gpu_before_free_or_maintenance_busy',409)
        held=[];released=[];status='failed'
        try:
            # A job may be between claim and resource acquisition; refuse while any is running.
            if self.db.ai_jobs.count_documents({'type':{'$in':GPU_TYPES},'status':'running'}):raise ConfigError('gpu_jobs_still_running',409)
            for group in sorted({i['group'] for i in instances}):
                try:self.db.ai_resources.insert_one({'_id':group,'job_id':'maintenance:'+token,'created_at':stamp})
                except DuplicateKeyError:raise ConfigError('gpu_resource_busy_or_unresolved',409) from None
                held.append(group)
            if self.db.ai_resources.count_documents({'job_id':{'$ne':'maintenance:'+token}}):raise ConfigError('gpu_resource_busy_or_unresolved',409)
            # Single-instance unload affects only that ComfyUI process. Other offline
            # services must not prevent releasing an idle target. Bulk release
            # still validates every managed instance and configured peer.
            peers={i['url']:i for i in targets}
            for i in (instances if instance_id is None else []):
                for url in i['config'].get('parameters',{}).get('peer_base_urls',[]):
                    url=url.rstrip('/')
                    if url not in peers:peers[url]={'url':url}
            for peer in peers.values():
                q=self.queue(peer)
                if q['running'] or q['pending']:raise ConfigError('comfy_queue_not_idle',409)
            for target in targets:
                # ComfyUI returns an empty 200; do not parse it as JSON.
                self.request(target,'POST','/free',json={'unload_models':True,'free_memory':True})
                released.append(target['id'])
            status='requested'
            return {'status':'requested','instances':released,'note':'已请求卸载模型与释放缓存；刷新显存查看实际结果，GPU队列仍保持暂停。'}
        except ConfigError:raise
        except Exception:raise ConfigError('comfy_cache_release_unavailable',503) from None
        finally:
            for group in held:self.db.ai_resources.delete_one({'_id':group,'job_id':'maintenance:'+token})
            self.gpu.collection.update_one({'_id':'gpu','maintenance.token':token},
                {'$unset':{'maintenance':''},'$set':{'last_release':{'at':utcnow(),'status':status,'instances':released}}})
