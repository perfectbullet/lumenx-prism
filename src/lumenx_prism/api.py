"""Run: uvicorn lumenx_prism.api:app --app-dir src --host 127.0.0.1 --port 17178."""
import os
import secrets
import time
import logging
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Response, Query, Request, Header
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .history import MongoHistory
from .jobs import Jobs
from .execution_timing import refresh_many as refresh_execution_times
from .model_configs import ModelConfigs, ModelConfigInput, ConfigError
from .extraction import EntityExtractionError, EntityExtractor
from .llm_adapter import LLMAdapter
from .schemas import EntityExtractionRequest, EntityExtractionResult

app = FastAPI(title='LumenX Prism', version='0.1.0')
@app.exception_handler(RequestValidationError)
async def safe_config_validation(request: Request, exc: RequestValidationError):
    if request.url.path.startswith(('/v1/model-configs','/v1/comfyui-api-configs')):
        return JSONResponse(status_code=422, content={'detail': [
            {k: error[k] for k in ('type', 'loc', 'msg')} for error in exc.errors()
        ]}, headers={'Cache-Control': 'no-store'})
    return await request_validation_exception_handler(request, exc)


bearer = HTTPBearer(auto_error=False)
logger = logging.getLogger(__name__)
static = Path(__file__).parent / 'static'
app.mount('/static', StaticFiles(directory=static), name='static')


def service_auth_enabled():
    return os.getenv('PRISM_SERVICE_AUTH_ENABLED', 'true').strip().lower() not in ('false', '0', 'off', 'no')


def console_page(filename):
    if service_auth_enabled():
        return FileResponse(static / filename, headers={'Cache-Control': 'no-store'})
    html = (static / filename).read_text(encoding='utf-8')
    # Keep the inputs in the DOM for existing console scripts, but hide the token controls.
    html = html.replace('</head>', '<style>label:has(#token),div:has(>label[for="token"]){display:none!important}</style>'
                        '<script>try{sessionStorage.removeItem("prism.images.serviceToken")}catch{}</script></head>')
    html = html.replace('令牌仅保留在当前页面内存中。', '当前开发环境已关闭接口鉴权。')
    html = html.replace('服务 token 沿用 Prism 鉴权', '当前开发环境已关闭接口鉴权')
    return HTMLResponse(html, headers={'Cache-Control': 'no-store'})


@app.get('/', include_in_schema=False)
@app.get('/tools/entities', include_in_schema=False)
def test_page():
    return console_page('entities.html')


def get_history():
    uri = os.getenv('PRISM_MONGODB_URI', '').strip()
    if not uri:
        yield None
        return
    try:
        repo = MongoHistory(uri, os.getenv('PRISM_MONGODB_DATABASE', 'lumenx_prism'))
    except Exception:
        raise HTTPException(503, detail={'code': 'history_configuration_error'}) from None
    try:
        yield repo
    finally:
        repo.close()


def history_operation(operation):
    try:
        return operation()
    except Exception:
        raise HTTPException(503, detail={'code': 'history_unavailable'}) from None



