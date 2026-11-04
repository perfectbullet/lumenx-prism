from datetime import timedelta
from uuid import uuid4
import json
import subprocess
import shutil
from unittest.mock import Mock
import mongomock
import pytest
from pydantic import ValidationError
from lumenx_prism.production_jobs import ProductionJobs
from lumenx_prism.production_schemas import AssemblyInput
from lumenx_prism.video_media import probe,put_video,assemble
from lumenx_prism.media import MediaStore
from lumenx_prism.model_configs import ConfigError
from lumenx_prism.jobs import utcnow

@pytest.fixture
def env():
    db=mongomock.MongoClient(tz_aware=True).db;repo=ProductionJobs(db);draft=str(uuid4())
    shots=[{'id':f'S{i+1:02}'} for i in range(11)]
    db.storyboard_drafts.insert_one({'_id':draft,'revision':1,'content':{'shots':shots}})
    ids=[]
    for s in reversed(shots):
        id=str(uuid4());ids.insert(0,id)
        db.ai_jobs.insert_one({'_id':id,'idempotency_key':id,'type':'video_generation','status':'succeeded','finished_at':utcnow(),
            'created_at':utcnow(),'request':{'draft_id':draft,'revision':1,'shot_id':s['id']},
            'result':{'artifacts':[{'id':str(uuid4()),'sha256':'hash','kind':'video'}]}})
    return db,repo,draft,ids

def test_more_than_six_manual_clips_and_order_validation(env):
    db,jobs,draft,ids=env
    p=AssemblyInput(draft_id=draft,revision=1,video_job_ids=ids)
    j=jobs.submit_new('video_assembly',p,'manual',None,None)
    assert len(j['reference_snapshot'])==11 and j['request']['video_job_ids']==ids
    with pytest.raises(ConfigError,match='candidate_mismatch'):
        jobs.submit_new('video_assembly',AssemblyInput(draft_id=draft,revision=1,video_job_ids=list(reversed(ids))),'wrong-order',None,None)

def test_auto_latest_success_freezes_candidates_across_retries(env):
    db,jobs,draft,ids=env
    newest=db.ai_jobs.find_one({'_id':ids[0]});newest['_id']=str(uuid4());newest['finished_at']=utcnow()+timedelta(seconds=1)
    newest['idempotency_key']=newest['_id'];db.ai_jobs.insert_one(newest)
    failed=dict(newest,_id=str(uuid4()),status='failed',finished_at=utcnow()+timedelta(seconds=2));failed['idempotency_key']=failed['_id'];db.ai_jobs.insert_one(failed)
    p=AssemblyInput(draft_id=draft,revision=1,selection_mode='latest_succeeded')
    j=jobs.submit_new('video_assembly',p,'auto',None,None)
    assert j['request']['video_job_ids']==[newest['_id'],*ids[1:]]
    newer=dict(newest,_id=str(uuid4()),finished_at=utcnow()+timedelta(seconds=3));newer['idempotency_key']=newer['_id'];db.ai_jobs.insert_one(newer)
    assert jobs.submit_new('video_assembly',p,'auto',None,None)['request']['video_job_ids']==j['request']['video_job_ids']
    assert jobs.submit_new('video_assembly',p,'auto-new',None,None)['request']['video_job_ids'][0]==newer['_id']

def test_missing_video_and_foreign_revision_not_silently_skipped(env):
    db,jobs,draft,ids=env
    db.ai_jobs.update_one({'_id':ids[5]},{'$set':{'request.revision':2}})
    with pytest.raises(ConfigError,match='shots_incomplete'):
        jobs.submit_new('video_assembly',AssemblyInput(draft_id=draft,revision=1,selection_mode='latest_succeeded'),'missing',None,None)
    assert db.ai_jobs.count_documents({'type':'video_assembly'})==0

@pytest.mark.parametrize('ids,mode',[([], 'selected'),([uuid4()],'latest_succeeded'),([uuid4() for _ in range(37)],'selected')])
def test_selection_contract(ids,mode):
    with pytest.raises(ValidationError):AssemblyInput(draft_id=uuid4(),revision=1,video_job_ids=ids,selection_mode=mode)

def test_output_limit_allows_long_assembly_but_not_long_provider_clip(monkeypatch,tmp_path):
    output={'streams':[{'codec_type':'video','width':64,'height':64},{'codec_type':'audio','channels':2}],'format':{'duration':'210'}}
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:Mock(stdout=json.dumps(output)))
    assert probe(tmp_path/'fake',max_duration=720)['duration_seconds']==210
    with pytest.raises(ConfigError,match='video_file_invalid'):probe(tmp_path/'fake')

def test_eleven_clips_really_assemble_with_audio(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):pytest.skip('ffmpeg required')
    path=tmp_path/'tiny.mp4'
    subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-f','lavfi','-i','color=c=blue:s=64x64:r=24','-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','0.25','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(path)],check=True)
    media=MediaStore(mongomock.MongoClient().db,tmp_path/'media')
    clip=put_video(media,path.read_bytes(),'short')
    result=assemble(media,[clip]*11,'eleven')
    assert result['has_audio'] and result['duration_seconds']>=2.75


def test_merge_page_is_separate_from_video_generation():
 from fastapi.testclient import TestClient
 from lumenx_prism.api import app
 with TestClient(app) as client:
  merge=client.get('/tools/merges')
  assert merge.status_code==200 and '视频合并' in merge.text
  assert 'mergeSelected' in merge.text and 'mergeAll' in merge.text
  video=client.get('/tools/videos')
  assert 'currentVideoModel' in video.text and 'videoAdvanced' in video.text
  assert 'goMerge' in video.text and 'id="assembleAll"' not in video.text


def test_cpu_worker_can_merge_while_gpu_worker_only_generates(env,monkeypatch):
 from lumenx_prism.worker import Worker
 db,repo,draft,ids=env
 config=Mock();media=Mock()
 job=repo.submit_new('video_assembly',AssemblyInput(draft_id=draft,revision=1,video_job_ids=ids),'cpu-merge',config,media)
 cpu=Worker(repo,config,media=media,capabilities='text')
 gpu=Worker(repo,config,media=media,capabilities='gpu')
 assert 'video_assembly' in cpu.production_worker.kinds
 assert 'video_assembly' not in gpu.production_worker.kinds
 monkeypatch.setattr('lumenx_prism.production_worker.assemble',lambda *a,**k:{'id':'merged-test-video'})
 assert cpu.production_worker.run_once()
 assert repo.get(job['id'])['status']=='succeeded'
 config.resolve_production_model.assert_not_called()
