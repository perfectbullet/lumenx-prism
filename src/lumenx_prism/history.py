"""Prism-only inference experiments, separate from business projects/assets."""
from datetime import datetime, timezone
import hashlib
import os
from uuid import uuid4

from pymongo import MongoClient, DESCENDING

from .prompts import build_entity_prompt


class HistoryUnavailable(RuntimeError):
    pass


class MongoHistory:
    def __init__(self, uri, database):
        if database != 'lumenx_prism':
            raise HistoryUnavailable('history_configuration_error')
        self.client = MongoClient(uri, connect=False, serverSelectionTimeoutMS=3000,
                                  connectTimeoutMS=3000, timeoutMS=5000, tz_aware=True)
        self.collection = self.client[database]['entity_extraction_runs']

    def close(self):
        self.client.close()

    def begin(self, request):
        prompt = build_entity_prompt(request.text, request.custom_extraction_prompt)
        record = {
            '_id': str(uuid4()), 'schema_version': 1,
            'created_at': datetime.now(timezone.utc), 'finished_at': None,
            'status': 'running', 'duration_ms': None,
            'request': request.model_dump(), 'effective_prompt': prompt,
            'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
            'text_preview': request.text[:100], 'text_length': len(request.text),
            'provider': os.getenv('LLM_PROVIDER', 'dashscope').lower(),
            'requested_model': (os.getenv('OPENAI_MODEL', 'gpt-4o')
                                if os.getenv('LLM_PROVIDER', 'dashscope').lower() == 'openai'
                                else 'provider-default-fallback'),
            'result': None, 'error_code': None,
        }
        self.collection.create_index([('created_at', DESCENDING), ('_id', DESCENDING)])
        self.collection.insert_one(record)
        return record['_id']

    def finish(self, record_id, *, result=None, error_code=None, duration_ms):
        update = self.collection.update_one({'_id': record_id}, {'$set': {
            'status': 'failed' if error_code else 'succeeded',
            'finished_at': datetime.now(timezone.utc), 'duration_ms': duration_ms,
            'result': result, 'error_code': error_code,
        }})
        if update.matched_count != 1:
            raise HistoryUnavailable('history_record_missing')

    def list(self, limit, offset, status=None):
        query = {'status': status} if status else {}
        projection = {'request': 0, 'effective_prompt': 0, 'result': 0}
        rows = list(self.collection.find(query, projection).sort(
            [('created_at', DESCENDING), ('_id', DESCENDING)]).skip(offset).limit(limit + 1))
        return {'items': [self.public(row) for row in rows[:limit]],
                'has_more': len(rows) > limit, 'offset': offset, 'limit': limit}

    def get(self, record_id):
        row = self.collection.find_one({'_id': record_id})
        return self.public(row) if row else None

    @staticmethod
    def public(row):
        return {'id': row['_id'], **{k: v for k, v in row.items() if k != '_id'}}