def require_service_auth(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if not service_auth_enabled():
        return
    expected = os.getenv('AI_SERVICE_API_KEY', '')
    if expected and (credentials is None or not secrets.compare_digest(
            credentials.credentials.encode('utf-8'), expected.encode('utf-8'))):
        raise HTTPException(status_code=401, detail={'code': 'unauthorized'})


def config_operation(operation):
    try:
        return operation()
    except ConfigError as exc:
        raise HTTPException(exc.status, detail={'code': exc.code, **({'context':exc.details} if exc.details is not None else {})}) from None
    except Exception:
        raise HTTPException(503, detail={'code': 'model_config_unavailable'}) from None


def get_model_configs(history=Depends(get_history)):
    if history is None:
        raise HTTPException(503, detail={'code': 'model_database_not_configured'})
    return ModelConfigs(history.client['lumenx_prism'])


def get_extractor(configs=Depends(get_model_configs)):
    config = config_operation(configs.resolve_entity_model)
    adapter = LLMAdapter(config, timeout=120, max_retries=0)
    try:
        yield EntityExtractor(adapter)
    finally:
        if adapter._client is not None:
            adapter._client.close()


@app.get('/tools/models', include_in_schema=False)
def model_page():
    return console_page('models.html')


@app.get('/v1/model-configs', dependencies=[Depends(require_service_auth)])
def list_model_configs(response: Response, limit: int = Query(50, ge=1, le=100),
                       offset: int = Query(0, ge=0, le=10000), configs=Depends(get_model_configs)):
    response.headers['Cache-Control'] = 'no-store'
    return config_operation(lambda: configs.list(limit, offset))


@app.post('/v1/model-configs', status_code=201, dependencies=[Depends(require_service_auth)])
def create_model_config(payload: ModelConfigInput, response: Response, configs=Depends(get_model_configs)):
    response.headers['Cache-Control'] = 'no-store'
    return config_operation(lambda: configs.save(payload))


@app.get('/v1/model-configs/{config_id}', dependencies=[Depends(require_service_auth)])
def get_model_config(config_id: UUID, response: Response, configs=Depends(get_model_configs)):
    response.headers['Cache-Control'] = 'no-store'
    return config_operation(lambda: configs.public(configs.get(str(config_id))))


@app.put('/v1/model-configs/{config_id}', dependencies=[Depends(require_service_auth)])
def update_model_config(config_id: UUID, payload: ModelConfigInput, response: Response,
                        configs=Depends(get_model_configs)):
    response.headers['Cache-Control'] = 'no-store'
    return config_operation(lambda: configs.save(payload, str(config_id)))


@app.put('/v1/model-configs/{config_id}/entity-extraction', dependencies=[Depends(require_service_auth)])
def bind_model_config(config_id: UUID, response: Response, configs=Depends(get_model_configs)):
    response.headers['Cache-Control'] = 'no-store'
    return config_operation(lambda: configs.bind_entity_model(str(config_id)))


@app.get('/health')
def health():
    # Liveness only: does not make paid provider calls or reveal configuration.
    return {'ok': True, 'service': 'lumenx-prism', 'version': '0.1.0'}


@app.get('/v1/test-console/config', dependencies=[Depends(require_service_auth)])
def console_config(response: Response, configs=Depends(get_model_configs)):
    response.headers['Cache-Control'] = 'no-store'
    config = config_operation(configs.resolve_entity_model)
    return {'provider': config['provider'], 'model': config['model'],
            'model_configured': True, 'history_configured': True,
            'model_config_id': config['_id'], 'model_revision': config['revision']}


@app.get('/v1/scripts/extract-entities/history', dependencies=[Depends(require_service_auth)])
def list_history(response: Response, limit: int = Query(20, ge=1, le=100),
                 offset: int = Query(0, ge=0, le=10000),
                 status: Literal['running', 'succeeded', 'failed'] | None = None,
                 history=Depends(get_history)):
    response.headers['Cache-Control'] = 'no-store'
    if history is None:
        raise HTTPException(503, detail={'code': 'history_not_configured'})
    return history_operation(lambda: history.list(limit, offset, status))


@app.get('/v1/scripts/extract-entities/history/{record_id}',
         dependencies=[Depends(require_service_auth)])
def history_detail(record_id: UUID, response: Response, history=Depends(get_history)):
    response.headers['Cache-Control'] = 'no-store'
    if history is None:
        raise HTTPException(503, detail={'code': 'history_not_configured'})
    result = history_operation(lambda: history.get(str(record_id)))
    if result is None:
        raise HTTPException(404, detail={'code': 'history_not_found'})
    return result


@app.post('/v1/scripts/extract-entities', response_model=EntityExtractionResult,
          dependencies=[Depends(require_service_auth)])
def extract_entities(request: EntityExtractionRequest, response: Response,
                     extractor: EntityExtractor = Depends(get_extractor),
                     history=Depends(get_history)):
    # Persist valid input before inference. Never silently lose an input then incur cost.
    response.headers['Cache-Control'] = 'no-store'
    started = time.monotonic()
    record_id = history_operation(lambda: history.begin(request, model_snapshot=getattr(extractor, 'model_snapshot', None))) if history is not None else None
    if record_id:
        response.headers['X-Prism-History-Id'] = record_id
    headers = {'X-Prism-History-Id': record_id} if record_id else {}

    def finish(result=None, error_code=None):
        if record_id:
            try:
                history.finish(record_id, result=result, error_code=error_code,
                               duration_ms=round((time.monotonic() - started) * 1000),
                               actual_model=getattr(extractor.llm, 'actual_model', None),
                               usage=getattr(extractor.llm, 'usage', None))
            except Exception:
                # Input is durable already. Keep inference result; never suggest a paid retry.
                headers['X-Prism-History-Warning'] = 'finalize_failed'
                response.headers['X-Prism-History-Warning'] = 'finalize_failed'
                logger.warning('History finalization failed for run %s', record_id)

    try:
        result = extractor.extract(request)
    except EntityExtractionError as exc:
        finish(error_code=exc.code)
        raise HTTPException(status_code=503 if exc.code == 'not_configured' else 502,
                            detail={'code': exc.code}, headers=headers) from None
    finish(result=result.model_dump())
    return result


# Additive async contract; the legacy synchronous endpoint remains unchanged.
def get_jobs(history=Depends(get_history)):
    if history is None:
        raise HTTPException(503, detail={'code': 'model_database_not_configured'})
    return Jobs(history.client['lumenx_prism'])


def job_operation(operation):
    try:
        return operation()
    except ConfigError as exc:
        raise HTTPException(exc.status, detail={'code': exc.code, **({'context':exc.details} if exc.details is not None else {})}) from None
    except Exception:
        raise HTTPException(503, detail={'code': 'job_store_unavailable'}) from None


@app.post('/v1/scripts/extract-entities/jobs', status_code=202,
          dependencies=[Depends(require_service_auth)])
def submit_entity_job(payload: EntityExtractionRequest, response: Response,
                      idempotency_key: str = Header(min_length=1, max_length=128),
                      jobs=Depends(get_jobs), configs=Depends(get_model_configs)):
    response.headers['Cache-Control'] = 'no-store'
    # A retried submission must succeed even if the binding changed since acceptance.
    row = job_operation(lambda: jobs.existing(idempotency_key, payload))
    if row is None:
        if payload.work_context:
            from .works import Works
            job_operation(lambda:Works(jobs.collection.database).validate_context(payload.work_context,payload.text))
        config = config_operation(configs.resolve_entity_model)
        row = job_operation(lambda: jobs.submit(payload, idempotency_key, config))
    response.headers['Location'] = '/v1/jobs/' + row['id']
    return row


@app.get('/v1/jobs', dependencies=[Depends(require_service_auth)])
def list_jobs(response: Response, background_tasks:BackgroundTasks, limit: int = Query(20, ge=1, le=100),
              offset: int = Query(0, ge=0, le=10000),
              status: Literal['queued', 'running', 'succeeded', 'failed'] | None = None,
              job_type: Literal['entity_extraction', 'image_generation','storyboard_generation','video_generation','video_assembly','image_polish','script_planning','work_breakdown','work_entities'] | None = Query(None, alias='type'),
              draft_id: UUID | None = None, revision: int | None = Query(None,ge=1),
              work_id: UUID | None = None, edition_id: UUID | None = None, episode_id: str | None = None, episode_ids: str | None = None,
              asset_type: Literal['character','scene','prop','image'] | None = None, name_query: str | None = Query(None,max_length=100),
              prompt_mode: Literal['asset','raw'] | None = None,
              scheduling_paused:bool | None=None,
              jobs=Depends(get_jobs)):
    response.headers['Cache-Control'] = 'no-store'
    rows=job_operation(lambda: jobs.list(limit, offset, status, job_type, draft_id, revision, work_id, edition_id, episode_id, episode_ids, asset_type, name_query, prompt_mode,scheduling_paused=scheduling_paused))
    background_tasks.add_task(refresh_execution_times,jobs.collection.database,[j['id'] for j in rows['items'] if j['type'] in ('image_generation','video_generation')])
    return rows


@app.get('/v1/gpu/jobs/history',dependencies=[Depends(require_service_auth)])
def gpu_job_history(response:Response,background_tasks:BackgroundTasks,limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0,le=10000),
                    status:Literal['succeeded','failed'] | None=None,
                    job_type:Literal['image_generation','video_generation'] | None=Query(None,alias='type'),
                    name_query:str | None=Query(None,max_length=100),jobs=Depends(get_jobs)):
    response.headers['Cache-Control']='no-store'
    rows=job_operation(lambda:jobs.list(limit,offset,status,job_type,name_query=name_query,gpu_history=True))
    background_tasks.add_task(refresh_execution_times,jobs.collection.database,[j['id'] for j in rows['items']])
    return rows

