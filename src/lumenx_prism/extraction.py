"""Entity inference only: prompt -> provider -> validated extraction DTO."""
import json

from pydantic import ValidationError

from .prompts import build_entity_prompt
from .schemas import EntityExtractionRequest, EntityExtractionResult


class EntityExtractionError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def parse_entity_output(content: str) -> EntityExtractionResult:
    try:
        content = content.strip()
        if content.startswith('```'):
            lines = content.splitlines()
            if len(lines) < 3 or lines[-1].strip() != '```':
                raise ValueError('Incomplete JSON fence')
            content = '\n'.join(lines[1:-1])
        return EntityExtractionResult.model_validate(json.loads(content))
    except (AttributeError, TypeError, ValueError, ValidationError):
        raise EntityExtractionError('invalid_model_output') from None


class EntityExtractor:
    def __init__(self, llm):
        self.llm = llm

    def extract(self, request: EntityExtractionRequest) -> EntityExtractionResult:
        if not self.llm.is_configured:
            raise EntityExtractionError('not_configured')
        prompt = build_entity_prompt(request.text, request.custom_extraction_prompt)
        try:
            content = self.llm.chat(messages=[{'role': 'user', 'content': prompt}])
        except Exception:
            # No provider exception text: it may contain credentials or script data.
            raise EntityExtractionError('upstream_error') from None
        return parse_entity_output(content)
