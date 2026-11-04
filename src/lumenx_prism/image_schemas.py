"""Image task contracts. Business IDs are opaque tracing fields, not Prism entities."""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator


class CharacterCard(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=100)
    role: str = Field(default='', max_length=200)
    age_range: str = Field(default='', max_length=100)
    body_type: str = Field(default='', max_length=300)
    hairstyle: str = Field(default='', max_length=300)
    costume: str = Field(default='', max_length=1500)
    props: list[str] = Field(default_factory=list, max_length=12)
    immutable_traits: list[str] = Field(min_length=1, max_length=20)
    expressions: list[str] = Field(default_factory=lambda: ['neutral', 'happy', 'sad', 'angry'], min_length=1, max_length=6)


class ImageRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    prompt_mode: Literal['asset', 'raw'] = 'asset'
    asset_type: Literal['character', 'scene', 'prop', 'image'] = 'image'
    generation_type: Literal['image', 'reference_sheet', 'full_body', 'three_view', 'headshot'] = 'image'
    name: str = Field(default='', max_length=100)
    description: str = Field(default='', max_length=6000)
    prompt: str = Field(default='', max_length=12000)
    style: str = Field(default='', max_length=4000)
    negative_prompt: str = Field(default='', max_length=3000)
    character_card: CharacterCard | None = None
    reference_media_ids: list[UUID] = Field(default_factory=list, max_length=4)
    model_config_id: UUID | None = None
    width: int = Field(default=1344, ge=256, le=2048, multiple_of=32)
    height: int = Field(default=768, ge=256, le=2048, multiple_of=32)
    seed: int = Field(default=0, ge=0, le=2**53-1)
    batch_size: int = Field(default=1, ge=1, le=4)
    source_context: dict[str, str] = Field(default_factory=dict, max_length=12)

    @model_validator(mode='after')
    def check_request(self):
        if self.prompt_mode == 'raw':
            if self.asset_type != 'image' or self.generation_type != 'image' or self.character_card or self.style or self.description:
                raise ValueError('Raw prompt mode requires a plain image without asset descriptions or style templates')
            if not self.prompt:
                raise ValueError('Raw prompt mode requires a prompt')
        if self.width * self.height > 2097152:
            raise ValueError('At most 2 megapixels per image')
        if not (self.prompt or self.description or self.character_card):
            raise ValueError('Description or prompt is required')
        if self.asset_type == 'character':
            if self.generation_type == 'image':
                raise ValueError('Choose a character output type')
            if self.character_card is None:
                raise ValueError('Character card identity specification is required')
            if self.generation_type == 'three_view' and not self.reference_media_ids:
                raise ValueError('Three-view turnaround requires a reference image')
        elif self.generation_type != 'image':
            raise ValueError('Character output type requires asset_type=character')
        if len(self.reference_media_ids) != len(set(self.reference_media_ids)):
            raise ValueError('Duplicate reference images')
        if any(len(k)>100 or len(v)>1000 for k,v in self.source_context.items()):
            raise ValueError('Source context too long')
        if self.character_card:
            for values in (self.character_card.props, self.character_card.immutable_traits, self.character_card.expressions):
                if any(not v.strip() or len(v)>1000 for v in values):
                    raise ValueError('Invalid character trait')
        return self


class ImageParameters(BaseModel):
    model_config = ConfigDict(extra='forbid')
    workflow: Literal['qwen-image21-v1'] = 'qwen-image21-v1'
    steps: int = Field(default=20, ge=1, le=50)
    cfg: float = Field(default=1, ge=1, le=10)
    # Models on one physical GPU must share this identifier, regardless of port.
    resource_group: str = Field(min_length=1, max_length=100, pattern=r'^[a-zA-Z0-9_.:-]+$')
    peer_base_urls: list[str] = Field(default_factory=list, max_length=8)
    poll_seconds: float = Field(default=2, ge=0.05, le=30)
    queue_retry_seconds: int = Field(default=5,ge=1,le=3600)
    timeout_seconds: int = Field(default=1200, ge=30, le=7200)
    reference_resolution: int = Field(default=768, ge=256, le=1536, multiple_of=32)