@app.get('/v1/jobs/{job_id}', dependencies=[Depends(require_service_auth)])
def job_detail(job_id: UUID, response: Response, request: Request, background_tasks:BackgroundTasks, jobs=Depends(get_jobs)):
    response.headers['Cache-Control'] = 'no-store'
    background_tasks.add_task(refresh_execution_times,jobs.collection.database,[str(job_id)])
    return media_urls(job_operation(lambda: jobs.get(str(job_id))), request)


# Image generation and Prism-owned input/output media.
from fastapi import UploadFile, File
from .image_schemas import ImageRequest
from .image_jobs import ImageJobs
from .media import MediaStore, MAX_BYTES


def get_media(jobs=Depends(get_jobs)):
    return MediaStore(jobs.collection.database)


def media_urls(row, request):
    # Absolute public origin is optional; relative links work for the same-origin console.
    import copy
    row=copy.deepcopy(row)
    base=os.getenv('PRISM_PUBLIC_BASE_URL','').rstrip('/')
    for artifact in (row.get('result') or {}).get('artifacts',[]):
        artifact['download_url']=base+'/v1/media/'+artifact['id']+'/download'
    return row


@app.get('/tools/prompt-images',include_in_schema=False)
def prompt_image_page():
    return console_page('prompt-images.html')


@app.get('/tools/images',include_in_schema=False)
def image_page():
    return console_page('images.html')


@app.put('/v1/model-configs/{config_id}/image-generation',dependencies=[Depends(require_service_auth)])
def bind_image_model(config_id: UUID,response: Response,configs=Depends(get_model_configs)):
    response.headers['Cache-Control']='no-store'
    return config_operation(lambda:configs.bind_image_model(str(config_id)))


@app.post('/v1/images/generate/jobs',status_code=202,dependencies=[Depends(require_service_auth)])
def submit_image_job(payload: ImageRequest,response: Response,request: Request,
                     idempotency_key: str=Header(min_length=1,max_length=128),
                     jobs=Depends(get_jobs),configs=Depends(get_model_configs),media=Depends(get_media)):
    images=ImageJobs(jobs.collection.database)
    row=job_operation(lambda:images.existing(idempotency_key,payload))
    if row is None:
        config=config_operation(lambda:configs.resolve_image_model(payload.model_config_id))
        row=job_operation(lambda:images.submit_image(payload,idempotency_key,config,media))
    response.headers['Cache-Control']='no-store'
    response.headers['Location']='/v1/jobs/'+row['id']
    return media_urls(row,request)


