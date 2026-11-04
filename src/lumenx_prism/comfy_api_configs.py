"""Authoritative MongoDB configuration for ComfyUI services, separate from models."""
from datetime import datetime,timezone
from uuid import UUID,uuid4
from pydantic import BaseModel,ConfigDict,Field,SecretStr,AnyHttpUrl,model_validator
from pymongo.errors import DuplicateKeyError
from .model_configs import ConfigError


class ComfyApiInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(min_length=1,max_length=100)
    base_url:AnyHttpUrl
    resource_group:str=Field(min_length=1,max_length=100,pattern=r'^[a-zA-Z0-9_.:-]+$')
    enabled:bool=True
    api_key:SecretStr | None=None
    peer_ids:list[UUID]=Field(default_factory=list,max_length=8)
    optional_peer_ids:list[UUID]=Field(default_factory=list,max_length=8)
    min_free_vram_mb:int=Field(ge=0,le=200000)
    memory_check_seconds:int=Field(ge=1,le=3600)
    queue_retry_seconds:int=Field(ge=1,le=3600)
    http_timeout_seconds:int=Field(ge=1,le=600)
    poll_seconds:float=Field(ge=.05,le=30,allow_inf_nan=False)
    execution_timeout_seconds:int=Field(ge=30,le=10800)
    notes:str=Field(default='',max_length=2000)
    revision:int | None=Field(default=None,ge=1)

    @model_validator(mode='after')
    def valid(self):
        if self.base_url.username or self.base_url.password or self.base_url.query or self.base_url.fragment:raise ValueError('服务地址不能含凭据、查询参数或片段')
        if len(set(self.peer_ids))!=len(self.peer_ids) or len(set(self.optional_peer_ids))!=len(self.optional_peer_ids):raise ValueError('关联实例不能重复')
        if not set(self.optional_peer_ids)<=set(self.peer_ids):raise ValueError('可选关联实例必须属于关联实例')
        return self


class ComfyApiConfigs:
    def __init__(self,db):self.db=db;self.collection=db['comfyui_api_config']
    @staticmethod
    def public(row):return {'id':row['_id'],'api_key_set':bool(row.get('api_key')),**{k:v for k,v in row.items() if k not in ('_id','api_key')}}
    def get(self,id,require_enabled=False):
        row=self.collection.find_one({'_id':str(id)})
        if not row:raise ConfigError('comfyui_config_not_found',404)
        if require_enabled and not row['enabled']:raise ConfigError('comfyui_config_disabled',409)
        return row
    def list(self):return {'items':[self.public(r) for r in self.collection.find({}).sort([('name',1),('_id',1)])]}
    def save(self,payload,id=None):
        self.collection.create_index('base_url',unique=True)
        data=payload.model_dump(mode='json',exclude={'api_key','revision'});data['base_url']=data['base_url'].rstrip('/')
        for peer in data['peer_ids']:
            row=self.get(peer)
            if peer==id:raise ConfigError('comfyui_peer_self_reference',422)
            if row['resource_group']!=data['resource_group']:raise ConfigError('comfyui_peer_resource_mismatch',422)
        stamp=datetime.now(timezone.utc);key=payload.api_key.get_secret_value() if payload.api_key else ''
        if id:
            old=self.get(id)
            if payload.revision is None:raise ConfigError('comfyui_revision_required',422)
            if any(data[k]!=old[k] for k in ('base_url','resource_group')) or (old['enabled'] and not data['enabled']):
                models=[r['_id'] for r in self.db.model_comfigs.find({'comfyui_api_config_id':str(id)},{'_id':1})]
                if self.db.ai_jobs.find_one({'status':{'$in':['queued','running']},'model_config.id':{'$in':models}}):raise ConfigError('comfyui_config_in_use',409)
            if key:data['api_key']=key
            result=self.collection.update_one({'_id':str(id),'revision':payload.revision},{'$set':{**data,'updated_at':stamp},'$inc':{'revision':1}})
            if not result.matched_count:raise ConfigError('comfyui_revision_conflict',409)
        else:
            id=str(uuid4());data.update(_id=id,schema_version=1,revision=1,created_at=stamp,updated_at=stamp,api_key=key)
            try:self.collection.insert_one(data)
            except DuplicateKeyError:raise ConfigError('comfyui_address_exists',409) from None
        return self.public(self.get(id))
    def connection(self,row):
        peers=[];optional=[];auth={}
        for id in row['peer_ids']:
            peer=self.get(id)
            if not peer['enabled']:continue
            if peer['resource_group']!=row['resource_group']:raise ConfigError('comfyui_peer_resource_mismatch',422)
            peers.append(peer['base_url'])
            if peer.get('api_key'):auth[peer['base_url']]=peer['api_key']
            if id in row['optional_peer_ids']:optional.append(peer['base_url'])
        return {'base_url':row['base_url'],'api_key':row.get('api_key',''),
            'parameters':{'resource_group':row['resource_group'],'peer_base_urls':peers,'poll_seconds':row['poll_seconds'],
                'timeout_seconds':row['execution_timeout_seconds'],'queue_retry_seconds':row['queue_retry_seconds']},
            '_optional_peer_base_urls':optional,'_comfyui_peer_auth':auth,
            '_comfyui_runtime':{k:row[k] for k in ('name','revision','base_url','resource_group','http_timeout_seconds','min_free_vram_mb','memory_check_seconds','queue_retry_seconds')}|{'id':row['_id']}}
