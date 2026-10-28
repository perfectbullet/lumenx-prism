"""Real HTTP smoke test of a copied standalone app against a local fake provider."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests


def test_copied_service_runs_without_business_source(tmp_path, monkeypatch):
    calls = []
    entities = {'characters': [{'id': 'c1', 'name': '叶墨'}],
                'scenes': [{'id': 's1', 'name': '书房'}], 'props': []}

    class Provider(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            body = json.dumps({'id': 'test-completion', 'object': 'chat.completion',
                               'created': 0, 'model': 'test-model',
                               'choices': [{'index': 0, 'finish_reason': 'stop',
                                            'message': {'role': 'assistant', 'content': json.dumps(entities)}}]}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / 'src/lumenx_prism', tmp_path / 'lumenx_prism',
                    ignore=shutil.ignore_patterns('__pycache__'))
    # Seed a fake DB in the child; runtime still resolves its config through the DB repository.
    model_config = dict(name='本机模型', kind='text', provider='test-provider', protocol='openai_chat',
                        model='test-model', base_url=f'http://127.0.0.1:{provider.server_port}/v1',
                        api_key='fake-local-key')
    (tmp_path / 'seed.json').write_text(json.dumps(model_config))
    (tmp_path / 'launcher.py').write_text("""
import json
from pathlib import Path
import mongomock
from lumenx_prism.api import app, get_model_configs
from lumenx_prism.model_configs import ModelConfigs, ModelConfigInput
repo = ModelConfigs(mongomock.MongoClient()['lumenx_prism'])
created = repo.save(ModelConfigInput.model_validate(json.loads(Path('seed.json').read_text())))
repo.bind_entity_model(created['id'])
app.dependency_overrides[get_model_configs] = lambda: repo
""")
    env = dict(os.environ, OPENAI_MODEL='wrong-env-model', AI_SERVICE_API_KEY='internal-token',
               NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost')
    env.pop('PYTHONPATH', None)
    process = subprocess.Popen(
        [sys.executable, '-m', 'uvicorn', 'launcher:app', '--host', '127.0.0.1',
         '--port', str(port)], cwd=tmp_path, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    session = requests.Session()
    session.trust_env = False
    url = f'http://127.0.0.1:{port}'
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            assert process.poll() is None, 'Standalone AI process exited during startup'
            try:
                if session.get(url + '/health', timeout=0.2).status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(0.1)
        else:
            raise AssertionError('Standalone AI service failed to become ready')
        assert session.post(url + '/v1/scripts/extract-entities', json={'text': '原文'},
                            timeout=2).status_code == 401
        assert not calls
        response = session.post(url + '/v1/scripts/extract-entities',
                                json={'text': '叶墨走进书房。'},
                                headers={'Authorization': 'Bearer internal-token'}, timeout=10)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['characters'][0]['name'] == '叶墨'
        assert result['characters'][0]['id'] == 'c1'
        assert result['scenes'][0]['name'] == '书房'
        assert len(calls) == 1
        assert calls[0]['model'] == 'test-model'
        assert '叶墨走进书房。' in calls[0]['messages'][0]['content']
    finally:
        session.close()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=2)
