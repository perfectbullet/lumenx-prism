"""Run: uvicorn lumenx_prism.api:app --app-dir src --host 127.0.0.1 --port 17178."""
import os
import secrets
import time
import logging
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Response, Query, Request, Header
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .history import MongoHistory
from .jobs import Jobs
from .model_configs import ModelConfigs, ModelConfigInput, ConfigError
from .extraction import EntityExtractionError, EntityExtractor
from .llm_adapter import LLMAdapter
from .schemas import EntityExtractionRequest, EntityExtractionResult

app = FastAPI(title='LumenX Prism', version='0.1.0')
@app.exception_handler(RequestValidationError)
async def safe_config_validation(request: Request, exc: RequestValidationError):
    if request.url.path.startswith('/v1/model-configs'):
        return JSONResponse(status_code=422, content={'detail': [
            {k: error[k] for k in ('type', 'loc', 'msg')} for error in exc.errors()
        ]}, headers={'Cache-Control': 'no-store'})
    return await request_validation_exception_handler(request, exc)


bearer = HTTPBearer(auto_error=False)
logger = logging.getLogger(__name__)
static = Path(__file__).parent / 'static'
app.mount('/static', StaticFiles(directory=static), name='static')


@app.get('/', include_in_schema=False)
@app.get('/tools/entities', include_in_schema=False)
def test_page():
    return FileResponse(static / 'entities.html', headers={'Cache-Control': 'no-store'})


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
    expected = os.getenv('AI_SERVICE_API_KEY', '')
    if expected and (credentials is None or not secrets.compare_digest(
            credentials.credentials.encode('utf-8'), expected.encode('utf-8'))):
        raise HTTPException(status_code=401, detail={'code': 'unauthorized'})


def config_operation(operation):
    try:
        return operation()
    except ConfigError as exc:
        raise HTTPException(exc.status, detail={'code': exc.code}) from None
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
    return FileResponse(static / 'models.html', headers={'Cache-Control': 'no-store'})


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
        raise HTTPException(exc.status, detail={'code': exc.code}) from None
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
        config = config_operation(configs.resolve_entity_model)
        row = job_operation(lambda: jobs.submit(payload, idempotency_key, config))
    response.headers['Location'] = '/v1/jobs/' + row['id']
    return row


@app.get('/v1/jobs', dependencies=[Depends(require_service_auth)])
def list_jobs(response: Response, limit: int = Query(20, ge=1, le=100),
              offset: int = Query(0, ge=0, le=10000),
              status: Literal['queued', 'running', 'succeeded', 'failed'] | None = None,
              jobs=Depends(get_jobs)):
    response.headers['Cache-Control'] = 'no-store'
    return job_operation(lambda: jobs.list(limit, offset, status))


@app.get('/v1/jobs/{job_id}', dependencies=[Depends(require_service_auth)])
def job_detail(job_id: UUID, response: Response, jobs=Depends(get_jobs)):
    response.headers['Cache-Control'] = 'no-store'
    return job_operation(lambda: jobs.get(str(job_id)))
