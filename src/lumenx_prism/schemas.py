"""Versioned HTTP contract containing inference data, never business entities."""
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


from .works import WorkContext

class EntityExtractionRequest(BaseModel):
    work_context: WorkContext | None = None
    model_config = ConfigDict(extra='forbid')
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80000)]
    custom_extraction_prompt: str = Field(default='', max_length=30000)


class ExtractedEntity(BaseModel):
    # Provider extensions are not forwarded into the business database.
    id: Name | None = None
    name: Name
    description: str = ''


class ExtractedCharacter(ExtractedEntity):
    age: str | None = None
    gender: str | None = None
    clothing: str | None = None
    visual_weight: int = Field(default=3, ge=1, le=5)


class ExtractedScene(ExtractedEntity):
    time_of_day: str | None = None
    lighting_mood: str | None = None
    visual_weight: int = Field(default=3, ge=1, le=5)


class EntityExtractionResult(BaseModel):
    characters: list[ExtractedCharacter]
    scenes: list[ExtractedScene]
    props: list[ExtractedEntity]

    @model_validator(mode='after')
    def unique_reference_ids(self):
        ids = [item.id for group in (self.characters, self.scenes, self.props)
               for item in group if item.id is not None]
        if len(ids) != len(set(ids)):
            raise ValueError('Extraction reference IDs must be unique')
        return self
