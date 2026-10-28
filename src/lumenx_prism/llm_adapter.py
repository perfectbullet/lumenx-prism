"""Explicit model configuration only; runtime never reads model environment variables."""
from .model_configs import ChatParameters, ModelConfigs


class LLMAdapter:
    def __init__(self, config, timeout=120, max_retries=0):
        ModelConfigs.validate_entity_model(config)
        self.config = config
        self.provider = config['provider']
        self._client = None
        self._client_options = {'timeout': timeout, 'max_retries': max_retries}
        self.actual_model = None
        self.usage = None

    @property
    def is_configured(self):
        return bool(self.config.get('api_key'))

    def _get_default_model(self):
        return self.config['model']

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self.config['api_key'],
                                  base_url=self.config['base_url'], **self._client_options)
        return self._client

    def chat(self, messages, model=None, response_format=None):
        parameters = ChatParameters.model_validate(self.config.get('parameters', {})).model_dump(exclude_none=True)
        if response_format:
            parameters['response_format'] = response_format
        try:
            response = self._get_client().chat.completions.create(
                model=model or self.config['model'], messages=messages, **parameters)
            self.actual_model = response.model
            self.usage = response.usage.model_dump() if response.usage else None
            return response.choices[0].message.content
        except Exception:
            raise RuntimeError('Model request failed') from None
