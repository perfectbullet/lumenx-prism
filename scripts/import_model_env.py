"""One-time migration only. Runtime inference never reads legacy model env keys."""
import argparse
from pathlib import Path
import sys

from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from lumenx_prism.history import MongoHistory
from lumenx_prism.model_configs import ModelConfigs, ModelConfigInput, ConfigError

MODEL_KEYS = {'LLM_PROVIDER', 'OPENAI_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_MODEL',
              'DASHSCOPE_API_KEY', 'DASHSCOPE_BASE_URL'}


def migrate(path, remove_model_env=False):
    values = dotenv_values(path, interpolate=False)
    history = MongoHistory(values['PRISM_MONGODB_URI'], values.get('PRISM_MONGODB_DATABASE', 'lumenx_prism'))
    try:
        repo = ModelConfigs(history.client['lumenx_prism'])
        try:
            row = repo.resolve_entity_model()
            config_id = row['_id']
        except ConfigError as exc:
            if exc.code != 'model_not_configured':
                raise
            imported = repo.collection.find_one({'record_type': 'model', 'migration_source': 'legacy_env_v1'})
            if imported:
                config_id = imported['_id']
            else:
                provider = values.get('LLM_PROVIDER', 'openai').lower()
                if provider == 'dashscope':
                    key = values.get('DASHSCOPE_API_KEY')
                    base = (values.get('DASHSCOPE_BASE_URL') or 'https://dashscope.aliyuncs.com').rstrip('/')
                    if not base.endswith('/compatible-mode/v1'):
                        base += '/compatible-mode/v1'
                    model = values.get('OPENAI_MODEL') or 'qwen-plus'
                else:
                    key, base, model = (values.get(k) for k in ('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'OPENAI_MODEL'))
                if not all((key, base, model)):
                    raise ValueError('Legacy model configuration is incomplete')
                created = repo.save(ModelConfigInput(
                    name='实体提取主模型', kind='text', provider=provider, protocol='openai_chat',
                    model=model, base_url=base, api_key=key, capabilities=['entity_extraction'],
                    notes='从 Prism 原有环境配置一次性迁入；后续在模型配置页维护。'))
                config_id = created['id']
                repo.collection.update_one({'_id': config_id}, {'$set': {'migration_source': 'legacy_env_v1'}})
            repo.bind_entity_model(config_id)
        # Read back from MongoDB before removing obsolete local keys.
        resolved = repo.resolve_entity_model()
        if remove_model_env:
            lines = path.read_text().splitlines()
            lines = [line for line in lines if line.split('=', 1)[0].strip().removeprefix('export ') not in MODEL_KEYS]
            path.write_text('\n'.join(lines) + '\n')
            path.chmod(0o600)
        return {'id': resolved['_id'], 'model': resolved['model'], 'revision': resolved['revision']}
    finally:
        history.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=Path('.env'))
    parser.add_argument('--remove-model-env', action='store_true')
    args = parser.parse_args()
    try:
        print(migrate(args.env_file, args.remove_model_env))
    except Exception as exc:
        print('Model migration failed:', type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