@app.post('/v1/images/preview',dependencies=[Depends(require_service_auth)])
def preview_image(payload: ImageRequest,response: Response):
    from .image_prompts import compile_image_prompt,PROMPT_VERSION
    from .style_rules import adapt_visual_style
    prompt,negative=compile_image_prompt(payload)
    response.headers['Cache-Control']='no-store'
    return {'prompt':prompt,'negative_prompt':negative,
            'prompt_version':'raw-prompt-v1' if payload.prompt_mode=='raw' else PROMPT_VERSION,
            'style_snapshot':{'mode':'raw','effective_style':''} if payload.prompt_mode=='raw' else adapt_visual_style(payload.style, payload.asset_type)}


@app.post('/v1/media',status_code=201,dependencies=[Depends(require_service_auth)])
def upload_image(response: Response,file: UploadFile=File(...),media=Depends(get_media)):
    content=file.file.read(MAX_BYTES+1)
    row=config_operation(lambda:media.put(content))
    response.headers['Cache-Control']='no-store'
    return row | {'download_url':os.getenv('PRISM_PUBLIC_BASE_URL','').rstrip('/')+'/v1/media/'+row['id']+'/download'}


@app.get('/v1/media/{media_id}/download',dependencies=[Depends(require_service_auth)])
def download_image(media_id: UUID,media=Depends(get_media)):
    row,path=config_operation(lambda:media.get(str(media_id)))
    return FileResponse(path,media_type=row['mime_type'],headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


# Read-only test fixtures; imports are explicit and never mutate business assets.
from .image_samples import ImageSamples


def get_image_samples(jobs=Depends(get_jobs)):
    return ImageSamples(jobs.collection.database)


def sample_operation(operation):
    try:
        return operation()
    except ConfigError as exc:
        raise HTTPException(exc.status, detail={'code': exc.code, **({'context':exc.details} if exc.details is not None else {})}) from None
    except Exception:
        raise HTTPException(503, detail={'code': 'image_sample_store_unavailable'}) from None


@app.get('/v1/image-samples', dependencies=[Depends(require_service_auth)])
def list_image_samples(response: Response,
                       kind: Literal['style', 'character', 'scene', 'prop'] | None = None,
                       limit: int = Query(100, ge=1, le=100),
                       offset: int = Query(0, ge=0, le=10000), samples=Depends(get_image_samples)):
    response.headers['Cache-Control'] = 'no-store'
    return sample_operation(lambda: samples.list(kind, limit, offset))


@app.get('/v1/image-samples/{sample_id}', dependencies=[Depends(require_service_auth)])
def image_sample_detail(sample_id: UUID, response: Response, samples=Depends(get_image_samples)):
    response.headers['Cache-Control'] = 'no-store'
    return sample_operation(lambda: samples.get(str(sample_id)))


# Short-film planning and H3 video production. Existing contracts remain unchanged.
from .production_schemas import BoardInput, DraftInput, VideoInput, AssemblyInput, BoardContent
from .production_jobs import ProductionJobs
from .storyboards import Drafts, compile_video

@app.get('/tools/storyboards',include_in_schema=False)
def storyboard_page(): return console_page('storyboards.html')

@app.get('/tools/videos',include_in_schema=False)
def video_page(): return console_page('videos.html')

from .h3_requests import H3ReferenceInput

@app.get('/tools/h3-ref2va-v1',include_in_schema=False)
def h3_reference_page(): return console_page('h3-ref2va-v1.html')

@app.post('/v1/videos/h3-ref2va-v1/jobs',status_code=202,dependencies=[Depends(require_service_auth)])
def submit_h3_reference(payload:H3ReferenceInput,response:Response,idempotency_key:str=Header(min_length=1,max_length=128),
                        jobs=Depends(get_jobs),configs=Depends(get_model_configs),media=Depends(get_media)):
    return production_submit('video_generation',payload,idempotency_key,response,jobs,configs,media)

@app.post('/v1/videos/h3-ref2va-v1/jobs/{job_id}/update',dependencies=[Depends(require_service_auth)])
def update_h3_task(job_id:UUID,payload:H3ReferenceInput,idempotency_key:str=Header(min_length=1,max_length=128),
                   expected_revision:int=Header(ge=1,alias='X-Input-Revision'),jobs=Depends(get_jobs),
                   configs=Depends(get_model_configs),media=Depends(get_media)):
    from .h3_versions import update_task
    return job_operation(lambda:update_task(ProductionJobs(jobs.collection.database),str(job_id),payload,idempotency_key,expected_revision,configs,media))

@app.get('/v1/videos/h3-ref2va-v1/jobs/{job_id}/versions',dependencies=[Depends(require_service_auth)])
def h3_task_versions(job_id:UUID,response:Response,jobs=Depends(get_jobs)):
    from .h3_versions import versions
    response.headers['Cache-Control']='no-store'
    return job_operation(lambda:versions(jobs.collection.database,str(job_id)))

@app.get('/tools/merges',include_in_schema=False)
def merge_page(): return console_page('merges.html')

@app.put('/v1/model-configs/{config_id}/storyboard-generation',dependencies=[Depends(require_service_auth)])
def bind_storyboard(config_id: UUID,configs=Depends(get_model_configs)):
    return config_operation(lambda:configs.bind_production_model('storyboard_generation',str(config_id)))

@app.put('/v1/model-configs/{config_id}/video-generation',dependencies=[Depends(require_service_auth)])
def bind_video(config_id: UUID,configs=Depends(get_model_configs)):
    return config_operation(lambda:configs.bind_production_model('video_generation',str(config_id)))

def production_submit(kind,payload,key,response,jobs,configs,media):
    repo=ProductionJobs(jobs.collection.database)
    row=job_operation(lambda:repo.submit_new(kind,payload,key,configs,media))
    response.headers['Cache-Control']='no-store';response.headers['Location']='/v1/jobs/'+row['id']
    return row

@app.post('/v1/storyboards/generate/jobs',status_code=202,dependencies=[Depends(require_service_auth)])
def submit_board(payload:BoardInput,response:Response,idempotency_key:str=Header(min_length=1,max_length=128),
                 jobs=Depends(get_jobs),configs=Depends(get_model_configs),media=Depends(get_media)):
    if payload.scene_context is not None: raise HTTPException(422,detail={'code':'use_script_scene_endpoint'})
    return production_submit('storyboard_generation',payload,idempotency_key,response,jobs,configs,media)

@app.post('/v1/videos/generate/jobs',status_code=202,dependencies=[Depends(require_service_auth)])
def submit_video(payload:VideoInput,response:Response,idempotency_key:str=Header(min_length=1,max_length=128),
                 jobs=Depends(get_jobs),configs=Depends(get_model_configs),media=Depends(get_media)):
    return production_submit('video_generation',payload,idempotency_key,response,jobs,configs,media)

@app.post('/v1/videos/assemble/jobs',status_code=202,dependencies=[Depends(require_service_auth)])
def submit_assembly(payload:AssemblyInput,response:Response,idempotency_key:str=Header(min_length=1,max_length=128),
                 jobs=Depends(get_jobs),configs=Depends(get_model_configs),media=Depends(get_media)):
    return production_submit('video_assembly',payload,idempotency_key,response,jobs,configs,media)

@app.get('/v1/storyboards',dependencies=[Depends(require_service_auth)])
def list_boards(response:Response,jobs=Depends(get_jobs)):
    response.headers['Cache-Control']='no-store'
    return job_operation(lambda:Drafts(jobs.collection.database).list())

@app.get('/v1/storyboards/{draft_id}',dependencies=[Depends(require_service_auth)])
def get_board(draft_id:UUID,response:Response,jobs=Depends(get_jobs)):
    response.headers['Cache-Control']='no-store'
    return job_operation(lambda:Drafts(jobs.collection.database).get(str(draft_id)))

@app.post('/v1/storyboards',status_code=201,dependencies=[Depends(require_service_auth)])
def create_board(payload:DraftInput,jobs=Depends(get_jobs)):
    return job_operation(lambda:Drafts(jobs.collection.database).save(payload))

@app.put('/v1/storyboards/{draft_id}',dependencies=[Depends(require_service_auth)])
def update_board(draft_id:UUID,payload:DraftInput,jobs=Depends(get_jobs)):
    return job_operation(lambda:Drafts(jobs.collection.database).save(payload,str(draft_id)))

@app.get('/v1/storyboards/{draft_id}/shots/{shot_id}/prompt',dependencies=[Depends(require_service_auth)])
def shot_prompt(draft_id:UUID,shot_id:str,response:Response,jobs=Depends(get_jobs)):
    def compile():
        draft=Drafts(jobs.collection.database).get(str(draft_id))
        shot=next((s for s in BoardContent.model_validate(draft['content']).shots if s.id==shot_id),None)
        if not shot:raise ConfigError('shot_not_found',404)
        return {'prompt':compile_video(BoardInput.model_validate(draft['input']),shot),'revision':draft['revision']}
    response.headers['Cache-Control']='no-store'
    return job_operation(compile)


@app.post('/v1/jobs/{job_id}/retry-video',dependencies=[Depends(require_service_auth)])
def retry_video_task(job_id:UUID,idempotency_key:str=Header(min_length=1,max_length=128),
                     jobs=Depends(get_jobs),configs=Depends(get_model_configs),media=Depends(get_media)):
    return job_operation(lambda:ProductionJobs(jobs.collection.database).retry_video(str(job_id),idempotency_key,configs,media))

@app.post('/v1/jobs/{job_id}/recover-video',dependencies=[Depends(require_service_auth)])
def recover_video(job_id:UUID,jobs=Depends(get_jobs)):
    return job_operation(lambda:ProductionJobs(jobs.collection.database).recover_video(str(job_id)))


@app.post('/v1/jobs/{job_id}/recover-storyboard',dependencies=[Depends(require_service_auth)])
def recover_storyboard(job_id:UUID,jobs=Depends(get_jobs)):
    return job_operation(lambda:ProductionJobs(jobs.collection.database).recover_board(str(job_id)))


from .image_polish import ImagePolishInput

@app.put('/v1/model-configs/{config_id}/image-polish',dependencies=[Depends(require_service_auth)])
def bind_image_polish(config_id:UUID,configs=Depends(get_model_configs)):
    return config_operation(lambda:configs.bind_production_model('image_polish',str(config_id)))

@app.post('/v1/images/polish/jobs',status_code=202,dependencies=[Depends(require_service_auth)])
def submit_image_polish(payload:ImagePolishInput,response:Response,idempotency_key:str=Header(min_length=1,max_length=128),
                        jobs=Depends(get_jobs),configs=Depends(get_model_configs),media=Depends(get_media)):
    return production_submit('image_polish',payload,idempotency_key,response,jobs,configs,media)


from .script_plans import PlanInput, PlanSave, SceneBoardInput, Plans

@app.get('/tools/scripts',include_in_schema=False)
def script_plan_page(): return console_page('scripts.html')

@app.post('/v1/scripts/plan/jobs',status_code=202,dependencies=[Depends(require_service_auth)])
def submit_script_plan(payload:PlanInput,response:Response,idempotency_key:str=Header(min_length=1,max_length=128),
                       jobs=Depends(get_jobs),configs=Depends(get_model_configs),media=Depends(get_media)):
    return production_submit('script_planning',payload,idempotency_key,response,jobs,configs,media)

@app.get('/v1/script-plans',dependencies=[Depends(require_service_auth)])
def list_script_plans(work_id:UUID|None=None,edition_id:UUID|None=None,episode_id:str|None=None,jobs=Depends(get_jobs)):
    return job_operation(lambda:Plans(jobs.collection.database).list(work_id,edition_id,episode_id))

@app.get('/v1/script-plans/{plan_id}',dependencies=[Depends(require_service_auth)])
def get_script_plan(plan_id:UUID,response:Response,jobs=Depends(get_jobs)):
    response.headers['Cache-Control']='no-store'
    return job_operation(lambda:Plans(jobs.collection.database).get(str(plan_id)))

@app.post('/v1/script-plans',status_code=201,dependencies=[Depends(require_service_auth)])
def save_script_plan(payload:PlanSave,jobs=Depends(get_jobs)):
    return job_operation(lambda:Plans(jobs.collection.database).save(payload))

@app.put('/v1/script-plans/{plan_id}',dependencies=[Depends(require_service_auth)])
def update_script_plan(plan_id:UUID,payload:PlanSave,jobs=Depends(get_jobs)):
    return job_operation(lambda:Plans(jobs.collection.database).save(payload,str(plan_id)))

@app.get('/v1/script-plans/{plan_id}/scene-jobs',dependencies=[Depends(require_service_auth)])
def scene_board_jobs(plan_id:UUID,revision:int=Query(ge=1),jobs=Depends(get_jobs)):
    return job_operation(lambda:Plans(jobs.collection.database).scene_jobs(str(plan_id),revision))

@app.post('/v1/script-plans/{plan_id}/scenes/{scene_id}/storyboard/jobs',status_code=202,dependencies=[Depends(require_service_auth)])
def submit_scene_board(plan_id:UUID,scene_id:str,payload:SceneBoardInput,response:Response,
                       idempotency_key:str=Header(min_length=1,max_length=128),
                       jobs=Depends(get_jobs),configs=Depends(get_model_configs),media=Depends(get_media)):
    board=job_operation(lambda:Plans(jobs.collection.database).scene_board(str(plan_id),scene_id,payload))
    return production_submit('storyboard_generation',board,idempotency_key,response,jobs,configs,media)


# Queue-safe runtime maintenance, using registered ComfyUI connections only.
from .comfy_control import GPUControl,ComfyControl

@app.get('/tools/comfyui',include_in_schema=False)
def comfy_control_page(): return console_page('comfyui.html')

from .comfy_api_configs import ComfyApiConfigs,ComfyApiInput

def get_comfy_api_configs(jobs=Depends(get_jobs)):
    return ComfyApiConfigs(jobs.collection.database)

@app.get('/v1/comfyui-api-configs',dependencies=[Depends(require_service_auth)])
def list_comfy_api_configs(response:Response,configs=Depends(get_comfy_api_configs)):
    response.headers['Cache-Control']='no-store'
    return config_operation(configs.list)

@app.post('/v1/comfyui-api-configs',status_code=201,dependencies=[Depends(require_service_auth)])
def create_comfy_api_config(payload:ComfyApiInput,configs=Depends(get_comfy_api_configs)):
    return config_operation(lambda:configs.save(payload))

@app.get('/v1/comfyui-api-configs/{config_id}',dependencies=[Depends(require_service_auth)])
def comfy_api_config_detail(config_id:UUID,response:Response,configs=Depends(get_comfy_api_configs)):
    response.headers['Cache-Control']='no-store'
    return config_operation(lambda:configs.public(configs.get(str(config_id))))

@app.put('/v1/comfyui-api-configs/{config_id}',dependencies=[Depends(require_service_auth)])
def update_comfy_api_config(config_id:UUID,payload:ComfyApiInput,configs=Depends(get_comfy_api_configs)):
    return config_operation(lambda:configs.save(payload,str(config_id)))

@app.get('/v1/comfyui/status',dependencies=[Depends(require_service_auth)])
def comfy_runtime_status(response:Response,jobs=Depends(get_jobs)):
    response.headers['Cache-Control']='no-store'
    return job_operation(lambda:ComfyControl(jobs.collection.database).status())

@app.post('/v1/gpu/pause',dependencies=[Depends(require_service_auth)])
def pause_gpu(jobs=Depends(get_jobs)):
    return job_operation(lambda:GPUControl(jobs.collection.database).pause())

@app.post('/v1/gpu/resume',dependencies=[Depends(require_service_auth)])
def resume_gpu(jobs=Depends(get_jobs)):
    return job_operation(lambda:GPUControl(jobs.collection.database).resume())

@app.post('/v1/comfyui/free',dependencies=[Depends(require_service_auth)])
def free_all_comfy(jobs=Depends(get_jobs)):
    return job_operation(lambda:ComfyControl(jobs.collection.database).free())

@app.post('/v1/comfyui/{config_id}/free',dependencies=[Depends(require_service_auth)])
def free_comfy_instance(config_id:UUID,jobs=Depends(get_jobs)):
    return job_operation(lambda:ComfyControl(jobs.collection.database).free(str(config_id)))

@app.post('/v1/jobs/{job_id}/pause',dependencies=[Depends(require_service_auth)])
def pause_queued_gpu_job(job_id:UUID,jobs=Depends(get_jobs)):
    return job_operation(lambda:GPUControl(jobs.collection.database).job_pause(str(job_id),True))

@app.post('/v1/jobs/{job_id}/resume',dependencies=[Depends(require_service_auth)])
def resume_queued_gpu_job(job_id:UUID,jobs=Depends(get_jobs)):
    return job_operation(lambda:GPUControl(jobs.collection.database).job_pause(str(job_id),False))

# Source library: immutable original text and confirmed episode editions.
from .works import Works,WorkCreate,WorkRename,BreakdownOptions,EditionConfirm,WorkContext

@app.get('/tools/works',include_in_schema=False)
def works_page():return console_page('works.html')

@app.get('/v1/works',dependencies=[Depends(require_service_auth)])
def works_list(response:Response,limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0,le=10000),jobs=Depends(get_jobs)):
    response.headers['Cache-Control']='no-store'
    return job_operation(lambda:Works(jobs.collection.database).list(limit,offset))

