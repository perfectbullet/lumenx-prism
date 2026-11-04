"""Best-effort local NVIDIA process memory; no remote execution or mutable controls."""
import csv
import ipaddress
import math
from pathlib import Path
import socket
import subprocess
from urllib.parse import urlsplit


def local_host(host):
    if host=='localhost':host='127.0.0.1'
    try:
        address=ipaddress.ip_address(host)
        family=socket.AF_INET6 if address.version==6 else socket.AF_INET
        with socket.socket(family,socket.SOCK_STREAM) as probe:probe.bind((host,0))
        return True
    except (OSError,ValueError,TypeError):return False


def collect(proc_root=Path('/proc'),runner=None):
    """Only explicit-port ComfyUI-shaped GPU commands can be attributed to an instance."""
    runner=runner or subprocess.run
    try:
        result=runner(['nvidia-smi','--query-compute-apps=pid,used_gpu_memory,gpu_uuid',
                       '--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=3,check=True)
        processes={}
        for fields in csv.reader(result.stdout.splitlines()):
            if len(fields)!=3:continue
            pid_text,memory_text,gpu=(v.strip() for v in fields)
            try:pid=int(pid_text);memory=float(memory_text)
            except ValueError:continue
            if pid<=0 or not math.isfinite(memory) or memory<0:continue
            if pid not in processes:
                try:argv=(proc_root/str(pid)/'cmdline').read_bytes().decode().strip('\0').split('\0')
                except (OSError,UnicodeError):continue
                # Require an actual main.py command and explicit --port, not arbitrary text matching.
                if not any(Path(a).name=='main.py' for a in argv[1:]):continue
                ports=[]
                for i,arg in enumerate(argv):
                    if arg=='--port' and i+1<len(argv):ports.append(argv[i+1])
                    elif arg.startswith('--port='):ports.append(arg.split('=',1)[1])
                if len(ports)!=1 or not ports[0].isdigit() or not 1<=int(ports[0])<=65535:continue
                processes[pid]={'pid':pid,'port':int(ports[0]),'gpus':{}}
            processes[pid]['gpus'][gpu]=int(memory*1024**2)
        return {'available':True,'processes':list(processes.values())}
    except (OSError,UnicodeError,subprocess.SubprocessError):return {'available':False,'processes':[]}


def memory_for(url,sample):
    try:
        parts=urlsplit(url);port=parts.port or (443 if parts.scheme=='https' else 80)
    except ValueError:return {'available':False,'reason':'instance_url_invalid'}
    if not local_host(parts.hostname):return {'available':False,'reason':'instance_not_local'}
    if not sample['available']:return {'available':False,'reason':'nvidia_smi_unavailable'}
    matches=[p for p in sample['processes'] if p['port']==port]
    if len(matches)!=1:return {'available':False,'reason':'pid_ambiguous' if matches else 'gpu_pid_not_found'}
    p=matches[0]
    return {'available':True,'source':'nvidia-smi','pid':p['pid'],'vram_used':sum(p['gpus'].values()),
            'gpu_count':len(p['gpus']),'match_method':'local_explicit_port'}
