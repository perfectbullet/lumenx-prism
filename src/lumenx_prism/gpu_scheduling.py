"""A persisted FIFO head per physical GPU group, including delayed memory checks."""
from .comfy_control import GPU_TYPES



def eligible_heads(db,now):
    seen=set();ids=[]
    rows=list(db.ai_jobs.find({'type':{'$in':GPU_TYPES},'scheduling_paused':{'$ne':True},'$or':[
        {'status':'queued'},{'status':'running','lease_until':{'$lte':now}}]}).sort([('created_at',1),('_id',1)]))
    # Recover already-submitted owners before starting any new workflow. Their
    # physical-GPU fence must be resolved even if an older queued job exists.
    holders={r.get('job_id') for r in db.ai_resources.find({})}
    rows.sort(key=lambda r:0 if r['_id'] in holders or r['status']=='running' else 1)
    for row in rows:
        group=(row.get('model_config') or {}).get('parameters',{}).get('resource_group') or '__default__'
        if group in seen:continue
        seen.add(group)
        due=row.get('not_before')
        if row['status']=='queued' and due is not None:
            if due.tzinfo is None:due=due.replace(tzinfo=now.tzinfo)
            if due>now:continue
        ids.append(row['_id'])
    return ids