@app.post('/v1/works',status_code=201,dependencies=[Depends(require_service_auth)])
def work_create(payload:WorkCreate,jobs=Depends(get_jobs)):
    return job_operation(lambda:Works(jobs.collection.database).create(payload))

@app.get('/v1/works/{work_id}',dependencies=[Depends(require_service_auth)])
def work_get(work_id:UUID,response:Response,jobs=Depends(get_jobs)):
    response.headers['Cache-Control']='no-store'
    return job_operation(lambda:Works(jobs.collection.database).get(work_id))

@app.patch('/v1/works/{work_id}',dependencies=[Depends(require_service_auth)])
def work_rename(work_id:UUID,payload:WorkRename,jobs=Depends(get_jobs)):
    return job_operation(lambda:Works(jobs.collection.database).rename(work_id,payload))

@app.post('/v1/works/{work_id}/breakdown/jobs',status_code=202,dependencies=[Depends(require_service_auth)])
def work_breakdown(work_id:UUID,payload:BreakdownOptions,response:Response,idempotency_key:str=Header(min_length=1,max_length=128),jobs=Depends(get_jobs),configs=Depends(get_model_configs),media=Depends(get_media)):
    # Replay accepted request even if display title changed after submission.
    previous=jobs.collection.find_one({'idempotency_key':idempotency_key})
    if previous:
        if previous['type']!='work_breakdown' or previous['request']['work_id']!=str(work_id) or any(previous['request'][k]!=v for k,v in payload.model_dump().items()):
            raise HTTPException(409,detail={'code':'idempotency_conflict'})
        response.headers['Location']='/v1/jobs/'+previous['_id']
        return jobs.public(previous)
    request=job_operation(lambda:Works(jobs.collection.database).request(work_id,payload))
    return production_submit('work_breakdown',request,idempotency_key,response,jobs,configs,media)

