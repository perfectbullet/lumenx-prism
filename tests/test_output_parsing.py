"""Reject ambiguous provider JSON without changing the HTTP error contract."""
import pytest
from fastapi.testclient import TestClient

from lumenx_prism.api import app, get_extractor
from lumenx_prism.extraction import EntityExtractionError, parse_entity_output
from test_entity_service import extractor


AMBIGUOUS_OUTPUTS = [
    '{"characters": [], "characters": [{"name": "幻觉"}], "scenes": [], "props": []}',
    '{"characters": [{"name": "林岚", "name": "幻觉"}], "scenes": [], "props": []}',
    '{"characters": [], "scenes": [], "props": [{"name": "钥匙", "extra": NaN}]}',
    '{"characters": [], "scenes": [], "props": [{"name": "钥匙", "extra": Infinity}]}',
    '{"characters": [], "scenes": [], "props": [{"name": "钥匙", "extra": -Infinity}]}',
]


@pytest.mark.parametrize('content', AMBIGUOUS_OUTPUTS)
def test_reject_ambiguous_or_non_json_output(content):
    with pytest.raises(EntityExtractionError, match='invalid_model_output'):
        parse_entity_output(content)


def test_ambiguous_output_keeps_sanitized_http_error(monkeypatch):
    monkeypatch.delenv('AI_SERVICE_API_KEY', raising=False)
    app.dependency_overrides[get_extractor] = lambda: extractor(AMBIGUOUS_OUTPUTS[0])
    try:
        with TestClient(app) as client:
            response = client.post('/v1/scripts/extract-entities', json={'text': '林岚回家。'})
        assert response.status_code == 502
        assert response.json() == {'detail': {'code': 'invalid_model_output'}}
    finally:
        app.dependency_overrides.clear()
