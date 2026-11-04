import subprocess
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock
import pytest
from lumenx_prism import gpu_processes as gp


def proc(root,pid,argv):
 p=root/str(pid);p.mkdir();(p/'cmdline').write_bytes(('\0'.join(argv)+'\0').encode())


def test_nvidia_pid_memory_is_attributed_by_exact_port(tmp_path,monkeypatch):
 proc(tmp_path,101,['python','./ComfyUI/main.py','--port','8188'])
 proc(tmp_path,202,['python','main.py','--port=8190'])
 run=Mock(return_value=SimpleNamespace(stdout='101, 808, GPU-1\n202, 4096, GPU-1\n'))
 sample=gp.collect(tmp_path,run);monkeypatch.setattr(gp,'local_host',lambda _:True)
 h3=gp.memory_for('http://192.168.8.231:8188',sample);qwen=gp.memory_for('http://192.168.8.231:8190',sample)
 assert h3['pid']==101 and h3['vram_used']==808*1024**2
 assert qwen['pid']==202 and qwen['vram_used']==4096*1024**2
 assert run.call_args.kwargs['timeout']==3
 assert run.call_args.args[0][0]=='nvidia-smi'


def test_remote_host_cannot_reuse_local_port_memory(tmp_path,monkeypatch):
 monkeypatch.setattr(gp,'local_host',lambda _:False)
 assert gp.memory_for('http://other-server:8188',{'available':True,'processes':[{'pid':1,'port':8188,'gpus':{'gpu':123}}]})=={'available':False,'reason':'instance_not_local'}


def test_ambiguous_port_is_unknown_not_summed(tmp_path,monkeypatch):
 for pid in [101,202]:proc(tmp_path,pid,['python','main.py','--port','8188'])
 sample=gp.collect(tmp_path,lambda *a,**kw:SimpleNamespace(stdout='101, 808, GPU-1\n202, 909, GPU-1\n'))
 monkeypatch.setattr(gp,'local_host',lambda _:True)
 assert gp.memory_for('http://127.0.0.1:8188',sample)=={'available':False,'reason':'pid_ambiguous'}


def test_multiple_gpus_sum_once_each(tmp_path,monkeypatch):
 proc(tmp_path,101,['python','main.py','--port','8188'])
 sample=gp.collect(tmp_path,lambda *a,**kw:SimpleNamespace(stdout='101, 100, GPU-1\n101, 200, GPU-2\n101, 100, GPU-1\n'))
 monkeypatch.setattr(gp,'local_host',lambda _:True)
 result=gp.memory_for('http://127.0.0.1:8188',sample)
 assert result['vram_used']==300*1024**2 and result['gpu_count']==2


def test_unavailable_or_invalid_memory_is_not_reported_as_zero(tmp_path,monkeypatch):
 def timeout(*a,**kw):raise subprocess.TimeoutExpired('nvidia-smi',3)
 monkeypatch.setattr(gp,'local_host',lambda _:True)
 sample=gp.collect(tmp_path,timeout)
 assert gp.memory_for('http://127.0.0.1:8188',sample)['reason']=='nvidia_smi_unavailable'
 proc(tmp_path,101,['python','main.py','--port','8188'])
 sample=gp.collect(tmp_path,lambda *a,**kw:SimpleNamespace(stdout='101, [N/A], GPU-1\n101, nan, GPU-1\n101, -1, GPU-1\n'))
 assert gp.memory_for('http://127.0.0.1:8188',sample)['reason']=='gpu_pid_not_found'


def test_unreadable_process_or_wrong_command_is_not_attributed(tmp_path):
 proc(tmp_path,101,['python','other.py','--port','8188'])
 proc(tmp_path,202,['python','main.py','--port','8188','--port','8190'])
 sample=gp.collect(tmp_path,lambda *a,**kw:SimpleNamespace(stdout='101, 808, GPU-1\n202, 808, GPU-1\n303, 808, GPU-1\n'))
 assert sample['processes']==[]


def test_loopback_is_local_but_invalid_addresses_are_not():
 assert gp.local_host('127.0.0.1')
 assert not gp.local_host('not-an-address')
