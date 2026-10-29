"""Durable queue. The job document is authoritative; attempts are audit mirrors."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .model_configs import ConfigError, ModelConfigs
from .prompts import build_entity_prompt


def utcnow():
    return datetime.now(timezone.utc)


class Jobs:
    def __init__(self, database):
        self.collection = database['ai_jobs']
        self.attempts = database['ai_job_attempts']

    def ensure_indexes(self):
        self.collection.create_index('idempotency_key', unique=True)
        self.collection.create_index([('status', 1), ('created_at', 1)])
        self.collection.create_index([('status', 1), ('lease_until', 1)])

    @staticmethod
    def public(row):
        fields = ('type','schema_version','status','phase','created_at','started_at','finished_at',
                  'duration_ms','request','effective_prompt','prompt_sha256','model_config','provider',
                  'requested_model','result','error_code','actual_model','usage','text_preview','text_length',
                  'attempt','worker_id','lease_until','negative_prompt','reference_snapshot','workflow_snapshot',
                  'prompt_version','skill_revision')
        return {'id':row['_id'], **{k:row[k] for k in fields if k in row}}


    @staticmethod
    def fingerprint(request):
        return hashlib.sha256(json.dumps(request.model_dump(), sort_keys=True,
                                        ensure_ascii=False).encode()).hexdigest()

    def existing(self, key, request):
        row = self.collection.find_one({'idempotency_key': key})
        if row and row['request_hash'] != self.fingerprint(request):
            raise ConfigError('idempotency_conflict', 409)
        return self.public(row) if row else None

    def submit(self, request, key, config):
        self.ensure_indexes()
        old = self.existing(key, request)
        if old:
            return old
        prompt = build_entity_prompt(request.text, request.custom_extraction_prompt)
        row = dict(_id=str(uuid4()), schema_version=1, type='entity_extraction',
                   status='queued', phase='queued', created_at=utcnow(), started_at=None,
                   finished_at=None, duration_ms=None, request=request.model_dump(),
                   effective_prompt=prompt, prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
                   text_preview=request.text[:100], text_length=len(request.text),
                   idempotency_key=key, request_hash=self.fingerprint(request),
                   model_config=ModelConfigs.snapshot(config), provider=config['provider'],
                   requested_model=config['model'], result=None, error_code=None,
                   attempt=0, worker_id=None, lease_until=None)
        try:
            self.collection.insert_one(row)
        except DuplicateKeyError:
            return self.existing(key, request)
        return self.public(row)

    def get(self, job_id):
        row = self.collection.find_one({'_id': job_id})
        if not row:
            raise ConfigError('job_not_found', 404)
        return self.public(row)

    def list(self, limit, offset, status=None, job_type=None):
        query = {'status': status} if status else {}
        if job_type: query['type'] = job_type
        rows = list(self.collection.find(query, {'request': 0, 'effective_prompt': 0, 'result': 0})
                    .sort([('created_at', -1), ('_id', -1)]).skip(offset).limit(limit + 1))
        return dict(items=[self.public(row) for row in rows[:limit]], has_more=len(rows) > limit,
                    limit=limit, offset=offset)

    def recover_expired(self):
        # A synchronous upstream call cannot be safely replayed after loss of ownership.
        self.collection.update_many({'type':'entity_extraction','status': 'running', 'lease_until': {'$lte': utcnow()}},
                                    {'$set': {'status': 'failed', 'phase': 'interrupted',
                                              'error_code': 'execution_outcome_unknown',
                                              'finished_at': utcnow()}})

    def claim(self, worker_id, lease_seconds=60):
        now = utcnow()
        return self.collection.find_one_and_update(
            {'status': 'queued', 'type': 'entity_extraction'},
            {'$set': {'status': 'running', 'phase': 'preparing', 'worker_id': worker_id,
                      'claim_token': str(uuid4()), 'started_at': now,
                      'lease_until': now + timedelta(seconds=lease_seconds)}, '$inc': {'attempt': 1}},
            sort=[('created_at', 1), ('_id', 1)], return_document=ReturnDocument.AFTER)

    def ownership(self, job):
        return {'_id': job['_id'], 'status': 'running', 'claim_token': job['claim_token'],
                'lease_until': {'$gt': utcnow()}}

    def heartbeat(self, job, lease_seconds=60):
        return self.collection.update_one(self.ownership(job), {'$set': {
            'lease_until': utcnow() + timedelta(seconds=lease_seconds)}}).matched_count == 1

    def invoking(self, job):
        return self.collection.update_one(self.ownership(job), {'$set': {'phase': 'inference'}}).matched_count == 1

    def finish(self, job, *, result=None, error_code=None, actual_model=None, usage=None):
        now = utcnow()
        started = job['started_at'].replace(tzinfo=timezone.utc)
        return self.collection.update_one(self.ownership(job), {'$set': {
            'status': 'failed' if error_code else 'succeeded', 'phase': 'finished',
            'result': result, 'error_code': error_code, 'actual_model': actual_model, 'usage': usage,
            'finished_at': now, 'duration_ms': round((now - started).total_seconds() * 1000),
            'lease_until': None}}).matched_count == 1

    def mirror_attempts(self):
        # Reconcile after a crash between the authoritative update and this audit write.
        for row in self.collection.find({'attempt': {'$gt': 0}, 'audit_synced': {'$ne': True}}).limit(100):
            base = {'job_id': row['_id'], **{key: row.get(key) for key in
                    ('attempt', 'worker_id', 'started_at')}}
            self.attempts.update_one({'_id': row['claim_token']}, {'$setOnInsert': {
                **base, 'status': 'running', 'phase': 'preparing'}}, upsert=True)
            if row['status'] in ('succeeded', 'failed'):
                self.attempts.update_one({'_id': row['claim_token']}, {'$set': {
                    key: row.get(key) for key in ('finished_at', 'status', 'phase', 'error_code')}})
                self.collection.update_one({'_id': row['_id'], 'status': row['status']},
                                           {'$set': {'audit_synced': True}})
