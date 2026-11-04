"""ComfyUI workflow duration excludes native/Prism queue wait; never infer it from leases."""
from datetime import datetime,timezone
import math
import threading
import time
import httpx

_lock=threading.Lock()
_busy=set()
_recent={}


def from_history(history):
    if not isinstance(history,dict) or not isinstance(history.get('status'),dict):return None
    events=history['status'].get('messages',[])
    if not isinstance(events,list):return None
    start=end=None
    for event in events:
        if not isinstance(event,(list,tuple)) or len(event)!=2 or not isinstance(event[1],dict):continue
        kind,data=event;stamp=data.get('timestamp')
        if isinstance(stamp,bool) or not isinstance(stamp,(int,float)) or not math.isfinite(stamp):continue
        if kind=='execution_start':start=stamp;end=None
        elif kind in ('execution_success','execution_error','execution_interrupted') and start is not None and stamp>=start:end=stamp
    if start is None or end is None:return None
    try:
        return {'started_at':datetime.fromtimestamp(start/1000,timezone.utc),'finished_at':datetime.fromtimestamp(end/1000,timezone.utc),'duration_ms':round(end-start)}
    except (ValueError,OverflowError,OSError):return None


def aggregate(parts,expected):
    unique={p['prompt_id']:p for p in parts if p.get('prompt_id') and p.get('duration_ms') is not None}
    if not unique:return None
    values=list(unique.values())
    return {'source':'comfy_history','duration_ms':sum(p['duration_ms'] for p in values),
        'started_at':min(p['started_at'] for p in values),'finished_at':max(p['finished_at'] for p in values),
        'completed_executions':len(values),'expected_executions':expected,'complete':len(values)==expected}


def refresh_many(db,ids,client_factory=None):
    """Best-effort cached telemetry, executed after API responses or by the worker."""
    deadline=time.monotonic()+12
    for id in ids[:20]:
        if time.monotonic()>deadline:break
        key=(str(db.name),str(id))
        with _lock:
            if key in _busy or time.monotonic()-_recent.get(key,0)<30:continue
            _busy.add(key)
        try:
            from .h3_versions import apply_pending
            apply_pending(db,str(id))
            row=db.ai_jobs.find_one({'_id':str(id),'type':{'$in':['image_generation','video_generation']}})
            if not row or (row.get('execution_timing') or {}).get('complete'):continue
            executions=row.get('executions',[]) if row['type']=='image_generation' else [row.get('execution') or {}]
            pids=list(dict.fromkeys(e.get('prompt_id') for e in executions if e.get('prompt_id')))
            if not pids:continue
            config=db.model_comfigs.find_one({'_id':row.get('model_config',{}).get('id')})
            if not config or config.get('revision')!=row.get('model_config',{}).get('revision'):continue
            from .model_configs import ModelConfigs
            config=ModelConfigs(db).runtime_config(config)
            factory=client_factory or httpx.Client
            parts=[]
            with factory(base_url=config['base_url'].rstrip('/'),timeout=5,trust_env=False,follow_redirects=False,
                headers={'Authorization':'Bearer '+config['api_key']} if config.get('api_key') else {}) as client:
                for pid in pids:
                    if time.monotonic()>deadline:break
                    response=client.get('/history/'+pid);response.raise_for_status()
                    timing=from_history(response.json().get(pid))
                    if timing:parts.append({'prompt_id':pid,**timing})
            timing=aggregate(parts,row['request'].get('batch_size',1) if row['type']=='image_generation' else 1)
            if timing:db.ai_jobs.update_one({'_id':str(id),'attempt':row.get('attempt',0),'input_revision':row.get('input_revision')}, {'$set':{'execution_timing':timing}})
        except Exception:pass
        finally:
            with _lock:
                _busy.discard(key);_recent[key]=time.monotonic()
                if len(_recent)>5000:_recent.clear()
