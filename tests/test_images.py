from datetime import timedelta
from uuid import UUID, uuid4
import io
import json
from unittest.mock import Mock

import mongomock
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from pydantic import ValidationError

from lumenx_prism.api import app,get_jobs,get_model_configs,get_media
from lumenx_prism.jobs import Jobs,utcnow
from lumenx_prism.image_jobs import ImageJobs,ImageWorker
from lumenx_prism.image_schemas import ImageRequest,CharacterCard
from lumenx_prism.image_prompts import compile_image_prompt
from lumenx_prism.model_configs import ModelConfigs,ModelConfigInput,ConfigError
from lumenx_prism.media import MediaStore
from lumenx_prism.comfy_images import build_workflow


def png():
    stream=io.BytesIO();Image.new('RGB',(64,64),'blue').save(stream,format='PNG');return stream.getvalue()


@pytest.fixture
def setup(tmp_path):
    db=mongomock.MongoClient(tz_aware=True)['lumenx_prism'];jobs=ImageJobs(db);configs=ModelConfigs(db)
    config=configs.save(ModelConfigInput(name='Local Qwen',kind='image',provider='comfyui',protocol='custom_http',
        model='qwen-image-2.1',base_url='http://comfy.invalid',parameters={'resource_group':'gpu0','poll_seconds':0.05}))
    configs.bind_image_model(config['id'])
    media=MediaStore(db,tmp_path/'media')
    request=ImageRequest(asset_type='character',generation_type='reference_sheet',
        character_card=CharacterCard(name='Lin Xia',immutable_traits=['black hair','navy jacket']))
    adapter=Mock();adapter.params=None;adapter.idle.return_value=True;adapter.submit.return_value='provider-id'
    adapter.history.return_value={'status':{'completed':True,'status_str':'success'},
        'outputs':{'43':{'images':[{'filename':'out.png','subfolder':'prism','type':'output'}]}}}
    adapter.download.return_value=png();adapter.upload.return_value='prism/ref.png'
    return jobs,configs,media,request,adapter


def run(setup):
    jobs,configs,media,request,adapter=setup
    row=jobs.submit_image(request,'key',configs.resolve_image_model(),media)
    worker=ImageWorker(jobs,configs,media,'worker',adapter_factory=lambda _:adapter)
    worker.run_once()
    return jobs.get(row['id']),worker


def test_character_card_includes_all_skill_elements(setup):
    prompt,negative=compile_image_prompt(setup[3])
    for word in ('3/4','FRONT','SIDE','BACK','expression','MATERIALS','COSTUME','PROP','VISUAL ID','DO NOT CHANGE'):
        assert word in prompt
    assert 'no text' not in negative
    assert prompt.count('black hair')>=2
    custom=setup[3].model_copy(update={'style':'2D cel shading'})
    assert '3D animated feature' not in compile_image_prompt(custom)[0]


def test_scene_card_has_no_people():
    prompt,_=compile_image_prompt(ImageRequest(asset_type='scene',description='教室'))
    assert 'Environment-only' in prompt and 'No characters' in prompt and 'landmarks' in prompt


def test_derived_requires_reference_and_identity(setup):
    with pytest.raises(ValidationError):ImageRequest(asset_type='character',generation_type='headshot',description='x')
    with pytest.raises(ValidationError):ImageRequest(**(setup[3].model_dump()|{'generation_type':'three_view'}))


def test_image_success_idempotency_media_and_private_fields(setup):
    row,worker=run(setup);jobs,configs,media,request,adapter=setup
    assert row['status']=='succeeded' and row['result']['review_required']
    artifact=row['result']['artifacts'][0]
    assert media.read_verified(artifact['id'])==png()
    assert jobs.submit_image(request,'key',configs.resolve_image_model(),media)['id']==row['id']
    with pytest.raises(ConfigError,match='idempotency_conflict'):
        jobs.submit_image(request.model_copy(update={'seed':2}),'key',configs.resolve_image_model(),media)
    assert 'executions' not in row and 'comfy.invalid' not in json.dumps(row,default=str)
    assert not worker.run_once();adapter.submit.assert_called_once()
    assert jobs.collection.database.ai_resources.count_documents({})==0


def test_batch_is_serial_and_independently_recoverable(setup):
    jobs,configs,media,request,adapter=setup
    request.batch_size=3
    row,_=run(setup)
    assert len(row['result']['artifacts'])==3 and adapter.submit.call_count==3
    graphs=[call.args[0] for call in adapter.submit.call_args_list]
    assert [graph['23']['inputs']['seed'] for graph in graphs]==[0,1,2]
    assert all(graph['36']['inputs']['batch_size']==1 for graph in graphs)


