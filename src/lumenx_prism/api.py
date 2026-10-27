"""Run: uvicorn lumenx_prism.api:app --app-dir src --host 127.0.0.1 --port 17178."""
import os
import secrets

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .extraction import EntityExtractionError, EntityExtractor
from .llm_adapter import LLMAdapter
from .schemas import EntityExtractionRequest, EntityExtractionResult

app = FastAPI(title='LumenX Prism', version='0.1.0')
bearer = HTTPBearer(auto_error=False)


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


@app.post('/v1/scripts/extract-entities', response_model=EntityExtractionResult,
          dependencies=[Depends(require_service_auth)])
def extract_entities(request: EntityExtractionRequest,
                     extractor: EntityExtractor = Depends(get_extractor)):
    # Synchronous handler executes in FastAPI's threadpool, not the event loop.
    try:
        return extractor.extract(request)
    except EntityExtractionError as exc:
        raise HTTPException(status_code=503 if exc.code == 'not_configured' else 502,
                            detail={'code': exc.code}) from None
