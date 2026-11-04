#!/usr/bin/env python3
"""Import this Prism database's legacy ComfyUI instances. Dry-run unless --apply."""
import argparse
import os
from pathlib import Path
import sys
from collections import defaultdict
from dotenv import load_dotenv
from pymongo import MongoClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from lumenx_prism.comfy_api_configs import ComfyApiConfigs,ComfyApiInput
from lumenx_prism.image_schemas import ImageParameters
from lumenx_prism.production_schemas import VideoParameters

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--apply',action='store_true')
p.add_argument('--video-min-free-vram-mb',type=int,required=True)
p.add_argument('--memory-check-seconds',type=int,required=True)
p.add_argument('--http-timeout-seconds',type=int,required=True)
p.add_argument('--disable-url',action='append',default=[])
args=p.parse_args()
load_dotenv(Path(__file__).resolve().parents[1]/'.env')
db=MongoClient(os.environ['PRISM_MONGODB_URI'])[os.getenv('PRISM_MONGODB_DATABASE','lumenx_prism')]
services=ComfyApiConfigs(db);groups=defaultdict(list);mapping={}
for model in db.model_comfigs.find({'record_type':'model','provider':'comfyui','kind':{'$in':['image','video']}}):groups[model['base_url'].rstrip('/')].append(model)
for url,models in groups.items():
    resource_groups={m['parameters']['resource_group'] for m in models}
    if len(resource_groups)!=1:raise SystemExit('Conflicting resource groups for '+url)
    sample=models[0];video=any(m['kind']=='video' for m in models)
    parameters=(VideoParameters if video else ImageParameters).model_validate(sample['parameters'])
    data=ComfyApiInput(name=sample['name'],base_url=url,resource_group=next(iter(resource_groups)),
        enabled=url not in args.disable_url,api_key=sample.get('api_key') or None,
        min_free_vram_mb=args.video_min_free_vram_mb if video else 0,memory_check_seconds=args.memory_check_seconds,
        http_timeout_seconds=args.http_timeout_seconds,queue_retry_seconds=parameters.queue_retry_seconds,
        poll_seconds=parameters.poll_seconds,execution_timeout_seconds=parameters.timeout_seconds,
        notes='Imported from existing Prism model configuration; legacy model snapshots retained.')
    existing=services.collection.find_one({'base_url':url})
    if args.apply:
        if existing:id=existing['_id']
        else:id=services.save(data)['id']
        mapping[url]=id
        for model in models:db.model_comfigs.update_one({'_id':model['_id']},{'$set':{'comfyui_api_config_id':id}})
    print(('APPLY' if args.apply else 'DRY RUN'),sample['name'],url,'enabled='+str(data.enabled),'group='+data.resource_group)
if args.apply:
    for url,models in groups.items():
        server=services.get(mapping[url]);peers=list(dict.fromkeys(peer.rstrip('/') for m in models for peer in m.get('parameters',{}).get('peer_base_urls',[])))
        if any(peer not in mapping for peer in peers):raise SystemExit('Unregistered peer URL requires explicit import')
        ids=[mapping[peer] for peer in peers]
        if server['peer_ids']!=ids:
            payload={k:v for k,v in server.items() if k in ComfyApiInput.model_fields and k!='api_key'}
            payload['peer_ids']=ids;services.save(ComfyApiInput.model_validate(payload),server['_id'])
    print('Instance references linked without changing model revisions or existing jobs.')
