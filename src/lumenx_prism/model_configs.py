"""Versioned model definitions and atomic usage bindings in model_comfigs."""
from datetime import datetime, timezone
import json
from typing import Literal
from uuid import UUID,uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, AnyHttpUrl, model_validator


class ConfigError(RuntimeError):
    def __init__(self, code, status=503, details=None):
        self.code, self.status, self.details = code, status, details
        super().__init__(code)


class ModelConfigInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    kind: Literal['text', 'speech', 'image', 'video']
    provider: str = Field(min_length=1, max_length=80)
    protocol: Literal['openai_chat', 'openai_audio', 'openai_images', 'custom_http']
    model: str = Field(min_length=1, max_length=200)
    base_url: AnyHttpUrl | None=None
    comfyui_api_config_id: UUID | None=None
    api_key: SecretStr | None = None
    enabled: bool = True
    capabilities: list[str] = Field(default_factory=list, max_length=30)
    parameters: dict = Field(default_factory=dict)
    notes: str = Field(default='', max_length=2000)
    revision: int | None = Field(default=None, ge=1)

    @model_validator(mode='after')
    def valid_options(self):
        if self.comfyui_api_config_id and not (self.provider=='comfyui' and self.protocol=='custom_http' and self.kind in ('image','video')):raise ValueError('ComfyUI 实例引用仅用于 ComfyUI 图片或视频模型')
        if not self.base_url and not (self.provider=='comfyui' and self.comfyui_api_config_id):raise ValueError('需要服务地址或 ComfyUI 实例引用')
        if self.base_url and (self.base_url.username or self.base_url.password or self.base_url.query or self.base_url.fragment):
            raise ValueError('Base URL must not contain credentials, query or fragment')
        if len(json.dumps(self.parameters, allow_nan=False)) > 16000:
            raise ValueError('Parameters exceed 16000 characters')
        if any(k in self.parameters for k in ('model', 'messages', 'api_key', 'base_url', 'stream')):
            raise ValueError('Connection/model fields must not be placed in parameters')
        if self.kind == 'text' and self.protocol == 'openai_chat':
            ChatParameters.model_validate(self.parameters)
        if self.kind == 'image' and self.provider == 'comfyui':
            from .image_schemas import ImageParameters
            parameters=dict(self.parameters)
            if self.comfyui_api_config_id:parameters.setdefault('resource_group','instance-reference')
            ImageParameters.model_validate(parameters)
        if self.kind == 'video' and self.provider == 'comfyui':
            from .production_schemas import VideoParameters
            parameters=dict(self.parameters)
            if self.comfyui_api_config_id:parameters.setdefault('resource_group','instance-reference')
            VideoParameters.model_validate(parameters)
        return self


class ChatParameters(BaseModel):
    model_config = ConfigDict(extra='forbid')
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, ge=1, le=1000000)
    max_completion_tokens: int | None = Field(default=None, ge=1, le=1000000)
    seed: int | None = None
    response_format: dict | None = None
    extra_body: dict | None = None

    @model_validator(mode='after')
    def protect_identity(self):
        protected = {'model', 'messages', 'stream', 'api_key', 'base_url', 'timeout', 'max_retries'}
        if protected.intersection(self.extra_body or {}):
            raise ValueError('extra_body must not override model, input or connection fields')
        return self


