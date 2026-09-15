"""Client-only licensing. Contains no Mercado Pago credentials or signing key."""
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import sqlite3
from urllib.parse import urlsplit, parse_qs
import uuid

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from novarix_stock.database import StockDatabase
from novarix_stock.models import AppPlan, LicenseState


class LicenseError(Exception):
    pass


class InstallationStore:
    """Separate local cache: installation UUID, installation credential, signed lease."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS identity (
                id INTEGER PRIMARY KEY CHECK(id=1), installation_id TEXT NOT NULL,
                credential TEXT NOT NULL, lease TEXT, last_seen INTEGER NOT NULL DEFAULT 0)''')
            db.execute('INSERT OR IGNORE INTO identity(id, installation_id, credential) VALUES(1, ?, ?)',
                       (str(uuid.uuid4()), secrets.token_urlsafe(32)))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def read(self):
        with self.connect() as db:
            return dict(db.execute('SELECT * FROM identity WHERE id=1').fetchone())

    def save(self, lease):
        with self.connect() as db:
            db.execute('UPDATE identity SET lease=? WHERE id=1', (lease,))


def safe_checkout(url):
    try:
        value = urlsplit(url)
        if (value.scheme != 'https' or value.hostname != 'www.mercadopago.com.ar'
                or value.port not in (None, 443) or value.username or value.password
                or value.path != '/subscriptions/checkout'
                or len(parse_qs(value.query).get('preapproval_id', [])) != 1):
            raise ValueError()
        return url
    except (ValueError, TypeError):
        raise LicenseError('La direcciÃ³n de checkout recibida no es segura.') from None


class LicenseClient:
    def __init__(self, store, base_url='', public_key='', transport=None, clock=None):
        self.store = store
        self.base_url = base_url.rstrip('/')
        self.public_key = public_key
        self.transport = transport
        self.config_error = None
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        if self.base_url:
            url = urlsplit(self.base_url)
            local = url.scheme == 'http' and url.hostname in ('127.0.0.1', 'localhost', '::1')
            if (not local and url.scheme != 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise LicenseError('El backend debe usar HTTPS; HTTP solo se admite en localhost.')

    @classmethod
    def from_env(cls, path):
        store = InstallationStore(path)
        try:
            return cls(store, os.getenv('STOCK_LICENSE_API_URL', ''),
                       os.getenv('STOCK_LICENSE_PUBLIC_KEY', ''))
        except LicenseError as error:
            # A bad backend URL must not prevent inventory access or discard a signed cache.
            result = cls(store, public_key=os.getenv('STOCK_LICENSE_PUBLIC_KEY', ''))
            result.config_error = str(error)
            return result

    def _request(self, method, path, **kwargs):
        if self.config_error:
            raise LicenseError(self.config_error)
        if not self.base_url or not self.public_key:
            raise LicenseError('STOCK Pro TEST todavÃ­a no estÃ¡ configurado. ConsultÃ¡ la guÃ­a de la etapa 8.')
        identity = self.store.read()
        try:
            with httpx.Client(base_url=self.base_url, timeout=httpx.Timeout(20, connect=3),
                              follow_redirects=False, transport=self.transport) as client:
                response = client.request(method, path, headers={'Authorization': 'Bearer ' + identity['credential']}, **kwargs)
                if response.status_code == 409:
                    raise LicenseError('Existe una solicitud pendiente o cambiÃ³ el importe. ActualizÃ¡ la licencia y revisÃ¡ Mercado Pago TEST.')
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError):
            raise LicenseError('No se pudo contactar o validar el backend. Se conserva la tolerancia offline vigente.') from None

    def register(self):
        self._request('POST', '/installations/register', json={'installation_id': self.store.read()['installation_id']})

    def offer(self):
        result = self._request('GET', '/plans/pro')
        self._check_offer(result)
        return result

    @staticmethod
    def _check_offer(result):
        from decimal import Decimal, InvalidOperation
        try:
            amount = Decimal(result['monthly_amount'])
            if (result['currency'] != 'ARS' or result['mode'] not in ('test', 'production') or not amount.is_finite()
                    or amount <= 0 or amount > 10000000 or amount != amount.quantize(Decimal('.01'))):
                raise ValueError()
        except (KeyError, TypeError, ValueError, InvalidOperation):
            raise LicenseError('El backend devolviÃ³ un importe o entorno invÃ¡lido.') from None

    def start(self, expected_amount, payer_email=None):
        self.register()
        result = self._request('POST', '/subscriptions/start', json={
            'installation_id': self.store.read()['installation_id'], 'expected_amount': expected_amount, 'payer_email': payer_email})
        self._check_offer(result)
        from decimal import Decimal
        if Decimal(result['monthly_amount']) != Decimal(expected_amount):
            raise LicenseError('El importe cambiÃ³; no se abrirÃ¡ el checkout.')
        if result.get('checkout_url'):
            result['checkout_url'] = safe_checkout(result['checkout_url'])
        return result

    def verify(self, token):
        try:
            if not isinstance(token, str) or len(token) > 8192:
                raise ValueError()
            encoded, signature = token.split('.')
            key = Ed25519PublicKey.from_public_bytes(base64.b64decode(self.public_key, validate=True))
            key.verify(base64.urlsafe_b64decode(signature), encoded.encode())
            payload = json.loads(base64.urlsafe_b64decode(encoded))
            if (payload['v'] != 1 or payload['aud'] != 'stock-novarix'
                    or payload['installation_id'] != self.store.read()['installation_id']
                    or payload['status'] not in ('FREE', 'PRO_ACTIVE', 'PRO_EXPIRED')
                    or not all(type(payload[k]) is int for k in ('issued_at', 'validated_at', 'valid_until', 'revision'))
                    or payload['issued_at'] < payload['validated_at']
                    or not 0 < payload['valid_until'] - payload['validated_at'] <= 3 * 86400
                    or payload['issued_at'] > int(self.clock().timestamp()) + 300
                    or (payload['status'] == 'PRO_ACTIVE' and type(payload['paid_until']) is not int)):
                raise ValueError()
            uuid.UUID(payload['license_id'])
            return payload
        except (ValueError, TypeError, KeyError, InvalidSignature, UnicodeError):
            raise LicenseError('La licencia recibida no tiene una firma vÃ¡lida para esta instalaciÃ³n.') from None

    def refresh(self):
        self.register()
        result = self._request('GET', '/licenses/' + self.store.read()['installation_id'])
        token = result.get('lease')
        payload = self.verify(token)
        old_token = self.store.read()['lease']
        if old_token:
            try:
                old = self.verify(old_token)
            except LicenseError:
                old = None
            if old and (payload['revision'] < old['revision'] or payload['issued_at'] < old['issued_at']):
                raise LicenseError('Se rechazÃ³ una validaciÃ³n anterior a la ya guardada.')
        self.store.save(token)
        return self.effective_state()

    def effective_state(self):
        identity = self.store.read()
        if not identity['lease']:
            return LicenseState(AppPlan.FREE, None, None, None)
        try:
            payload = self.verify(identity['lease'])
        except LicenseError:
            return LicenseState(AppPlan.PRO_EXPIRED, None, None, None)
        now = int(self.clock().timestamp())
        # Ordinary clock rollback detection; a modified program/OS is outside this threat model.
        clock_rollback = now + 300 < identity['last_seen']
        if now > identity['last_seen'] + 60:
            with self.store.connect() as db:
                db.execute('UPDATE identity SET last_seen=MAX(last_seen, ?) WHERE id=1', (now,))
        status = AppPlan(payload['status'])
        if status is AppPlan.PRO_ACTIVE and (now >= payload['valid_until'] or clock_rollback):
            status = AppPlan.PRO_EXPIRED
        dt = lambda stamp: datetime.fromtimestamp(stamp, timezone.utc) if stamp is not None else None
        return LicenseState(status, payload['license_id'], dt(payload['valid_until']), dt(payload['validated_at']))


class LicensedStockDatabase(StockDatabase):
    """Production entry point: reuse stock limits with an authenticated plan source."""
    def __init__(self, database_path, license_client):
        self.license_client = license_client
        super().__init__(database_path)

    def _get_license_state(self, connection):
        return self.license_client.effective_state()

