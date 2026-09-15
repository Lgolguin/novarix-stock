import hashlib
import hmac
import re
import time
from uuid import UUID

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from backend.config import Settings
from backend.database import Repository
from backend.licenses import LicenseService, ServiceError
from backend.mercado_pago import MercadoPago, ProviderError


class Installation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    installation_id: UUID


class Start(Installation):
    expected_amount: str = Field(pattern=r'^\d{1,8}(\.\d{1,2})?$', max_length=11)
    payer_email: str | None = Field(default=None, min_length=3, max_length=254, pattern=r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def bearer(value):
    if not value or not re.fullmatch(r'Bearer [A-Za-z0-9_-]{40,100}', value):
        raise ServiceError('Autenticación requerida.', 401)
    return value[7:]


def verify_webhook(signature, request_id, data_id, secret, now=None):
    if not signature or not request_id or not data_id:
        raise ServiceError('Firma requerida.', 401)
    try:
        parts = [part.strip().split('=', 1) for part in signature.split(',')]
        if len(parts) != 2 or len(dict(parts)) != 2:
            raise ValueError()
        values = dict(parts)
        ts = values['ts']
        seconds = int(ts) / (1000 if len(ts) == 13 else 1)
        if len(ts) not in (10, 13) or abs((now or time.time()) - seconds) > 300:
            raise ValueError()
        manifest = f'id:{data_id.lower()};request-id:{request_id};ts:{ts};'
        digest = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(values['v1'], digest):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise ServiceError('Firma inválida o expirada.', 401) from None
    return hashlib.sha256(manifest.encode()).hexdigest()


def create_app(settings=None, provider=None, clock=None):
    settings = settings or Settings.from_env()
    settings.validate()
    repository = Repository(settings.database_path)
    service = LicenseService(settings, repository, provider or MercadoPago(settings), **({'clock': clock} if clock else {}))
    app = FastAPI(title='STOCK Pro — TEST', docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service = service

    @app.exception_handler(ServiceError)
    async def service_error(request, error):
        return JSONResponse({'detail': str(error)}, status_code=error.status)

    @app.exception_handler(ProviderError)
    async def provider_error(request, error):
        return JSONResponse({'detail': 'No se pudo verificar Mercado Pago TEST. Reintentá más tarde.'}, status_code=503)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # FastAPI defaults echo input values; never echo tokens or arbitrary bodies.
        return JSONResponse({'detail': 'Solicitud inválida.'}, status_code=422)

    @app.post('/installations/register')
    def register(body: Installation, authorization: str | None = Header(default=None)):
        return service.register(str(body.installation_id), bearer(authorization))

    @app.get('/plans/pro')
    def offer():
        return service.offer()

    @app.post('/subscriptions/start')
    def start(body: Start, authorization: str | None = Header(default=None)):
        return service.start(str(body.installation_id), bearer(authorization), body.expected_amount, body.payer_email)

    @app.get('/licenses/{installation_id}')
    def license(installation_id: UUID, authorization: str | None = Header(default=None)):
        return service.license(str(installation_id), bearer(authorization))

    @app.post('/webhooks/mercadopago')
    async def webhook(request: Request):
        ids = request.query_params.getlist('data.id')
        data_id = ids[0] if len(ids) == 1 else ''
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', data_id):
            raise ServiceError('Identificador firmado requerido.', 401)
        key = verify_webhook(request.headers.get('x-signature'), request.headers.get('x-request-id'),
                             data_id, settings.webhook_secret)
        body = b''
        async for chunk in request.stream():
            body += chunk
            if len(body) > 16384:
                raise ServiceError('Notificación demasiado grande.', 413)
        import json
        try:
            event = json.loads(body)
            topic = event['type']
            if str(event['data']['id']).lower() != data_id.lower():
                raise ValueError()
            if topic not in ('subscription_preapproval', 'subscription_authorized_payment', 'payment'):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise ServiceError('Notificación inválida.', 400) from None
        return await run_in_threadpool(service.webhook, topic, data_id, topic + ':' + key)

    return app
