"""Explicit one-time migration preserves independent env settings and is idempotent."""
import importlib.util
from pathlib import Path
from unittest.mock import Mock

import mongomock
import pytest

spec=importlib.util.spec_from_file_location('migration',Path(__file__).parents[1]/'scripts/import_model_env.py')
migration=importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def test_migration_reads_back_then_removes_only_old_model_variables(tmp_path,monkeypatch):
    client=mongomock.MongoClient()
    monkeypatch.setattr(migration,'MongoHistory',lambda *args:Mock(client=client))
    path=tmp_path/'.env'
    path.write_text('PRISM_MONGODB_URI=mongodb://localhost/lumenx_prism\nAI_SERVICE_API_KEY=service-token\nLLM_PROVIDER=openai\nOPENAI_MODEL=test-model\nOPENAI_BASE_URL=https://example.invalid/v1\nOPENAI_API_KEY=secret-model-key\n')
    first=migration.migrate(path,True)
    assert 'secret-model-key' not in path.read_text()
    assert 'AI_SERVICE_API_KEY=service-token' in path.read_text()
    second=migration.migrate(path,True)
    assert first==second
    assert client.lumenx_prism.model_comfigs.count_documents({'record_type':'model'})==1
    assert client.lumenx_prism.model_comfigs.find_one({'_id':first['id']})['api_key']=='secret-model-key'


def test_invalid_migration_never_erases_legacy_configuration(tmp_path,monkeypatch):
    monkeypatch.setattr(migration,'MongoHistory',lambda *args:Mock(client=mongomock.MongoClient()))
    path=tmp_path/'.env'
    original='PRISM_MONGODB_URI=mongodb://localhost\nOPENAI_API_KEY=secret\n'
    path.write_text(original)
    with pytest.raises(ValueError):
        migration.migrate(path,True)
    assert path.read_text()==original
