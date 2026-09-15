import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import secrets
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from backend.config import Settings


class FakeMP:
    """Deterministic provider. Does not contact Mercado Pago or create any payment."""
    def __init__(self, clock):
        self.clock = clock
        self.sub = None
        self.creates = 0
        self.reads = 0
        self.approved = False
        self.payment_status = 'approved'
        self.debit = clock() - timedelta(hours=1)
        self.fail = False

    def test_accounts(self):
        return 'buyer@testuser.com'

    def create_subscription(self, reference, amount, payer_email=None):
        self.creates += 1
        self.sub = {'id': 'sub123', 'external_reference': reference, 'collector_id': 100,
                    'payer_id': 200, 'status': 'pending',
                    'init_point': 'https://www.mercadopago.com.ar/subscriptions/checkout?preapproval_id=sub123',
                    'auto_recurring': {'frequency': 1, 'frequency_type': 'months', 'currency_id': 'ARS',
                                       'transaction_amount': float(amount)}}
        return deepcopy(self.sub)

    def subscription(self, sub_id):
        from backend.mercado_pago import ProviderError
        self.reads += 1
        if self.fail:
            raise ProviderError('TEST failure')
        return deepcopy(self.sub)

    def find_subscription(self, reference):
        return deepcopy(self.sub)

    def invoices(self, sub_id):
        return [{'id': 300, 'preapproval_id': 'sub123', 'debit_date': self.debit.isoformat(),
                 'payment': {'id': 400}}] if self.approved else []

    def invoice(self, invoice_id):
        return {'preapproval_id': 'sub123'}

    def payment(self, payment_id):
        return {'id': 400, 'collector_id': 100, 'payer': {'id': 200},
                'currency_id': 'ARS', 'transaction_amount': 49999, 'status': self.payment_status,
                'live_mode': False}

    def request(self, *args, **kwargs):
        return {'results': [{'preapproval_id': 'sub123'}]}


def settings_for(path):
    key = Ed25519PrivateKey.generate()
    private = base64.b64encode(key.private_bytes(serialization.Encoding.Raw,
                              serialization.PrivateFormat.Raw, serialization.NoEncryption())).decode()
    public = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,
                             serialization.PublicFormat.Raw)).decode()
    settings = Settings(access_token=secrets.token_urlsafe(32), webhook_secret=secrets.token_urlsafe(32),
                        signing_key=private, monthly_ars=Decimal('49999.00'), seller_id='100', buyer_id='200',
                        back_url='https://example.test/return', webhook_url='https://example.test/webhooks/mercadopago',
                        database_path=path)
    return settings, public