@app.get('/v1/works/{work_id}/breakdown/jobs',dependencies=[Depends(require_service_auth)])
def work_breakdown_history(work_id:UUID,response:Response,jobs=Depends(get_jobs)):
    response.headers['Cache-Control']='no-store'
    return job_operation(lambda:{'items':[jobs.public(r) for r in jobs.collection.find({'type':'work_breakdown','request.work_id':str(work_id)},{'request':0,'result':0,'effective_prompt':0}).sort('created_at',-1).limit(100)]})

@app.get('/v1/works/{work_id}/editions',dependencies=[Depends(require_service_auth)])
def work_editions(work_id:UUID,response:Response,jobs=Depends(get_jobs)):
    response.headers['Cache-Control']='no-store'
    return job_operation(lambda:Works(jobs.collection.database).list_editions(work_id))

@app.post('/v1/works/{work_id}/editions',status_code=201,dependencies=[Depends(require_service_auth)])
def work_confirm(work_id:UUID,payload:EditionConfirm,jobs=Depends(get_jobs)):
    return job_operation(lambda:Works(jobs.collection.database).confirm(work_id,payload))

@app.get('/v1/works/{work_id}/editions/{edition_id}',dependencies=[Depends(require_service_auth)])
def work_edition(work_id:UUID,edition_id:UUID,response:Response,jobs=Depends(get_jobs)):
    response.headers['Cache-Control']='no-store'
    return job_operation(lambda:Works(jobs.collection.database).edition(work_id,edition_id))

