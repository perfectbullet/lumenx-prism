"""Pinned local H3 Ref2VA graph. Other H3 routes are not implicitly enabled."""
import hashlib
import json
import time
from pathlib import Path,PurePosixPath
import httpx
from .comfy_images import ComfyImages
from .production_schemas import VideoParameters
from .video_media import MAX_VIDEO_BYTES
from .model_configs import ConfigError

WORKFLOW=Path(__file__).parent/'workflows/h3-ref2va-v1.json'
def template(): return json.loads(WORKFLOW.read_text())
def snapshot(): return {'version':'h3-ref2va-v1','sha256':hashlib.sha256(WORKFLOW.read_bytes()).hexdigest()}
def build(request,prompt,params,uploaded,job_id):
    graph=template();shot=request['shot'];board=request['board']
    graph['129']['inputs']['noise_seed']=request['seed']
    graph['124']['inputs']['steps']=params.steps
    graph['92']['inputs']['filename_prefix']='prism/'+job_id+'/video'
    items=[{'id':f'ref{i}','kind':'image','source':'upload','scope':'all','track':'visual','name':f,
            'file':f,'start':0,'duration':shot['duration'],'prompt':''} for i,f in enumerate(uploaded)]
    graph['136']['inputs'].update(duration_seconds=shot['duration'],aspect_ratio='16:9 横屏' if board['aspect_ratio']=='16:9' else '9:16 竖屏',
        output_megapixels=params.output_megapixels,global_prompt=prompt,
        timeline_data=json.dumps({'version':5,'globalPrompt':'','allReferencePrompt':'','gridImage':None,'items':items}))
    return graph

class ComfyVideos(ComfyImages):
    def __init__(self,config):
        self.params=VideoParameters.model_validate(config['parameters'])
        self.client=httpx.Client(base_url=config['base_url'].rstrip('/'),timeout=60,trust_env=False,follow_redirects=False,
            headers={'Authorization':'Bearer '+config['api_key']} if config.get('api_key') else {})
    def preflight(self,has_references=True):
        info=self.json('GET','/object_info');graph=template()
        if {n['class_type'] for n in graph.values()}-info.keys(): raise ConfigError('comfy_nodes_missing',422)
        for node,key in [('127','unet_name'),('128','clip_name'),('119','vae_name'),('120','vae_name')]:
            schema=info[graph[node]['class_type']]['input']['required'][key]
            options=schema[0] if isinstance(schema[0],list) else schema[1].get('options',[])
            if graph[node]['inputs'][key] not in options: raise ConfigError('comfy_model_missing',422)
    def idle(self):
        if not super().idle():return False
        response=self.client.post('/free',json={'unload_models':True,'free_memory':True})
        response.raise_for_status()
        # /free schedules unloading on the peer's queue thread; wait for GPU-wide
        # memory to actually become available rather than treating HTTP 200 as completion.
        deadline=time.monotonic()+45
        while time.monotonic()<deadline:
            devices=self.json('GET','/system_stats').get('devices',[])
            gpu=[d for d in devices if d.get('type')=='cuda']
            if gpu and gpu[0].get('vram_free',0)>=self.params.min_free_vram_mb*1024*1024:return True
            time.sleep(1)
        raise ConfigError('video_gpu_memory_not_released',503)

    def download(self,descriptor):
        name=descriptor.get('filename','');folder=descriptor.get('subfolder','')
        if not name.endswith('.mp4') or '/' in name or '\\' in name or descriptor.get('type')!='output' or PurePosixPath(folder).is_absolute() or '..' in PurePosixPath(folder).parts:
            raise ConfigError('comfy_media_invalid',502)
        content=bytearray()
        with self.client.stream('GET','/view',params={'filename':name,'subfolder':folder,'type':'output'}) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content)>MAX_VIDEO_BYTES: raise ConfigError('video_size_invalid',413)
        return bytes(content)