def test_expired_image_resumes_upstream_id_not_submit(setup):
    jobs,configs,media,request,adapter=setup
    row=jobs.submit_image(request,'key',configs.resolve_image_model(),media)
    old=jobs.claim_image('dead',60)
    jobs.update_owned(old,executions=[{'state':'submitted','index':0,'prompt_id':'existing','submission_id':'key'}])
    jobs.collection.update_one({'_id':old['_id']},{'$set':{'lease_until':utcnow()-timedelta(seconds=1)}})
    Jobs(jobs.collection.database).recover_expired()
    assert jobs.get(row['id'])['status']=='running'
    ImageWorker(jobs,configs,media,'replacement',adapter_factory=lambda _:adapter).run_once()
    assert jobs.get(row['id'])['status']=='succeeded';adapter.submit.assert_not_called()
    assert not jobs.finish(old,result={})


def test_uncertain_submission_never_replayed_and_gpu_slot_retained(setup):
    jobs,configs,media,request,adapter=setup
    row=jobs.submit_image(request,'key',configs.resolve_image_model(),media)
    old=jobs.claim_image('dead',60)
    jobs.update_owned(old,executions=[{'state':'submitting','index':0,'submission_id':'key'}])
    jobs.collection.update_one({'_id':old['_id']},{'$set':{'lease_until':utcnow()-timedelta(seconds=1)}})
    adapter.locate.return_value=None
    ImageWorker(jobs,configs,media,'replacement',adapter_factory=lambda _:adapter).run_once()
    assert jobs.get(row['id'])['error_code']=='execution_outcome_unknown'
    adapter.submit.assert_not_called()
    assert jobs.collection.database.ai_resources.find_one()['job_id']==row['id']


def test_uncertain_submission_can_be_reconciled(setup):
    jobs,configs,media,request,adapter=setup
    row=jobs.submit_image(request,'key',configs.resolve_image_model(),media);old=jobs.claim_image('dead',60)
    jobs.update_owned(old,executions=[{'state':'submitting','index':0,'submission_id':'key'}])
    jobs.collection.update_one({'_id':old['_id']},{'$set':{'lease_until':utcnow()-timedelta(seconds=1)}})
    adapter.locate.return_value='found'
    ImageWorker(jobs,configs,media,'replacement',adapter_factory=lambda _:adapter).run_once()
    assert jobs.get(row['id'])['status']=='succeeded';adapter.submit.assert_not_called()


def test_busy_resource_requeues_without_generation(setup):
    jobs,configs,media,request,adapter=setup
    jobs.collection.database.ai_resources.insert_one({'_id':'gpu0','job_id':'other'})
    row,_=run(setup)
    assert row['status']=='queued' and row['phase']=='waiting_for_resource'
    adapter.submit.assert_not_called()


def test_revision_change_does_not_call_provider(setup):
    jobs,configs,media,request,adapter=setup
    row=jobs.submit_image(request,'key',configs.resolve_image_model(),media)
    configs.collection.update_one({'_id':row['model_config']['id']},{'$inc':{'revision':1}})
    ImageWorker(jobs,configs,media,'worker',adapter_factory=lambda _:adapter).run_once()
    assert jobs.get(row['id'])['error_code']=='model_config_changed';adapter.submit.assert_not_called()


def test_reference_snapshot_and_wire_order(setup):
    jobs,configs,media,request,adapter=setup
    ref=media.put(png());request.reference_media_ids=[UUID(ref['id'])];request.generation_type='three_view'
    row,_=run(setup)
    graph=adapter.submit.call_args.args[0]
    assert graph['37']['inputs']['images.image_1']==['100',0]
    assert graph['100']['inputs']['image']=='prism/ref.png'
    assert row['reference_snapshot'][0]['sha256']==ref['sha256']


def test_media_rejects_invalid_content_and_tampering(setup):
    media=setup[2]
    with pytest.raises(ConfigError,match='invalid_image_file'):media.put(b'<html>not image</html>')
    ref=media.put(png());row,path=media.get(ref['id']);path.write_bytes(b'changed')
    with pytest.raises(ConfigError,match='media_content_changed'):media.read_verified(ref['id'])
    media.collection.update_one({'_id':ref['id']},{'$set':{'relative_path':'../../etc/passwd'}})
    with pytest.raises(ConfigError,match='media_file_unavailable'):media.get(ref['id'])


