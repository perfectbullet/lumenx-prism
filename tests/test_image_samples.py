import importlib.util
import io
import json
from pathlib import Path
from uuid import uuid4

import mongomock
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from lumenx_prism.api import app, get_image_samples
from lumenx_prism.image_samples import ImageSamples
from lumenx_prism.media import MediaStore


def importer():
    spec = importlib.util.spec_from_file_location('sample_import', Path(__file__).parents[1] / 'scripts/import_image_samples.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def samples():
    return ImageSamples(mongomock.MongoClient(tz_aware=True).lumenx_prism)


def add(samples, name='杯子', key='cup'):
    return samples.seed(key, kind='prop', name=name, source={'type': 'test'},
                        request={'asset_type': 'prop', 'name': name, 'description': name})


def test_seed_is_idempotent_preserves_amendments_and_type_filter(samples):
    first = add(samples)
    samples.collection.update_one({'_id': first['id']}, {'$set': {'description': '手动编辑'}})
    assert add(samples)['description'] == '手动编辑'
    assert samples.collection.count_documents({}) == 1
    add(samples, '笔记本', 'notebook')
    assert samples.list('character')['items'] == []
    page = samples.list('prop', limit=1)
    assert page['has_more'] and len(page['items']) == 1
    assert samples.list('prop', limit=1, offset=1)['items'][0]['id'] != page['items'][0]['id']


def test_source_extraction_keeps_known_traits_and_marks_missing_description(samples):
    source = {'_id': str(uuid4()), 'type': 'entity_extraction', 'status': 'succeeded', 'result': {
        'characters': [{'id': 'c1', 'name': '周屿', 'age': '32', 'gender': '男性',
                        'description': '黑色短发，戴黑框眼镜。', 'clothing': '深灰色连帽卫衣。'}],
        'scenes': [{'id': 's1', 'name': '办公室', 'description': '三块显示器，落地窗。'}],
        'props': [{'id': 'p1', 'name': '咖啡杯', 'description': ''}]}}
    rows = importer().import_entities(samples, source)
    assert len(rows) == 3
    assert rows[0]['request']['character_card']['immutable_traits'] == ['黑色短发，戴黑框眼镜。']
    assert rows[0]['request']['character_card']['body_type'] == ''
    assert rows[2]['request']['description'] == '咖啡杯'
    assert '未描述外观' in rows[2]['sample_notes']
    assert all(row['source']['job_id'] == source['_id'] for row in rows)
    importer().import_entities(samples, source)
    assert samples.collection.count_documents({}) == 3
    source['status'] = 'failed'
    with pytest.raises(ValueError): importer().import_entities(samples, source)


def test_style_import_copies_full_photo_and_separate_thumbnail(samples, tmp_path):
    root = tmp_path / 'business';(root / 'src/apps/comic_gen').mkdir(parents=True)
    photo = root / 'frontend/public/assets/styles/demo.png';photo.parent.mkdir(parents=True)
    Image.new('RGB', (1024, 768), 'blue').save(photo)
    catalog = {'version': 2, 'categories': [{'id': 'anime', 'name_zh': '动画'}], 'presets': [
        {'id': 'cel', 'name_zh': '赛璐璐', 'category': 'anime', 'description': '清晰线条',
         'positive_prompt': 'cel animation', 'negative_prompt': 'blurry', 'thumbnail': '/assets/styles/demo.png'}]}
    (root / 'src/apps/comic_gen/style_presets.json').write_text(json.dumps(catalog))
    media = MediaStore(samples.collection.database, tmp_path / 'prism-media')
    row = importer().import_styles(samples, media, root)[0]
    assert media.read_verified(row['original_media_id']) == photo.read_bytes()
    assert media.get(row['preview_media_id'])[0]['width'] <= 480
    assert row['category_name'] == '动画' and row['positive_prompt'] == 'cel animation'
    importer().import_styles(samples, media, root)
    assert samples.collection.count_documents({}) == 1
    assert media.collection.count_documents({}) == 2


def test_sample_api_auth_filter_detail_and_missing(samples, monkeypatch):
    row = add(samples)
    monkeypatch.setenv('AI_SERVICE_API_KEY', 'service-token')
    monkeypatch.setenv('PRISM_SERVICE_AUTH_ENABLED', 'true')
    app.dependency_overrides[get_image_samples] = lambda: samples
    try:
        with TestClient(app) as client:
            assert client.get('/v1/image-samples').status_code == 401
            client.headers['Authorization'] = 'Bearer service-token'
            assert client.get('/v1/image-samples?kind=prop').json()['items'][0]['id'] == row['id']
            assert client.get('/v1/image-samples?kind=style').json()['items'] == []
            assert client.get('/v1/image-samples/' + row['id']).json()['name'] == '杯子'
            assert client.get('/v1/image-samples/' + str(uuid4())).status_code == 404
            assert client.get('/v1/image-samples?kind=unknown').status_code == 422
            monkeypatch.setenv('PRISM_SERVICE_AUTH_ENABLED', 'false')
            client.headers.pop('Authorization')
            assert client.get('/v1/image-samples').status_code == 200
    finally:
        app.dependency_overrides.clear()
