"""Prism test fixtures, independent from formal business assets and model settings."""
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from .image_schemas import ImageRequest
from .model_configs import ConfigError


class ImageSamples:
    def __init__(self, database):
        self.collection = database['image_test_samples']

    @staticmethod
    def public(row):
        return {'id': row['_id'], **{k: v for k, v in row.items() if k != '_id'}}

    def list(self, kind=None, limit=100, offset=0):
        query = {'kind': kind} if kind else {}
        rows = list(self.collection.find(query).sort([('sort_order', 1), ('_id', 1)])
                    .skip(offset).limit(limit + 1))
        return {'items': [self.public(row) for row in rows[:limit]],
                'has_more': len(rows) > limit, 'limit': limit, 'offset': offset}

    def get(self, sample_id):
        row = self.collection.find_one({'_id': sample_id})
        if row is None:
            raise ConfigError('image_sample_not_found', 404)
        return self.public(row)

    def seed(self, key, *, kind, name, source, sort_order=0, **content):
        if kind not in ('style', 'character', 'scene', 'prop'):
            raise ValueError('Unsupported sample kind')
        if kind == 'style':
            if not content.get('positive_prompt'):
                raise ValueError('Style prompt required')
        else:
            request = ImageRequest.model_validate(content['request'])
            if request.asset_type != kind:
                raise ValueError('Sample kind does not match request')
            content['request'] = request.model_dump(mode='json')
        sample_id = str(uuid5(NAMESPACE_URL, 'prism:image-sample:' + key))
        row = dict(_id=sample_id, schema_version=1, kind=kind, name=name, source=source,
                   sort_order=sort_order, created_at=datetime.now(timezone.utc), **content)
        # Repeat imports preserve existing records and any later manual amendments.
        self.collection.update_one({'_id': sample_id}, {'$setOnInsert': row}, upsert=True)
        self.collection.create_index([('kind', 1), ('sort_order', 1)])
        return self.get(sample_id)
