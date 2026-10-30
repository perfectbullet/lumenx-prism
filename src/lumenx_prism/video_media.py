"""Local video artifacts and preview assembly; never inference on this host."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from uuid import uuid4,uuid5,NAMESPACE_URL
from .jobs import utcnow
from .model_configs import ConfigError

MAX_VIDEO_BYTES=256*1024*1024

def probe(path):
    try:
        out=subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)],
            capture_output=True,timeout=30,check=True)
        data=json.loads(out.stdout);video=next(s for s in data['streams'] if s['codec_type']=='video')
        audio=[s for s in data['streams'] if s['codec_type']=='audio']
        duration=float(data['format']['duration'])
        if not 0<duration<=180 or video['width']*video['height']>4096*4096: raise ValueError()
        return {'width':video['width'],'height':video['height'],'duration_seconds':duration,
                'fps':video.get('avg_frame_rate'),'has_audio':bool(audio),'audio_channels':audio[0].get('channels') if audio else None}
    except (ValueError,KeyError,StopIteration,subprocess.SubprocessError):
        raise ConfigError('video_file_invalid',422) from None

def put_video(media,content,job_id,key='video'):
    if not content or len(content)>MAX_VIDEO_BYTES: raise ConfigError('video_size_invalid',413)
    media.root.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(media.root).free<len(content)*2+100*1024*1024: raise ConfigError('media_disk_full',503)
    digest=hashlib.sha256(content).hexdigest();id=str(uuid5(NAMESPACE_URL,f'prism:video:{job_id}:{key}:{digest}'))
    relative=Path('outputs')/id/'video.mp4';path=media.root/relative;path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(str(uuid4())+'.mp4')
    try:
        temp.write_bytes(content);metadata=probe(temp)
        subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(temp),'-f','null','-'],check=True,capture_output=True,timeout=180)
        os.replace(temp,path)
    except subprocess.SubprocessError: raise ConfigError('video_decode_failed',502) from None
    finally: temp.unlink(missing_ok=True)
    row={'_id':id,'id':id,'kind':'video','mime_type':'video/mp4','size_bytes':len(content),'sha256':digest,
         'relative_path':str(relative),'job_id':job_id,'created_at':utcnow(),**metadata}
    media.collection.update_one({'_id':id},{'$setOnInsert':row},upsert=True)
    return {k:v for k,v in row.items() if k not in ('_id','relative_path')}|{'download_url':f'/v1/media/{id}/download'}

def assemble(media,refs,job_id):
    media.root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='assemble-',dir=media.root) as directory:
        directory=Path(directory);clips=[]
        width=height=None
        for i,ref in enumerate(refs):
            row,path=media.get(ref['id'])
            if row['kind']!='video' or hashlib.sha256(path.read_bytes()).hexdigest()!=ref['sha256']:
                raise ConfigError('video_reference_changed',409)
            meta=probe(path)
            if not meta['has_audio']: raise ConfigError('video_audio_missing',422)
            if width is None: width,height=meta['width'],meta['height']
            output=directory/f'{i}.mp4'
            vf=f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24'
            subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-i',str(path),'-map','0:v:0','-map','0:a:0',
                '-vf',vf,'-c:v','libx264','-preset','fast','-crf','20','-pix_fmt','yuv420p','-c:a','aac',
                '-ar','48000','-ac','2',str(output)],check=True,capture_output=True,timeout=300)
            clips.append(output)
        listing=directory/'clips.txt';listing.write_text(''.join(f"file '{p.name}'\n" for p in clips))
        final=directory/'preview.mp4'
        subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-f','concat','-safe','1','-i',str(listing),
            '-c','copy','-movflags','+faststart',str(final)],check=True,capture_output=True,timeout=120)
        return put_video(media,final.read_bytes(),job_id,'assembly')
