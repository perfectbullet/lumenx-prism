"""Pinned Qwen-Image21 workflow over HTTP; no local inference imports."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import httpx
from .image_schemas import ImageParameters
from .media import MAX_BYTES
from .model_configs import ConfigError

WORKFLOW = Path(__file__).parent / 'workflows/qwen-image21-v1.json'


def template():
    return json.loads(WORKFLOW.read_text())


def workflow_snapshot():
    graph = template()
    return {'version':'qwen-image21-v1', 'sha256':hashlib.sha256(WORKFLOW.read_bytes()).hexdigest(),
            'checkpoints':{key:graph[node]['inputs'][key] for node,key in
                           [('4','clip_name'),('5','unet_name'),('13','vae_name')]}}


def build_workflow(request, prompt, negative, parameters, uploaded, job_id, index):
    graph = template()
    graph['37']['inputs'].update(prompt=prompt, negative_prompt=negative,
                                resolution=parameters.reference_resolution)
    graph['36']['inputs'].update(width=request['width'],height=request['height'],batch_size=1)
    graph['41']['inputs'].update(width=request['width'],height=request['height'])
    graph['23']['inputs'].update(seed=(request['seed']+index) % (2**53),steps=parameters.steps,cfg=parameters.cfg)
    graph['43']['inputs']['filename_prefix'] = f'prism/{job_id}/{index}'
    for i, filename in enumerate(uploaded):
        node = str(100+i)
        graph[node] = {'class_type':'LoadImage','inputs':{'image':filename}}
        graph['37']['inputs'][f'images.image_{i+1}'] = [node,0]
    # Explicit dimensions remove any dependency on an unused resolution widget.
    graph.pop('42', None)
    return graph


class ComfyImages:
    def __init__(self, config):
        self.params = ImageParameters.model_validate(config['parameters'])
        self.client = httpx.Client(base_url=config['base_url'].rstrip('/'), timeout=30,
                                  follow_redirects=False, trust_env=False,
                                  headers={'Authorization':'Bearer '+config['api_key']} if config.get('api_key') else {})

    def close(self): self.client.close()

    def json(self, method, path, **kwargs):
        response = self.client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    def preflight(self, has_references):
        info = self.json('GET','/object_info')
        graph = template()
        required = {n['class_type'] for n in graph.values()} | ({'LoadImage'} if has_references else set())
        if required - info.keys(): raise ConfigError('comfy_nodes_missing', 422)
        if has_references and 'images' not in info['TextEncodeQwenImage21']['input']['required']:
            raise ConfigError('image_references_unsupported', 422)
        for node, field in [('4','clip_name'),('5','unet_name'),('13','vae_name')]:
            schema = info[graph[node]['class_type']]['input']['required'][field]
            options = schema[0] if isinstance(schema[0],list) else schema[1].get('options',[])
            if graph[node]['inputs'][field] not in options:
                raise ConfigError('comfy_model_missing', 422)

    def idle(self):
        q = self.json('GET','/queue')
        if not isinstance(q.get('queue_running'),list) or not isinstance(q.get('queue_pending'),list):
            raise ConfigError('comfy_queue_invalid')
        if q['queue_running'] or q['queue_pending']: return False
        peers=[]
        for base in self.params.peer_base_urls:
            # Peer auth is intentionally not inherited from this provider.
            with httpx.Client(timeout=10,trust_env=False,follow_redirects=False) as client:
                response=client.get(base.rstrip('/')+'/queue'); response.raise_for_status(); peer=response.json()
            if not isinstance(peer.get('queue_running'),list) or not isinstance(peer.get('queue_pending'),list):
                raise ConfigError('comfy_queue_invalid')
            if peer['queue_running'] or peer['queue_pending']: return False
            peers.append(base)
        # Caller holds the physical-GPU resource slot. Idle peers must release cached models
        # before this instance loads a different model (image and H3 share one GPU).
        for base in peers:
            with httpx.Client(timeout=30,trust_env=False,follow_redirects=False) as client:
                response=client.post(base.rstrip('/')+'/free',json={'unload_models':True,'free_memory':True})
                response.raise_for_status()
        return True

    def upload(self, media, snapshot):
        content=media.read_verified(snapshot['id'],snapshot['sha256'])
        ext={'image/png':'png','image/jpeg':'jpg','image/webp':'webp'}[snapshot['mime_type']]
        result=self.json('POST','/upload/image',files={'image':(snapshot['id']+'.'+ext,content,snapshot['mime_type'])},
                         data={'type':'input','overwrite':'false','subfolder':'prism'})
        name=PurePosixPath(result.get('subfolder','')) / result['name']
        if name.is_absolute() or '..' in name.parts: raise ConfigError('comfy_media_invalid')
        return str(name)

    def submit(self, graph, token):
        response=self.json('POST','/prompt',json={'prompt':graph,'client_id':token,'extra_data':{'prism_submission_id':token}})
        if response.get('node_errors') or not response.get('prompt_id'): raise ConfigError('comfy_prompt_rejected',422)
        return response['prompt_id']

    def locate(self, token):
        queue=self.json('GET','/queue')
        history=self.json('GET','/history',params={'max_items':200})
        records=queue.get('queue_running',[])+queue.get('queue_pending',[])
        records += [row.get('prompt',[]) for row in history.values()]
        found={row[1] for row in records if len(row)>3 and isinstance(row[3],dict)
               and row[3].get('prism_submission_id')==token}
        if len(found)>1: raise ConfigError('execution_outcome_unknown')
        return next(iter(found),None)

    def history(self, prompt_id):
        return self.json('GET','/history/'+prompt_id).get(prompt_id)

    def download(self, descriptor):
        name=descriptor.get('filename',''); subfolder=descriptor.get('subfolder','')
        if (not name or '/' in name or '\\' in name or descriptor.get('type')!='output'
                or PurePosixPath(subfolder).is_absolute() or '..' in PurePosixPath(subfolder).parts):
            raise ConfigError('comfy_media_invalid')
        content=bytearray()
        with self.client.stream('GET','/view',params={k:descriptor[k] for k in ('filename','subfolder','type')}) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content)>MAX_BYTES: raise ConfigError('media_size_invalid',413)
        return bytes(content)
