"""Run: uvicorn lumenx_prism.api:app --app-dir src --host 127.0.0.1 --port 17178."""
import os
import secrets
import time
import logging
from typing import Literal
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Response, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .history import MongoHistory
from .extraction import EntityExtractionError, EntityExtractor
from .llm_adapter import LLMAdapter
from .schemas import EntityExtractionRequest, EntityExtractionResult

app = FastAPI(title='LumenX Prism', version='0.1.0')
bearer = HTTPBearer(auto_error=False)
logger = logging.getLogger(__name__)
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


def get_extractor():
    adapter = LLMAdapter(timeout=120, max_retries=0)
    try:
        yield EntityExtractor(adapter)
    finally:
        if adapter._client is not None:
            adapter._client.close()


@app.get('/health')
def health():
    # Liveness only: does not make paid provider calls or reveal configuration.
    return {'ok': True, 'service': 'lumenx-prism', 'version': '0.1.0'}


@app.get('/v1/test-console/config', dependencies=[Depends(require_service_auth)])
def console_config(response: Response):
    response.headers['Cache-Control'] = 'no-store'
    adapter = LLMAdapter()
    return {'provider': adapter.provider, 'model': adapter._get_default_model(),
            'model_configured': adapter.is_configured,
            'history_configured': bool(os.getenv('PRISM_MONGODB_URI', '').strip())}


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
    record_id = history_operation(lambda: history.begin(request)) if history is not None else None
    if record_id:
        response.headers['X-Prism-History-Id'] = record_id
    headers = {'X-Prism-History-Id': record_id} if record_id else {}

    def finish(result=None, error_code=None):
        if record_id:
            try:
                history.finish(record_id, result=result, error_code=error_code,
                               duration_ms=round((time.monotonic() - started) * 1000))
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