@app.get('/v1/works/{work_id}/editions/{edition_id}/episodes/{episode_id}',dependencies=[Depends(require_service_auth)])
def work_episode(work_id:UUID,edition_id:UUID,episode_id:str,response:Response,jobs=Depends(get_jobs)):
    response.headers['Cache-Control']='no-store'
    return job_operation(lambda:Works(jobs.collection.database).episode(WorkContext(work_id=work_id,edition_id=edition_id,episode_id=episode_id)))

from .work_assets import WorkAssets,ConfirmEntities,BatchRequest,SelectCandidate
from .works import EpisodeSelection

@app.post('/v1/works/{work_id}/entities/jobs',status_code=202,dependencies=[Depends(require_service_auth)])
def work_entities_submit(work_id:UUID,payload:EpisodeSelection,response:Response,idempotency_key:str=Header(min_length=1,max_length=128),jobs=Depends(get_jobs),configs=Depends(get_model_configs),media=Depends(get_media)):
    previous=jobs.collection.find_one({'idempotency_key':idempotency_key})
    if previous:
        r=previous['request']
        if previous['type']!='work_entities' or r['work_id']!=str(work_id) or r['edition_id']!=str(payload.edition_id) or sorted(r['episode_ids'])!=sorted(payload.episode_ids):
            raise HTTPException(409,detail={'code':'idempotency_conflict'})
        response.headers['Location']='/v1/jobs/'+previous['_id'];return jobs.public(previous)
    request=job_operation(lambda:WorkAssets(jobs.collection.database).request(work_id,payload))
    return production_submit('work_entities',request,idempotency_key,response,jobs,configs,media)

