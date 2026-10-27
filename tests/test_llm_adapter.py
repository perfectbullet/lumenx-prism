"""Keep provider behavior stable when moving transport outside Studio."""
from unittest.mock import Mock, patch

import pytest

from lumenx_prism.llm_adapter import LLMAdapter


def test_model_unavailable_uses_original_fallback_order(monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'dashscope')
    adapter = LLMAdapter()
    adapter._get_client = Mock(return_value=object())
    adapter._chat_once = Mock(side_effect=[RuntimeError('model not found'),
                                          RuntimeError('InvalidModel'), 'ok'])
    assert adapter.chat([]) == 'ok'
    assert [call.args[1] for call in adapter._chat_once.call_args_list] == [
        'qwen3.7-plus', 'qwen3.6-plus', 'qwen-plus']


def test_authentication_failure_never_tries_next_model(monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'dashscope')
    adapter = LLMAdapter()
    adapter._get_client = Mock(return_value=object())
    adapter._chat_once = Mock(side_effect=RuntimeError('invalid API key'))
    with pytest.raises(RuntimeError):
        adapter.chat([])
    assert adapter._chat_once.call_count == 1


def test_explicit_model_has_no_fallback(monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'dashscope')
    adapter = LLMAdapter()
    adapter._get_client = Mock(return_value=object())
    adapter._chat_once = Mock(side_effect=RuntimeError('model not found'))
    with pytest.raises(RuntimeError):
        adapter.chat([], model='explicit-model')
    assert adapter._chat_once.call_count == 1


def test_provider_endpoint_and_bounded_service_options(monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'dashscope')
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'fake-key')
    monkeypatch.setenv('DASHSCOPE_BASE_URL', 'https://example.invalid/')
    with patch('openai.OpenAI') as client:
        LLMAdapter(timeout=120, max_retries=0)._get_client()
    assert client.call_args.kwargs == {
        'api_key': 'fake-key', 'base_url': 'https://example.invalid/compatible-mode/v1',
        'timeout': 120, 'max_retries': 0,
    }
