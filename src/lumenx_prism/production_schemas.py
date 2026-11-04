"""Versioned short-film planning contracts; no business project persistence."""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

class Asset(Strict):
    name: str = Field(min_length=1,max_length=100)
    kind: Literal['character','scene','prop']
    media_id: UUID
    description: str = Field(default='',max_length=2000)

class Beat(Strict):
    start: float = Field(ge=0,le=15)
    end: float = Field(gt=0,le=15)
    action: str = Field(min_length=1,max_length=600)
    camera: str = Field(min_length=1,max_length=300)
    spatial: str = Field(min_length=1,max_length=500)
    audio: str = Field(min_length=1,max_length=500)
    handoff: str = Field(min_length=1,max_length=400)

class Dialogue(Strict):
    delivery: Literal['on_screen','off_screen','narration'] = 'on_screen'
    speaker: str = Field(min_length=1,max_length=100)
    text: str = Field(min_length=1,max_length=200)
    start: float = Field(ge=0,le=15)
    end: float = Field(gt=0,le=15)
    tone: str = Field(default='natural',max_length=200)

class Shot(Strict):
    id: str = Field(pattern=r'^S[0-9]{2,3}$')
    title: str = Field(min_length=1,max_length=150)
    duration: int = Field(default=5,ge=4,le=15)
    continuity: str = Field(min_length=1,max_length=1500)
    landmarks: str = Field(min_length=1,max_length=1000)
    positions: str = Field(min_length=1,max_length=1000)
    exited: str = Field(default='None',max_length=500)
    lighting: str = Field(min_length=1,max_length=500)
    hook: Literal['visual-joke','reversal','suspense','tender','chase','reveal','callback','expression-beat']
    asset_names: list[str] = Field(min_length=1,max_length=9)
    beats: list[Beat] = Field(min_length=1,max_length=30)
    dialogue: list[Dialogue] = Field(default_factory=list,max_length=8)
    @model_validator(mode='after')
    def check_timing(self):
        end=0
        for b in self.beats:
            if abs(b.start-end)>0.001 or b.end<=b.start or b.end-b.start>1.001:
                raise ValueError('Per-second directives must cover the shot without gaps or overlaps')
            end=b.end
        if abs(end-self.duration)>0.001: raise ValueError('Timeline must cover requested duration')
        for d in self.dialogue:
            if d.end<=d.start or d.end>self.duration: raise ValueError('Dialogue timing outside shot')
            if len(d.text)>8*(d.end-d.start): raise ValueError('Dialogue too long for allotted time')
        return self

class BoardInput(Strict):
    title: str = Field(default='短片实验',min_length=1,max_length=150)
    script: str = Field(min_length=1,max_length=30000)
    source_entity_job_id: UUID | None = None
    assets: list[Asset] = Field(min_length=1,max_length=20)
    style: str = Field(default='',max_length=2000)
    aspect_ratio: Literal['16:9','9:16'] = '16:9'
    total_duration: int = Field(default=15,ge=8,le=180)
    shot_count: int = Field(default=3,ge=2,le=36)
    scene_context: dict | None = None
    audio: Literal['chinese_dialogue_environment_no_music'] = 'chinese_dialogue_environment_no_music'
    @model_validator(mode='after')
    def check_assets(self):
        if self.scene_context is None and (self.total_duration>45 or self.shot_count>6): raise ValueError('短片入口最多45秒、6镜头；长剧本请使用剧本制作规划按场生成')
        if len({a.name for a in self.assets})!=len(self.assets): raise ValueError('Asset names must be unique')
        if not 4*self.shot_count<=self.total_duration<=15*self.shot_count: raise ValueError('Duration incompatible with shot count')
        return self

class BoardContent(Strict):
    shots: list[Shot] = Field(min_length=2,max_length=36)

class DraftInput(Strict):
    source_job_id: UUID
    revision: int | None = Field(default=None,ge=1)
    content: BoardContent

class VideoInput(Strict):
    draft_id: UUID
    revision: int = Field(ge=1)
    shot_id: str = Field(pattern=r'^S[0-9]{2,3}$')
    seed: int = Field(default=0,ge=0,le=2**53-1)
    model_config_id: UUID | None = None

class AssemblyInput(Strict):
    draft_id: UUID
    revision: int = Field(ge=1)
    video_job_ids: list[UUID] = Field(default_factory=list,max_length=36)
    selection_mode: Literal['selected','latest_succeeded'] = 'selected'

    @model_validator(mode='after')
    def assembly_selection(self):
        if self.selection_mode=='selected' and len(self.video_job_ids)<2:
            raise ValueError('请为每个镜头选择候选，或使用全部合成')
        if self.selection_mode=='latest_succeeded' and self.video_job_ids:
            raise ValueError('自动选择模式不能同时指定候选')
        if len(set(self.video_job_ids))!=len(self.video_job_ids):
            raise ValueError('合成候选不能重复')
        return self

class VideoParameters(Strict):
    workflow: Literal['h3-ref2va-v1'] = 'h3-ref2va-v1'
    resource_group: str = Field(min_length=1,max_length=100,pattern=r'^[a-zA-Z0-9_.:-]+$')
    steps: int = Field(default=20,ge=1,le=50)
    output_megapixels: float = Field(default=1.8,ge=0.2,le=2)
    poll_seconds: float = Field(default=2,ge=.05,le=30)
    timeout_seconds: int = Field(default=10800,ge=30,le=10800)
    peer_base_urls: list[str] = Field(default_factory=list,max_length=8)
    memory_check_seconds: int = Field(default=30,ge=1,le=3600)
    queue_retry_seconds: int = Field(default=5,ge=1,le=3600)
    min_free_vram_mb: int = Field(default=24000,ge=0,le=200000)