def test_http_upload_preview_submit_and_download(setup,monkeypatch):
    jobs,configs,media,request,adapter=setup
    monkeypatch.setenv('AI_SERVICE_API_KEY','token')
    app.dependency_overrides[get_jobs]=lambda:jobs
    app.dependency_overrides[get_model_configs]=lambda:configs
    app.dependency_overrides[get_media]=lambda:media
    try:
        with TestClient(app) as client:
            assert client.post('/v1/images/generate/jobs',json=request.model_dump(mode='json')).status_code==401
            client.headers['Authorization']='Bearer token'
            upload=client.post('/v1/media',files={'file':('ref.png',png(),'image/png')})
            assert upload.status_code==201
            assert client.get(upload.json()['download_url']).content==png()
            assert client.post('/v1/images/preview',json=request.model_dump(mode='json')).status_code==200
            response=client.post('/v1/images/generate/jobs',json=request.model_dump(mode='json'),headers={'Idempotency-Key':'test'})
            assert response.status_code==202
            ImageWorker(jobs,configs,media,'worker',adapter_factory=lambda _:adapter).run_once()
            result=client.get(response.headers['location']).json()
            assert result['status']=='succeeded'
            assert client.get(result['result']['artifacts'][0]['download_url']).content==png()
            assert client.get('/tools/images').status_code==200
    finally:app.dependency_overrides.clear()


@pytest.mark.parametrize('kind',['full_body','three_view','headshot','reference_sheet'])
def test_all_character_prompt_modes(setup,kind):
    from uuid import uuid4
    req=setup[3].model_copy(update={'generation_type':kind,'reference_media_ids':[uuid4()], 'negative_prompt':'text, watermark'})
    prompt,negative=compile_image_prompt(req)
    assert prompt
    if kind=='reference_sheet':assert 'text' not in negative.split(', ')
    if kind=='headshot':assert 'exactly ONE' in prompt and 'reference sheet' in negative


@pytest.mark.parametrize('kind', ['full_body', 'headshot'])
def test_single_character_outputs_do_not_inherit_multi_view_constraints(setup, kind):
    request = ImageRequest(**(setup[3].model_dump() | {
        'generation_type': kind, 'negative_prompt': 'text, missing required views'}))
    prompt, negative = compile_image_prompt(request)
    assert 'across ALL views' not in prompt
    assert 'missing required views' not in negative
    assert 'exactly ONE' in prompt
    assert 'expression strip:' not in prompt
    assert 'panels' in negative and 'reference sheet' in negative
    assert 'listed clothing' in prompt
    if kind == 'full_body':
        assert 'head to toe' in prompt and 'feet visible' in prompt
    else:
        assert '根据下方人物描述' in prompt and '将参考图' not in prompt


def test_headshot_with_reference_preserves_identity_instruction(setup):
    request = ImageRequest(**(setup[3].model_dump() | {
        'generation_type': 'headshot', 'reference_media_ids': [str(uuid4())]}))
    prompt, _ = compile_image_prompt(request)
    assert '将参考图中的同一个人物' in prompt
    assert 'image 1' in prompt


def test_three_view_keeps_exactly_three_angles(setup):
    request = ImageRequest(**(setup[3].model_dump() | {
        'generation_type': 'three_view', 'reference_media_ids': [str(uuid4())]}))
    prompt, negative = compile_image_prompt(request)
    assert 'exactly THREE' in prompt and 'FRONT / SIDE / BACK' in prompt
    assert 'missing required views' in negative
    assert 'expression strip:' not in prompt


def test_worker_uses_frozen_prompt_after_compiler_changes(setup, monkeypatch):
    jobs, configs, media, request, adapter = setup
    request = ImageRequest(**(request.model_dump() | {'generation_type': 'headshot'}))
    row = jobs.submit_image(request, 'frozen-prompt', configs.resolve_image_model(), media)
    assert row['prompt_version'] == 'asset-production-v6'
    monkeypatch.setattr('lumenx_prism.image_jobs.compile_image_prompt', lambda _: ('different prompt', 'different negative'))
    ImageWorker(jobs, configs, media, 'worker', adapter_factory=lambda _: adapter).run_once()
    assert jobs.get(row['id'])['status'] == 'succeeded'
    graph = adapter.submit.call_args.args[0]
    assert graph['37']['inputs']['prompt'] == row['effective_prompt']
    assert graph['37']['inputs']['negative_prompt'] == row['negative_prompt']


def test_image_job_freezes_original_and_effective_asset_style(setup):
    jobs, configs, media, _, adapter = setup
    request = ImageRequest(asset_type='prop', name='手机', description='消息屏幕',
                           style='3D render, expressive character design, warm lighting')
    row = jobs.submit_image(request, 'typed-style', configs.resolve_image_model(), media)
    assert row['style_snapshot']['original'] == request.style
    assert row['style_snapshot']['effective'] == '3D render, warm lighting'
    assert row['request']['style'] == request.style
    assert 'expressive character design' not in row['effective_prompt']


@pytest.mark.parametrize('seed', [0, 2**53-1])
def test_image_seed_accepts_safe_integer_boundaries(seed):
    assert ImageRequest(asset_type='prop', description='杯子', seed=seed).seed == seed


@pytest.mark.parametrize('seed', [-1, 2**53, .5])
def test_image_seed_rejects_out_of_range_or_fractional_values(seed):
    with pytest.raises(ValidationError):
        ImageRequest(asset_type='prop', description='杯子', seed=seed)
