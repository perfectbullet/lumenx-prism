"""Database definitions control transport; model env vars are ignored."""
from unittest.mock import Mock, patch

import pytest

from lumenx_prism.llm_adapter import LLMAdapter


CONFIG = dict(_id='test', name='测试模型', kind='text', provider='openai',
              protocol='openai_chat', model='database-model', base_url='https://database.invalid/v1',
              api_key='database-key', enabled=True, parameters={}, revision=1)


def test_only_database_model_is_used_even_if_environment_differs(monkeypatch):
    monkeypatch.setenv('OPENAI_MODEL', 'wrong-environment-model')
    monkeypatch.setenv('OPENAI_API_KEY', 'wrong-key')
    adapter = LLMAdapter(CONFIG)
    client = Mock()
    response = client.chat.completions.create.return_value
    response.choices = [Mock(message=Mock(content='ok'))]
    response.model = 'actual-provider-model'
    response.usage.model_dump.return_value = {'total_tokens': 123}
    adapter._get_client = Mock(return_value=client)
    assert adapter.chat([]) == 'ok'
    assert client.chat.completions.create.call_args.kwargs['model'] == 'database-model'
    assert adapter.actual_model == 'actual-provider-model'
    assert adapter.usage == {'total_tokens': 123}


def test_failure_never_falls_back_to_another_model():
    adapter = LLMAdapter(CONFIG)
    client = Mock()
    client.chat.completions.create.side_effect = RuntimeError('model not found secret')
    adapter._get_client = Mock(return_value=client)
    with pytest.raises(RuntimeError, match='^Model request failed$'):
        adapter.chat([])
    client.chat.completions.create.assert_called_once()


def test_explicit_model_and_parameters():
    adapter = LLMAdapter(CONFIG | {'parameters': {'temperature': 0, 'max_tokens': 100}})
    client = Mock()
    adapter._get_client = Mock(return_value=client)
    client.chat.completions.create.return_value.choices = [Mock(message=Mock(content='ok'))]
    adapter.chat([], model='explicit-model', response_format={'type': 'json_object'})
    assert client.chat.completions.create.call_args.kwargs == {
        'model': 'explicit-model', 'messages': [], 'temperature': 0, 'max_tokens': 100,
        'response_format': {'type': 'json_object'}}


def test_provider_endpoint_and_bounded_options_come_from_database(monkeypatch):
    monkeypatch.setenv('OPENAI_BASE_URL', 'https://wrong.invalid')
    with patch('openai.OpenAI') as client:
        LLMAdapter(CONFIG, timeout=120, max_retries=0)._get_client()
    assert client.call_args.kwargs == {'api_key': 'database-key',
                                     'base_url': 'https://database.invalid/v1',
                                     'timeout': 120, 'max_retries': 0}
