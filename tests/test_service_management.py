"""Exercise management against an isolated fake worker, never the live AI worker."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import time

import pytest


def manager(tmp_path, monkeypatch, ignore_term=False):
    path = Path(__file__).parents[1] / 'scripts/prismctl.py'
    spec = importlib.util.spec_from_file_location('prism_manager', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'RUN', tmp_path / 'run')
    monkeypatch.setattr(module, 'LOGS', tmp_path / 'logs')
    module.RUN.mkdir(); module.LOGS.mkdir()
    (tmp_path / '.env').write_text('PRISM_PORT=17000\nPRISM_SERVICE_AUTH_ENABLED=false\n')
    fake = tmp_path / 'src/lumenx_prism'; fake.mkdir(parents=True)
    (fake / '__init__.py').write_text('')
    (fake / 'worker.py').write_text(
        'import signal,time\n' +
        ('signal.signal(signal.SIGTERM, signal.SIG_IGN)\n' if ignore_term else '') +
        'while True: time.sleep(.05)\n')
    return module


def test_worker_start_idempotence_and_graceful_stop(tmp_path, monkeypatch):
    m = manager(tmp_path, monkeypatch)
    try:
        m.start('worker', m.settings())
        pid = m.running('worker'); assert pid
        m.start('worker', m.settings())
        assert m.running('worker') == pid
        assert (m.RUN / 'worker.json').stat().st_mode & 0o777 == 0o600
        assert (m.LOGS / 'worker.log').is_file()
        m.stop('worker', 2)
        assert m.running('worker') is None
        m.stop('worker', 2)
    finally:
        if m.running('worker'): os.kill(m.running('worker'), signal.SIGKILL)


def test_stale_pid_receipt_never_signals_reused_process(tmp_path, monkeypatch):
    m = manager(tmp_path, monkeypatch)
    m.state_file('worker').write_text(json.dumps({'pid': os.getpid(), 'start_ticks': 'wrong'}))
    monkeypatch.setattr(m.os, 'kill', lambda *_: pytest.fail('Must not signal a stale PID'))
    m.stop('worker', 1)
    assert not m.state_file('worker').exists()


def test_stop_timeout_does_not_kill_or_start_second_worker(tmp_path, monkeypatch):
    m = manager(tmp_path, monkeypatch, ignore_term=True)
    try:
        m.start('worker', m.settings()); pid = m.running('worker')
        with pytest.raises(RuntimeError, match='still shutting down'):
            m.stop('worker', .1)
        assert m.running('worker') == pid
        m.start('worker', m.settings())
        assert m.running('worker') == pid
    finally:
        if m.running('worker'): os.kill(m.running('worker'), signal.SIGKILL)


def test_host_config_overrides_paths_without_modifying_synchronized_env(tmp_path, monkeypatch):
    m = manager(tmp_path, monkeypatch)
    original = (tmp_path / '.env').read_bytes()
    (tmp_path / 'deploy').mkdir()
    (tmp_path / 'deploy/231.env').write_text('PRISM_PORT=17178\nPRISM_MEDIA_ROOT=/data/aigc/lumenx-prism/data/media\n')
    env = m.settings()
    assert env['PRISM_PORT'] == '17178'
    assert env['PRISM_SERVICE_AUTH_ENABLED'] == 'false'
    assert env['PRISM_MEDIA_ROOT'].startswith('/data/aigc/lumenx-prism/')
    assert (tmp_path / '.env').read_bytes() == original
