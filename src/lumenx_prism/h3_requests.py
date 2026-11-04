"""Independent full-reference H3 requests; no work, episode or shot dependencies."""
from typing import Literal
from uuid import UUID
from pydantic import Field, field_validator, model_validator
from .production_schemas import Strict

ASPECTS={'auto':'自动','21:9':'21:9 超宽屏','16:9':'16:9 横屏','4:3':'4:3 横屏','1:1':'1:1 方形','3:4':'3:4 竖屏','9:16':'9:16 竖屏'}

class H3ReferenceInput(Strict):
    prompt_mode: Literal['raw']='raw'
    name: str=Field(default='',max_length=100)
    prompt: str=Field(min_length=1,max_length=16000)
    reference_media_ids: list[UUID]=Field(min_length=1,max_length=9)
    duration_seconds: float=Field(default=4,ge=4,le=15,allow_inf_nan=False)
    aspect_ratio: Literal['auto','21:9','16:9','4:3','1:1','3:4','9:16']='16:9'
    seed: int=Field(default=20261016,ge=0,le=2**53-1)
    steps: int=Field(default=20,ge=1,le=50)
    output_megapixels: float=Field(default=1.8,ge=.2,le=2,allow_inf_nan=False)
    ref_image_size: Literal['匹配输出（推荐）','最高保真（更慢）']='匹配输出（推荐）'
    model_config_id: UUID | None=None

    @field_validator('prompt')
    @classmethod
    def nonblank(cls,value):
        if not value.strip():raise ValueError('请填写提示词')
        return value

    @model_validator(mode='after')
    def distinct_references(self):
        if len(set(self.reference_media_ids))!=len(self.reference_media_ids):raise ValueError('参考图不能重复')
        return self