class ModelConfigs:
    def __init__(self, database):
        # Preserve the collection spelling requested by the user.
        self.collection = database['model_comfigs']

    @staticmethod
    def public(row):
        return {'id': row['_id'], 'api_key_set': bool(row.get('api_key')),
                **{k: v for k, v in row.items() if k not in ('_id', 'api_key', 'record_type')}}

    @staticmethod
    def snapshot(row):
        value={k: row[k] for k in ('name', 'kind', 'provider', 'protocol', 'model', 'revision', 'parameters')} | {'id': row['_id']}
        if row.get('comfyui_api_config_id'):value['comfyui_api_config_id']=str(row['comfyui_api_config_id'])
        if row.get('_comfyui_runtime'):value['comfyui_runtime']=row['_comfyui_runtime']
        return value

    def list(self, limit=50, offset=0):
        rows = list(self.collection.find({'record_type': 'model'}).sort(
            [('updated_at', -1), ('_id', -1)]).skip(offset).limit(limit + 1))
        binding = self.collection.find_one({'_id': 'bindings'}) or {}
        return {'items': [self.public(row) for row in rows[:limit]],
                'has_more': len(rows) > limit, 'limit': limit, 'offset': offset,
                'entity_extraction_id': binding.get('entity_extraction_id'),
                'image_generation_id': binding.get('image_generation_id'),
                'storyboard_generation_id':binding.get('storyboard_generation_id'),
                'video_generation_id':binding.get('video_generation_id'),
                'image_polish_id':binding.get('image_polish_id')}

    def get(self, config_id):
        row = self.collection.find_one({'_id': config_id, 'record_type': 'model'})
        if row is None:
            raise ConfigError('model_config_not_found', 404)
        return row

    def save(self, payload, config_id=None):
        data = payload.model_dump(mode='json', exclude={'api_key', 'revision'})
        data['base_url'] = data['base_url'].rstrip('/') if data['base_url'] else None
        if config_id and 'comfyui_api_config_id' not in payload.model_fields_set:
            data['comfyui_api_config_id']=self.get(config_id).get('comfyui_api_config_id')
        if payload.comfyui_api_config_id:
            from .comfy_api_configs import ComfyApiConfigs
            ComfyApiConfigs(self.collection.database).get(str(payload.comfyui_api_config_id),require_enabled=True)
        now = datetime.now(timezone.utc)
        key = payload.api_key.get_secret_value() if payload.api_key else ''
        if config_id:
            if payload.revision is None:
                raise ConfigError('model_revision_required', 422)
            if key:
                data['api_key'] = key
            data['updated_at'] = now
            result = self.collection.update_one(
                {'_id': config_id, 'record_type': 'model', 'revision': payload.revision},
                {'$set': data, '$inc': {'revision': 1}})
            if result.matched_count != 1:
                raise ConfigError('model_revision_conflict', 409)
        else:
            if not key and not (data['kind'] in ('image','video') and data['provider']=='comfyui' and data['protocol']=='custom_http'):
                raise ConfigError('model_api_key_required', 422)
            config_id = str(uuid4())
            data.update(_id=config_id, record_type='model', schema_version=1,
                        api_key=key, revision=1, created_at=now, updated_at=now)
            self.collection.insert_one(data)
        return self.public(self.get(config_id))

    @staticmethod
    def validate_entity_model(row):
        if not row.get('enabled'):
            raise ConfigError('model_config_disabled')
        if row.get('kind') != 'text' or row.get('protocol') != 'openai_chat':
            raise ConfigError('model_config_incompatible', 422)
        if not row.get('api_key'):
            raise ConfigError('model_api_key_required', 422)
        # Validate externally edited documents as well as page/API submissions.
        ModelConfigInput.model_validate({k: v for k, v in row.items()
                                        if k in ModelConfigInput.model_fields})

    def bind_entity_model(self, config_id):
        self.validate_entity_model(self.get(config_id))
        self.collection.update_one({'_id': 'bindings'}, {'$set': {
            'record_type': 'bindings', 'schema_version': 1,
            'entity_extraction_id': config_id, 'updated_at': datetime.now(timezone.utc),
        }}, upsert=True)
        return {'entity_extraction_id': config_id}

    def resolve_entity_model(self):
        binding = self.collection.find_one({'_id': 'bindings'}) or {}
        config_id = binding.get('entity_extraction_id')
        if not config_id:
            raise ConfigError('model_not_configured')
        row = self.get(config_id)
        self.validate_entity_model(row)
        return row


    def validate_image_model(self,row):
        row=self.runtime_config(row)
        from .image_schemas import ImageParameters
        from urllib.parse import urlsplit
        if not row.get('enabled'):
            raise ConfigError('model_config_disabled')
        if row.get('kind')!='image' or row.get('protocol')!='custom_http' or row.get('provider')!='comfyui':
            raise ConfigError('image_model_incompatible',422)
        if row.get('model')!='qwen-image-2.1':
            raise ConfigError('image_model_unsupported',422)
        ModelConfigInput.model_validate({k:v for k,v in row.items() if k in ModelConfigInput.model_fields})
        params=ImageParameters.model_validate(row['parameters'])
        for url in params.peer_base_urls:
            parts=urlsplit(url)
            if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
                raise ConfigError('image_peer_url_invalid',422)

    def runtime_config(self,row):
        """Retired image peers may be offline, but live/unknown peers remain mandatory."""
        from copy import deepcopy
        runtime=deepcopy(row);optional=[]
        if row.get('comfyui_api_config_id'):
            from .comfy_api_configs import ComfyApiConfigs
            services=ComfyApiConfigs(self.collection.database);server=services.get(row['comfyui_api_config_id'],require_enabled=True)
            connection=services.connection(server);runtime.update({k:v for k,v in connection.items() if k!='parameters'})
            runtime['parameters']={**row.get('parameters',{}),**connection['parameters']}
            if row['kind']=='video':runtime['parameters'].update(min_free_vram_mb=server['min_free_vram_mb'],memory_check_seconds=server['memory_check_seconds'])
            return runtime
        binding=(self.collection.find_one({'_id':'bindings'}) or {}).get('image_generation_id')
        for url in row.get('parameters',{}).get('peer_base_urls',[]):
            peers=list(self.collection.find({'record_type':'model','kind':'image','provider':'comfyui',
                'base_url':url.rstrip('/')},{'_id':1}))
            ids=[p['_id'] for p in peers]
            if not binding or not ids or binding in ids:continue
            active=self.collection.database.ai_jobs.find_one({'type':'image_generation','status':{'$in':['queued','running']},'model_config.id':{'$in':ids}})
            if not active:optional.append(url.rstrip('/'))
        runtime['_optional_peer_base_urls']=optional
        return runtime

    def bind_image_model(self,config_id):
        self.validate_image_model(self.get(config_id))
        self.collection.update_one({'_id':'bindings'},{'$set':{'record_type':'bindings',
            'schema_version':1,'image_generation_id':config_id,'updated_at':datetime.now(timezone.utc)}},upsert=True)
        return {'image_generation_id':config_id}

    def resolve_image_model(self,config_id=None):
        if not config_id:
            config_id=(self.collection.find_one({'_id':'bindings'}) or {}).get('image_generation_id')
        if not config_id:raise ConfigError('image_model_not_configured',503)
        row=self.runtime_config(self.get(str(config_id)));self.validate_image_model(row);return row

    def resolve_production_model(self,kind,config_id=None):
        if kind not in ('storyboard_generation','video_generation','image_polish'):raise ConfigError('model_usage_invalid',422)
        if not config_id:config_id=(self.collection.find_one({'_id':'bindings'}) or {}).get(kind+'_id')
        if not config_id:raise ConfigError(kind+'_model_not_configured')
        row=self.get(str(config_id))
        if kind=='video_generation':row=self.runtime_config(row)
        if kind in ('storyboard_generation','image_polish'):self.validate_entity_model(row)
        else:
            from .production_schemas import VideoParameters
            from urllib.parse import urlsplit
            if not row.get('enabled'):raise ConfigError('model_config_disabled')
            if (row['kind'],row['provider'],row['protocol'],row['model'])!=('video','comfyui','custom_http','minimax-h3-ref2va'):
                raise ConfigError('video_model_incompatible',422)
            ModelConfigInput.model_validate({k:v for k,v in row.items() if k in ModelConfigInput.model_fields})
            parameters=VideoParameters.model_validate(row['parameters'])
            for url in parameters.peer_base_urls:
                parts=urlsplit(url)
                if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
                    raise ConfigError('video_peer_url_invalid',422)
        return row

    def bind_production_model(self,kind,config_id):
        self.resolve_production_model(kind,config_id)
        self.collection.update_one({'_id':'bindings'},{'$set':{'record_type':'bindings','schema_version':1,
            kind+'_id':str(config_id),'updated_at':datetime.now(timezone.utc)}},upsert=True)
        return {kind+'_id':str(config_id)}
