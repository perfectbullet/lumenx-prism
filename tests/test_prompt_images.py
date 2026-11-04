import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from test_images import setup, png
from lumenx_prism.api import app,get_jobs,get_model_configs,get_media
from lumenx_prism.image_jobs import ImageJobs,ImageWorker
from lumenx_prism.image_schemas import ImageRequest
from lumenx_prism.image_prompts import compile_image_prompt


def test_raw_prompt_has_no_asset_or_style_instructions():
    r=ImageRequest(prompt_mode='raw',prompt='平面黑白建筑线稿，不要人物',negative_prompt='彩色，模糊')
    assert compile_image_prompt(r)==(r.prompt,r.negative_prompt)
    with pytest.raises(ValidationError):
        ImageRequest(prompt_mode='raw',prompt='test',style='3d')
    with pytest.raises(ValidationError):
        ImageRequest(prompt_mode='raw',description='description only')


def test_asset_fingerprint_remains_compatible():
    r=ImageRequest(prompt='old request')
    old=r.model_dump(mode='json');old.pop('prompt_mode')
    expected=hashlib.sha256(json.dumps({'type':'image_generation','request':old},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    assert ImageJobs.fingerprint(r)==expected


def test_raw_prompt_job_roundtrip_and_history_isolation(setup,monkeypatch):
    jobs,configs,media,_,adapter=setup
    monkeypatch.setenv('AI_SERVICE_API_KEY','test-token')
    app.dependency_overrides[get_jobs]=lambda:jobs
    app.dependency_overrides[get_model_configs]=lambda:configs
    app.dependency_overrides[get_media]=lambda:media
    try:
        with TestClient(app) as client:
            assert client.get('/tools/prompt-images').status_code==200
            p={'prompt_mode':'raw','prompt':'极简黑白钢笔画','negative_prompt':'彩色','batch_size':1}
            assert client.post('/v1/images/generate/jobs',json=p,headers={'Idempotency-Key':'raw-key'}).status_code==401
            client.headers['Authorization']='Bearer test-token'
            old=jobs.submit_image(ImageRequest(prompt='asset image'),'asset-key',configs.resolve_image_model(),media)
            preview=client.post('/v1/images/preview',json=p).json()
            assert preview['prompt']==p['prompt'] and preview['prompt_version']=='raw-prompt-v1'
            a=client.post('/v1/images/generate/jobs',json=p,headers={'Idempotency-Key':'raw-key'})
            assert a.status_code==202
            b=client.post('/v1/images/generate/jobs',json=p,headers={'Idempotency-Key':'raw-key'})
            assert a.json()['id']==b.json()['id']
            for _ in range(2):ImageWorker(jobs,configs,media,'raw-test',adapter_factory=lambda _:adapter).run_once()
            result=client.get('/v1/jobs/'+a.json()['id']).json()
            assert result['status']=='succeeded'
            assert result['effective_prompt']==p['prompt']
            assert result['negative_prompt']==p['negative_prompt']
            assert result['skill_revision'] is None
            assert result['style_snapshot']['effective_style']==''
            assert client.get(result['result']['artifacts'][0]['download_url']).content==png()
            raw=client.get('/v1/jobs?type=image_generation&prompt_mode=raw').json()
            assert raw['total']==1 and raw['items'][0]['id']==a.json()['id']
            asset=client.get('/v1/jobs?type=image_generation&prompt_mode=asset').json()
            assert asset['total']==1 and asset['items'][0]['id']==old['id']
            assert client.get('/v1/jobs?type=image_generation').json()['total']==2
            assert client.get('/v1/jobs?prompt_mode=invalid').status_code==422
    finally:app.dependency_overrides.clear()
