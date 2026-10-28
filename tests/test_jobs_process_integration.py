"""Opt-in: real MongoDB + separate API/worker processes + local fake provider.

PRISM_MONGO_INTEGRATION=1 python -m pytest tests/test_jobs_process_integration.py
Uses .env connection, temporary prism_test_* collections, and no paid model.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

from dotenv import dotenv_values
import httpx
import pytest
from pymongo import MongoClient

from lumenx_prism.model_configs import ModelConfigs, ModelConfigInput

pytestmark = pytest.mark.skipif(os.getenv('PRISM_MONGO_INTEGRATION') != '1',
                               reason='explicit MongoDB integration opt-in required')

CHILD = '''
import os, time, sys
from pymongo import MongoClient
from lumenx_prism.jobs import Jobs
from lumenx_prism.model_configs import ModelConfigs
from lumenx_prism.worker import Worker
client=MongoClient(os.environ['TEST_MONGO_URI'],tz_aware=True,serverSelectionTimeoutMS=3000,timeoutMS=5000)
db=client['lumenx_prism'];prefix=os.environ['TEST_PREFIX']
jobs=Jobs(db);jobs.collection=db[prefix+'_jobs'];jobs.attempts=db[prefix+'_attempts']
configs=ModelConfigs(db);configs.collection=db[prefix+'_models']
if sys.argv[1]=='api':
    import uvicorn
    from lumenx_prism.api import app,get_jobs,get_model_configs
    app.dependency_overrides[get_jobs]=lambda:jobs
    app.dependency_overrides[get_model_configs]=lambda:configs
    uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[2]),log_level='error')
else:
    jobs.ensure_indexes()
    worker=Worker(jobs,configs,lease_seconds=2)
    while True:
        worker.run_once();time.sleep(.1)
'''


def eventually(callback, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = callback()
            if value:
                return value
        except (httpx.HTTPError, KeyError):
            pass
        time.sleep(.1)
    raise AssertionError('Timed out waiting for process state')


def test_api_restart_worker_kill_and_no_duplicate_inference(tmp_path):
    values = dotenv_values(Path(__file__).resolve().parents[1] / '.env')
    uri = values.get('PRISM_MONGODB_URI')
    assert uri, 'Prism MongoDB connection required'
    mongo = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000, timeoutMS=5000)
    db = mongo['lumenx_prism']
    prefix = 'prism_test_' + uuid4().hex
    calls, blocked = [], threading.Event()

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(payload)
            if 'INTERRUPT_TEST' in payload['messages'][0]['content']:
                blocked.set()
                time.sleep(5)
            body = json.dumps({'id':'test', 'object':'chat.completion', 'created':0,
                'model':'mock', 'choices':[{'index':0,'finish_reason':'stop',
                    'message':{'role':'assistant','content':'{"characters":[],"scenes":[],"props":[]}'}}]}).encode()
            try:
                self.send_response(200); self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    provider = ThreadingHTTPServer(('127.0.0.1',0), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    processes = []
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port = sock.getsockname()[1]
    env = os.environ | {'TEST_MONGO_URI':uri,'TEST_PREFIX':prefix,
                        'AI_SERVICE_API_KEY':'integration-token',
                        'PYTHONPATH':str(Path(__file__).resolve().parents[1] / 'src')}
    script = tmp_path / 'child.py'
    script.write_text(CHILD)

    def start(mode):
        proc = subprocess.Popen([sys.executable,str(script),mode,str(port)], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        processes.append(proc)
        return proc

    def stop(proc):
        proc.terminate()
        proc.wait(timeout=10)

    try:
        configs = ModelConfigs(db)
        configs.collection = db[prefix+'_models']
        config = configs.save(ModelConfigInput(name='integration', kind='text', provider='local-mock',
            protocol='openai_chat',model='mock',api_key='fake-only',
            base_url=f'http://127.0.0.1:{provider.server_port}/v1'))
        configs.bind_entity_model(config['id'])
        api = start('api')
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=5,
                          headers={'Authorization':'Bearer integration-token'}) as client:
            eventually(lambda: client.get('/health').status_code==200)
            def submit(text, key):
                response=client.post('/v1/scripts/extract-entities/jobs',json={'text':text},
                                     headers={'Idempotency-Key':key})
                assert response.status_code==202
                return response.json()['id']
            first=submit('重启测试','first')
            assert client.get('/v1/jobs/'+first).json()['status']=='queued'
            stop(api)
            api=start('api')
            eventually(lambda: client.get('/health').status_code==200)
            assert submit('重启测试','first')==first
            worker=start('worker')
            eventually(lambda: client.get('/v1/jobs/'+first).json()['status']=='succeeded')
            assert len(calls)==1
            second=submit('INTERRUPT_TEST','second')
            assert blocked.wait(10)
            worker.kill();worker.wait(timeout=10)
            # The API is independent and remains queryable during the worker outage.
            assert client.get('/v1/jobs/'+second).json()['status']=='running'
            worker=start('worker')
            eventually(lambda: client.get('/v1/jobs/'+second).json()['status']=='failed')
            row=client.get('/v1/jobs/'+second).json()
            assert row['error_code']=='execution_outcome_unknown'
            assert len(calls)==2
            third=submit('新任务继续执行','third')
            eventually(lambda: client.get('/v1/jobs/'+third).json()['status']=='succeeded')
            assert len(calls)==3
            eventually(lambda: db[prefix+'_attempts'].count_documents({'status':{'$in':['succeeded','failed']}})==3)
    finally:
        for proc in processes:
            if proc.poll() is None:
                stop(proc)
        provider.shutdown();provider.server_close()
        for suffix in ('_jobs','_attempts','_models'):
            db.drop_collection(prefix+suffix)
        mongo.close()
