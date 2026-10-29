#!/usr/bin/env python3
"""Manage the standalone Prism API and worker on 231. No shell command execution."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / 'run'
LOGS = ROOT / 'logs'


def settings():
    env = dict(os.environ)
    for file in (ROOT / '.env', ROOT / 'deploy/231.env'):
        env.update({k: v for k, v in dotenv_values(file).items() if v is not None})
    env['PYTHONPATH'] = str(ROOT / 'src')
    env['PYTHONUNBUFFERED'] = '1'
    return env


def process_ticks(pid):
    try:
        stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return None if stat[0] == 'Z' else stat[19]
    except (FileNotFoundError, ProcessLookupError):
        return None


def state_file(component):
    return RUN / (component + '.json')


def running(component):
    try:
        row = json.loads(state_file(component).read_text())
        pid = int(row['pid'])
        if row['start_ticks'] != process_ticks(pid):
            return None
        command = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        marker = b'uvicorn' if component == 'api' else b'lumenx_prism.worker'
        if marker not in command or Path(f'/proc/{pid}/cwd').resolve() != ROOT:
            return None
        return pid
    except (FileNotFoundError, KeyError, ValueError, ProcessLookupError):
        return None


def api_ready(env):
    try:
        host = env.get('PRISM_BIND_HOST', '0.0.0.0')
        host = '127.0.0.1' if host in ('0.0.0.0', '::') else host
        url = f"http://{host}:{env.get('PRISM_PORT', '17178')}/health"
        with build_opener(ProxyHandler({})).open(url, timeout=2) as response:
            return json.load(response).get('service') == 'lumenx-prism'
    except (URLError, OSError, ValueError):
        return False


def start(component, env):
    if running(component):
        print(f'{component}: already running (PID {running(component)})')
        return
    if not (ROOT / '.env').is_file():
        raise RuntimeError('Missing Prism .env; sync it first')
    if component == 'api' and api_ready(env):
        raise RuntimeError('API port already serves Prism without a matching PID receipt; inspect it before starting')
    if component == 'api':
        command = [sys.executable, '-m', 'uvicorn', 'lumenx_prism.api:app', '--app-dir', str(ROOT / 'src'),
                   '--env-file', str(ROOT / '.env'), '--host', env.get('PRISM_BIND_HOST', '0.0.0.0'),
                   '--port', env.get('PRISM_PORT', '17178')]
    else:
        command = [sys.executable, '-m', 'lumenx_prism.worker', '--env-file', str(ROOT / '.env')]
    log_path = LOGS / (component + '.log')
    with log_path.open('ab') as log:
        log.write(f'\n[{datetime.now(timezone.utc).isoformat()}] Starting {component}\n'.encode())
        log.flush()
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=log, start_new_session=True)
    ticks = process_ticks(process.pid)
    if ticks is None:
        raise RuntimeError(f'{component} exited on startup; inspect {log_path}')
    temporary = state_file(component).with_suffix('.tmp')
    temporary.write_text(json.dumps({'pid': process.pid, 'start_ticks': ticks}))
    temporary.chmod(0o600)
    temporary.replace(state_file(component))
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError(f'{component} exited on startup; inspect {log_path}')
        if component == 'api' and api_ready(env):
            print(f'{component}: started (PID {process.pid})')
            return
        if component == 'worker' and _ >= 10:
            print(f'{component}: started (PID {process.pid})')
            return
        time.sleep(.1)
    raise RuntimeError(f'{component} startup is not ready; inspect status and {log_path}')


def stop(component, timeout):
    pid = running(component)
    if pid is None:
        state_file(component).unlink(missing_ok=True)
        print(f'{component}: stopped')
        return
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if running(component) is None:
            state_file(component).unlink(missing_ok=True)
            print(f'{component}: stopped')
            return
        time.sleep(.2)
    raise RuntimeError(f'{component} is still shutting down (PID {pid}); inspect logs and retry later. No SIGKILL was sent.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'restart', 'stop', 'status', 'logs'])
    parser.add_argument('component', choices=['all', 'api', 'worker'], default='all', nargs='?')
    parser.add_argument('--lines', type=int, default=100)
    parser.add_argument('--follow', '-f', action='store_true')
    parser.add_argument('--timeout', type=float, default=1200, help='Graceful worker shutdown timeout, seconds')
    args = parser.parse_args()
    if args.lines < 1 or args.timeout <= 0:
        parser.error('lines and timeout must be positive')
    RUN.mkdir(mode=0o700, exist_ok=True); LOGS.mkdir(mode=0o700, exist_ok=True)
    components = ['api', 'worker'] if args.component == 'all' else [args.component]
    if args.action == 'logs':
        paths = [LOGS / (component + '.log') for component in components]
        for path in paths:
            path.touch(exist_ok=True)
        os.execvp('tail', ['tail', '-n', str(args.lines)] + (['-F'] if args.follow else []) + [str(p) for p in paths])
    env = settings()
    if args.action == 'status':
        failed = False
        for component in components:
            pid = running(component)
            message = f'running (PID {pid})' if pid else 'stopped'
            if component == 'api' and pid:
                ready = api_ready(env)
                message += ', health=' + ('ok' if ready else 'not ready')
                failed |= not ready
            print(f'{component}: {message}')
            failed |= pid is None
        return int(failed)
    with (RUN / 'manage.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another start/stop/restart is in progress') from None
        if args.action in ('stop', 'restart'):
            # Stop accepting new HTTP requests before draining the worker.
            for component in components:
                stop(component, args.timeout)
        if args.action in ('start', 'restart'):
            for component in components:
                start(component, env)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError) as exc:
        print(f'Prism: {exc}', file=sys.stderr)
        raise SystemExit(1)
