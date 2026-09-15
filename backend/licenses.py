import base64
import calendar
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import hmac
import json
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from backend.mercado_pago import ProviderError, checkout_url, resource_id


def utcnow():
    return datetime.now(timezone.utc)


def parse_time(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Fecha remota sin zona horaria')
    return result.astimezone(timezone.utc)


def next_month(value):
    year, month = (value.year + 1, 1) if value.month == 12 else (value.year, value.month + 1)
    return value.replace(year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1]))


class ServiceError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


class LicenseService:
    def __init__(self, settings, repository, provider, clock=utcnow):
        self.settings, self.repository, self.provider, self.clock = settings, repository, provider, clock
        try:
            self.key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(settings.signing_key, validate=True))
        except ValueError:
            raise ValueError('STOCK_LICENSE_PRIVATE_KEY debe ser una clave Ed25519 de 32 bytes en base64.') from None

    @staticmethod
    def authenticate(db, installation_id, token):
        row = db.execute('SELECT * FROM installations WHERE installation_id=?', (installation_id,)).fetchone()
        digest = hashlib.sha256(token.encode()).hexdigest()
        if row is None or not hmac.compare_digest(row['token_hash'], digest):
            raise ServiceError('Instalación no autorizada.', 401)
        return row

    def register(self, installation_id, token):
        with self.repository.transaction() as db:
            db.execute('''INSERT OR IGNORE INTO installations
                (installation_id, token_hash, license_id) VALUES (?, ?, ?)''',
                (installation_id, hashlib.sha256(token.encode()).hexdigest(), str(uuid.uuid4())))
            self.authenticate(db, installation_id, token)
        return {'registered': True}

    def offer(self):
        return {'currency': 'ARS', 'monthly_amount': str(self.settings.monthly_ars), 'mode': self.settings.mode}

    def start(self, installation_id, token, expected_amount, payer_email=None):
        # Commit the attempt BEFORE the remote POST. A timeout never causes a blind retry.
        with self.repository.transaction() as db:
            row = self.authenticate(db, installation_id, token)
            if Decimal(expected_amount) != self.settings.monthly_ars:
                raise ServiceError('El importe cambió. Volvé a consultar STOCK Pro.')
            previous = db.execute('SELECT * FROM subscriptions WHERE reference=?', (row['current_reference'],)).fetchone()
            if previous and previous['state'] not in ('cancelled',):
                reference = previous['reference']
                created = False
            else:
                reference = str(uuid.uuid4())
                db.execute('''INSERT INTO subscriptions (reference, installation_id, amount, payer_email)
                    VALUES (?, ?, ?, ?)''', (reference, installation_id, str(self.settings.monthly_ars), (payer_email or '').strip().lower() or None))
                db.execute('UPDATE installations SET current_reference=? WHERE installation_id=?',
                           (reference, installation_id))
                created = True
        with self.repository.transaction() as db:
            attempt = db.execute('SELECT * FROM subscriptions WHERE reference=?', (reference,)).fetchone()
            if Decimal(attempt['amount']) != Decimal(expected_amount):
                raise ServiceError('Existe un checkout pendiente con otro importe. Revisalo en Mercado Pago.')
            if attempt['mp_id']:
                subscription = self.provider.subscription(attempt['mp_id'])
            elif created:
                subscription = self.provider.create_subscription(reference, attempt['amount'], attempt['payer_email'])
            else:
                subscription = self.provider.find_subscription(reference)
                if not subscription:
                    raise ServiceError('Solicitud anterior sin confirmar. Revisá Mercado Pago antes de reintentar; no se creará un cobro duplicado.')
            self._check_subscription(subscription, attempt)
            mp_id = resource_id(subscription['id'])
            state = subscription.get('status')
            url = checkout_url(subscription.get('init_point'), mp_id)
            db.execute('UPDATE subscriptions SET mp_id=?, state=?, checkout=? WHERE reference=?',
                       (mp_id, state, url, reference))
            if state != 'pending':
                # Commit observed state, then let the client refresh/manage instead of creating duplicates.
                result = {'checkout_url': None, 'monthly_amount': attempt['amount'], 'currency': 'ARS', 'mode': self.settings.mode}
            else:
                result = {'checkout_url': url, 'monthly_amount': attempt['amount'], 'currency': 'ARS', 'mode': self.settings.mode}
        return result

    def _check_subscription(self, sub, attempt):
        recurring = sub.get('auto_recurring', {})
        if (sub.get('external_reference') != attempt['reference']
                or str(sub.get('collector_id')) != self.settings.seller_id
                or (self.settings.mode == 'test' and sub.get('payer_id') and str(sub['payer_id']) != self.settings.buyer_id)
                or (self.settings.mode == 'production' and attempt['payer_id'] and sub.get('payer_id') and str(sub['payer_id']) != attempt['payer_id'])
                or recurring.get('currency_id') != 'ARS'
                or recurring.get('frequency') != 1 or recurring.get('frequency_type') != 'months'
                or Decimal(str(recurring.get('transaction_amount', -1))) != Decimal(attempt['amount'])
                or (attempt['mp_id'] and sub.get('id') != attempt['mp_id'])):
            raise ProviderError('La suscripción no corresponde a esta licencia, cuenta o importe.')

    def _reconcile(self, db, row):
        attempt = db.execute('SELECT * FROM subscriptions WHERE reference=?', (row['current_reference'],)).fetchone()
        if not attempt or not attempt['mp_id']:
            return row
        sub = self.provider.subscription(attempt['mp_id'])
        self._check_subscription(sub, attempt)
        now = self.clock()
        expires = None
        # An authorization alone is not proof of a paid period. Check actual payments.
        if sub.get('status') == 'authorized':
            if self.settings.mode == 'test':
                self.provider.test_accounts()
            invoices = self.provider.invoices(attempt['mp_id'])
            candidates = sorted(invoices, key=lambda item: item.get('debit_date', ''), reverse=True)
            for invoice in candidates:
                if invoice.get('preapproval_id') != attempt['mp_id']:
                    raise ProviderError('Factura ajena a la suscripción.')
                debit = parse_time(invoice['debit_date'])
                until = next_month(debit)
                if debit > now or until <= now:
                    continue
                payment_id = invoice.get('payment', {}).get('id')
                if not payment_id:
                    continue
                payment = self.provider.payment(payment_id)
                payment_payer_id = str(payment.get('payer', {}).get('id') or '')
                if self.settings.mode == 'test':
                    expected_payer_id = self.settings.buyer_id
                else:
                    expected_payer_id = str(attempt['payer_id'] or sub.get('payer_id') or '')
                    if not expected_payer_id:
                        raise ProviderError('Mercado Pago no devolvio la identidad del comprador.')
                    if not attempt['payer_id']:
                        db.execute('UPDATE subscriptions SET payer_id=? WHERE reference=?', (expected_payer_id, attempt['reference']))
                if (str(payment.get('collector_id')) != self.settings.seller_id
                        or payment_payer_id != expected_payer_id
                        or payment.get('currency_id') != 'ARS'
                        or Decimal(str(payment.get('transaction_amount', -1))) != Decimal(attempt['amount'])):
                    raise ProviderError('El pago no corresponde a las cuentas o importe.')
                if payment.get('status') == 'approved':
                    expires = max(expires, until) if expires else until
            end = sub.get('auto_recurring', {}).get('end_date')
            if expires and end:
                expires = min(expires, parse_time(end))
        active = expires is not None and expires > now and sub.get('status') == 'authorized'
        status = 'PRO_ACTIVE' if active else ('PRO_EXPIRED' if row['ever_active'] or sub.get('status') in ('cancelled', 'paused') else 'FREE')
        expires_text = expires.isoformat() if expires else row['expires_at']
        revision = row['revision'] + int(status != row['status'] or expires_text != row['expires_at'])
        db.execute('UPDATE subscriptions SET state=? WHERE reference=?', (sub.get('status', 'unknown'), attempt['reference']))
        db.execute('''UPDATE installations SET status=?, ever_active=?, expires_at=?,
            validated_at=?, revision=? WHERE installation_id=?''',
            (status, int(bool(row['ever_active']) or active), expires_text, now.isoformat(), revision, row['installation_id']))
        return db.execute('SELECT * FROM installations WHERE installation_id=?', (row['installation_id'],)).fetchone()

    def license(self, installation_id, token):
        with self.repository.transaction() as db:
            row = self.authenticate(db, installation_id, token)
            now = self.clock()
            if not row['validated_at'] or now - parse_time(row['validated_at']) >= timedelta(minutes=5):
                row = self._reconcile(db, row)
            status = row['status']
            paid_until = parse_time(row['expires_at']) if row['expires_at'] else None
            if status == 'PRO_ACTIVE' and (paid_until is None or paid_until <= now):
                row = self._reconcile(db, row)
                status = row['status']
                paid_until = parse_time(row['expires_at']) if row['expires_at'] else None
            validated = parse_time(row['validated_at']) if row['validated_at'] else now
            lease_end = validated + timedelta(days=3)
            if paid_until and status == 'PRO_ACTIVE':
                lease_end = min(lease_end, paid_until + timedelta(days=3))
            payload = {'v': 1, 'aud': 'stock-novarix', 'installation_id': installation_id,
                       'license_id': row['license_id'], 'status': status, 'revision': row['revision'],
                       'issued_at': int(now.timestamp()), 'validated_at': int(validated.timestamp()),
                       'valid_until': int(lease_end.timestamp()),
                       'paid_until': int(paid_until.timestamp()) if paid_until else None}
            encoded = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).decode()
            signature = base64.urlsafe_b64encode(self.key.sign(encoded.encode())).decode()
            return {'lease': encoded + '.' + signature}

    def webhook(self, topic, data_id, event_key):
        with self.repository.transaction() as db:
            if db.execute('SELECT 1 FROM webhook_events WHERE event_key=?', (event_key,)).fetchone():
                return {'ok': True, 'duplicate': True}
            if topic == 'subscription_preapproval':
                sub_id = data_id
            elif topic == 'subscription_authorized_payment':
                sub_id = self.provider.invoice(data_id).get('preapproval_id')
            elif topic == 'payment':
                # Resolve via the provider's invoice relationship, never client-supplied metadata.
                payment = self.provider.payment(data_id)
                result = self.provider.request('GET', '/authorized_payments/search', params={'payment_id': payment['id']})
                matches = result.get('results', [])
                if not matches:
                    raise ProviderError('El pago aún no tiene factura de suscripción disponible.')
                sub_id = matches[0].get('preapproval_id')
            else:
                return {'ok': True, 'ignored': True}
            attempt = db.execute('SELECT * FROM subscriptions WHERE mp_id=?', (sub_id,)).fetchone()
            if not attempt:
                # Recover a committed remote POST whose response was lost locally.
                sub = self.provider.subscription(resource_id(sub_id))
                attempt = db.execute('SELECT * FROM subscriptions WHERE reference=?', (sub.get('external_reference'),)).fetchone()
                if attempt:
                    self._check_subscription(sub, attempt)
                    db.execute('UPDATE subscriptions SET mp_id=? WHERE reference=?', (sub_id, attempt['reference']))
            if attempt:
                row = db.execute('SELECT * FROM installations WHERE installation_id=?', (attempt['installation_id'],)).fetchone()
                if row['current_reference'] == attempt['reference']:
                    self._reconcile(db, row)
            db.execute('INSERT INTO webhook_events VALUES (?, ?)', (event_key, self.clock().isoformat()))
            return {'ok': True, 'duplicate': False}
