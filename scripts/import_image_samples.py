"""Copy reference fixtures into Prism. Never import LumenX Python or touch its DB."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image
from lumenx_prism.history import MongoHistory
from lumenx_prism.image_samples import ImageSamples
from lumenx_prism.media import MediaStore


def import_styles(samples, media, business_root):
    path = business_root / 'src/apps/comic_gen/style_presets.json'
    content = path.read_bytes()
    catalog = json.loads(content)
    categories = {r['id']: r['name_zh'] for r in catalog['categories']}
    public = (business_root / 'frontend/public').resolve()
    files = [(r, (public / r['thumbnail'].lstrip('/')).resolve()) for r in catalog['presets']]
    for _, photo in files:
        if not photo.is_relative_to(public) or not photo.is_file():
            raise ValueError('Style image missing or outside public directory')
    imported = []
    for index, (preset, photo) in enumerate(files):
        full = media.put(photo.read_bytes(), artifact_key='style-original:' + preset['id'])
        with Image.open(photo) as im:
            im = im.convert('RGB')
            im.thumbnail((480, 320))
            output = io.BytesIO(); im.save(output, format='JPEG', quality=85)
        thumb = media.put(output.getvalue(), artifact_key='style-thumbnail:' + preset['id'])
        imported.append(samples.seed('lumenx-style:' + preset['id'], kind='style',
            name=preset['name_zh'], sort_order=index,
            source={'type': 'lumenx_style_preset', 'preset_id': preset['id'],
                    'catalog_version': catalog['version'], 'sha256': hashlib.sha256(content).hexdigest()},
            category=preset['category'], category_name=categories[preset['category']],
            description=preset['description'], subtitle=preset.get('subtitle_zh', ''),
            positive_prompt=preset['positive_prompt'], negative_prompt=preset['negative_prompt'],
            best_for=preset.get('best_for', []), avoid_for=preset.get('avoid_for', []),
            example_prompt=preset.get('sample_prompt', ''), preview_media_id=thumb['id'],
            original_media_id=full['id'], object_position=preset.get('object_position', 'center')))
    return imported


def import_entities(samples, job):
    if job.get('type') != 'entity_extraction' or job.get('status') != 'succeeded':
        raise ValueError('A succeeded entity extraction job is required')
    result = job['result']
    imported = []
    def save(kind, entity, request, suffix='', notes=''):
        row = samples.seed(f"extraction:{job['_id']}:{kind}:{entity['id']}:{suffix}",
            kind=kind, name=request['name'], request=request,
            description=request['description'], sample_notes=notes,
            sort_order=100 + len(imported),
            source={'type': 'entity_extraction_job', 'job_id': job['_id'],
                    'entity_id': entity['id'], 'adaptation': suffix or 'original'})
        imported.append(row)
    for entity in result.get('characters', []):
        description = ' '.join(str(entity.get(k) or '') for k in ('age', 'gender', 'description', 'clothing')).strip()
        traits = [entity['description']] if entity.get('description') else [entity['name']]
        save('character', entity, {'asset_type': 'character', 'generation_type': 'full_body', 'width': 768, 'height': 1344,
             'name': entity['name'], 'description': description,
             'character_card': {'name': entity['name'], 'age_range': str(entity.get('age') or ''),
                  'costume': entity.get('clothing') or '', 'immutable_traits': traits}},
             notes='提取结果中未描述的体型、瞳色等保持空白；可在生成前补充。')
    for entity in result.get('scenes', []):
        save('scene', entity, {'asset_type': 'scene', 'name': entity['name'],
             'description': entity.get('description') or entity['name']},
             notes='采用原提取描述；如同时包含多个时段，可自行选定一个时段。')
    for entity in result.get('props', []):
        description = entity.get('description') or ''
        save('prop', entity, {'asset_type': 'prop', 'name': entity['name'],
             'description': description or entity['name']},
             notes='原提取结果未描述外观，当前只使用道具名称，可自行补充材质与颜色。' if not description else '')
    return imported


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', default='.env')
    parser.add_argument('--lumenx-root', type=Path, required=True)
    parser.add_argument('--source-job-id', required=True)
    args = parser.parse_args()
    load_dotenv(args.env_file)
    history = MongoHistory(os.environ['PRISM_MONGODB_URI'], os.getenv('PRISM_MONGODB_DATABASE', 'lumenx_prism'))
    try:
        db = history.client['lumenx_prism']
        job = db.ai_jobs.find_one({'_id': args.source_job_id})
        if not job or job.get('type') != 'entity_extraction' or job.get('status') != 'succeeded':
            raise ValueError('Source extraction task missing or not succeeded')
        samples, media = ImageSamples(db), MediaStore(db)
        styles = import_styles(samples, media, args.lumenx_root)
        entities = import_entities(samples, job)
        print(f'Imported/kept {len(styles)} styles and {len(entities)} entity samples in Prism image_test_samples.')
    finally:
        history.close()


if __name__ == '__main__':
    main()