@app.get('/v1/works/{work_id}/entities/jobs',dependencies=[Depends(require_service_auth)])
def work_entities_history(work_id:UUID,edition_id:UUID|None=None,episode_ids:str='',jobs=Depends(get_jobs)):
    return job_operation(lambda:WorkAssets(jobs.collection.database).history(work_id,edition_id,[e for e in episode_ids.split(',') if e]))

@app.post('/v1/works/{work_id}/entities/confirm',dependencies=[Depends(require_service_auth)])
def work_entities_confirm(work_id:UUID,payload:ConfirmEntities,jobs=Depends(get_jobs)):
    return job_operation(lambda:WorkAssets(jobs.collection.database).confirm(work_id,payload))

@app.get('/v1/works/{work_id}/assets',dependencies=[Depends(require_service_auth)])
def work_assets_list(work_id:UUID,edition_id:UUID|None=None,episode_ids:str='',jobs=Depends(get_jobs)):
    return job_operation(lambda:{'items':WorkAssets(jobs.collection.database).list(work_id,edition_id,[x for x in episode_ids.split(',') if x])})

@app.post('/v1/works/{work_id}/image-batches',status_code=202,dependencies=[Depends(require_service_auth)])
def work_image_batch(work_id:UUID,payload:BatchRequest,idempotency_key:str=Header(min_length=1,max_length=128),jobs=Depends(get_jobs)):
    return job_operation(lambda:WorkAssets(jobs.collection.database).batch(work_id,payload,idempotency_key))

@app.get('/v1/works/{work_id}/image-batches',dependencies=[Depends(require_service_auth)])
def work_image_batches(work_id:UUID,jobs=Depends(get_jobs)):
    return job_operation(lambda:{'items':[WorkAssets.public(r) for r in jobs.collection.database.prism_image_batches.find({'scope.work_id':str(work_id)}).sort('created_at',-1).limit(50)]})

@app.put('/v1/works/{work_id}/assets/{variant_id}/candidate',dependencies=[Depends(require_service_auth)])
def work_candidate(work_id:UUID,variant_id:UUID,payload:SelectCandidate,jobs=Depends(get_jobs)):
    return job_operation(lambda:WorkAssets(jobs.collection.database).choose(work_id,variant_id,payload))

@app.get('/v1/works/{work_id}/production',dependencies=[Depends(require_service_auth)])
def work_production(work_id:UUID,edition_id:UUID,episode_id:str,jobs=Depends(get_jobs)):
    def read():
        scope={'work_id':str(work_id),'edition_id':str(edition_id),'episode_id':episode_id}
        episode=Works(jobs.collection.database).episode(WorkContext(**scope))
        plans=[Plans.public(r) for r in jobs.collection.database.script_plans.find({'input.work_context.'+k:v for k,v in scope.items()}).sort('updated_at',-1).limit(100)]
        drafts=[Drafts.public(r) for r in jobs.collection.database.storyboard_drafts.find({'input.scene_context.work_context.'+k:v for k,v in scope.items()}).sort('updated_at',-1).limit(100)]
        return {'episode':episode,'plans':plans,'drafts':drafts,'assets':WorkAssets(jobs.collection.database).list(work_id,edition_id,[episode_id])}
    return job_operation(read)
