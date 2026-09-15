from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import os
from unittest.mock import patch
import hmac
import secrets
import tempfile
import time
import unittest
import uuid
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings
from backend.mercado_pago import MercadoPago, ProviderError
from backend.tests.support import FakeMP, settings_for


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = datetime.now(timezone.utc)
        self.signature_ts = str(int(time.time()))
        self.settings, self.public_key = settings_for(Path(self.temp.name) / 'backend.db')
        self.mp = FakeMP(lambda: self.now)
        self.app = create_app(self.settings, self.mp, lambda: self.now)
        self.client = TestClient(self.app)
        self.installation_id = str(uuid.uuid4())
        self.headers = {'Authorization': 'Bearer ' + secrets.token_urlsafe(32)}
        self.client.post('/installations/register', json={'installation_id': self.installation_id}, headers=self.headers)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def start(self, amount='49999.00'):
        return self.client.post('/subscriptions/start', json={'installation_id': self.installation_id,
                                 'expected_amount': amount}, headers=self.headers)

    def webhook(self, topic='subscription_preapproval', data_id='sub123', request_id=None, valid=True):
        ts = self.signature_ts
        request_id = request_id or str(uuid.uuid4())
        digest = hmac.new(self.settings.webhook_secret.encode(),
                         f'id:{data_id.lower()};request-id:{request_id};ts:{ts};'.encode(), hashlib.sha256).hexdigest()
        return self.client.post('/webhooks/mercadopago', params={'data.id': data_id},
            headers={'x-request-id': request_id, 'x-signature': f'ts={ts},v1={digest if valid else "0"*64}'},
            json={'type': topic, 'data': {'id': data_id}, 'status': 'approved'})

    def state(self):
        with self.app.state.service.repository.transaction() as db:
            return dict(db.execute('SELECT * FROM installations WHERE installation_id=?', (self.installation_id,)).fetchone())

    def activate(self):
        self.start()
        self.mp.sub['status'] = 'authorized'
        self.mp.approved = True
        self.assertEqual(self.webhook().status_code, 200)

    def test_free_checkout_ars_and_repeated_start_no_duplicate(self):
        plan = self.client.get('/plans/pro')
        self.assertEqual(plan.status_code, 200)
        self.assertEqual(plan.json()['monthly_amount'], '49999.00')
        self.assertEqual(plan.json()['currency'], 'ARS')
        response = self.start()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['monthly_amount'], '49999.00')
        self.assertTrue(response.json()['checkout_url'].startswith('https://www.mercadopago.com.ar/'))
        self.start()
        self.assertEqual(self.mp.creates, 1)
        self.assertEqual(self.state()['status'], 'FREE')

    def test_client_amount_and_extra_fields_cannot_set_price_or_status(self):
        self.assertEqual(self.start('1').status_code, 409)
        response = self.client.post('/subscriptions/start', headers=self.headers, json={
            'installation_id': self.installation_id, 'expected_amount': '49999', 'status': 'PRO_ACTIVE'})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.mp.creates, 0)

    def test_installation_authentication_and_uuid_persistence(self):
        wrong = {'Authorization': 'Bearer ' + secrets.token_urlsafe(32)}
        response = self.client.get('/licenses/' + self.installation_id, headers=wrong)
        self.assertEqual(response.status_code, 401)
        response = self.client.post('/installations/register', json={'installation_id': self.installation_id}, headers=wrong)
        self.assertEqual(response.status_code, 401)
        before = self.state()['license_id']
        self.client.post('/installations/register', json={'installation_id': self.installation_id}, headers=self.headers)
        self.assertEqual(before, self.state()['license_id'])

    def test_secrets_never_in_responses(self):
        responses = [self.start(), self.client.get('/plans/pro'), self.client.get('/licenses/' + self.installation_id, headers=self.headers)]
        for response in responses:
            for secret in (self.settings.access_token, self.settings.webhook_secret, self.settings.signing_key, self.headers['Authorization'][7:]):
                self.assertNotIn(secret, response.text)

    def test_authorization_without_payment_does_not_activate(self):
        self.start()
        self.mp.sub['status'] = 'authorized'
        self.webhook()
        self.assertEqual(self.state()['status'], 'FREE')

    def test_activation_cancel_and_renewal(self):
        self.activate()
        self.assertEqual(self.state()['status'], 'PRO_ACTIVE')
        self.mp.sub['status'] = 'cancelled'
        self.webhook()
        self.assertEqual(self.state()['status'], 'PRO_EXPIRED')
        self.mp.sub['status'] = 'authorized'
        self.webhook()
        self.assertEqual(self.state()['status'], 'PRO_ACTIVE')

    def test_payment_expired_or_rejected_revokes(self):
        self.activate()
        self.mp.debit = self.now - timedelta(days=40)
        self.webhook()
        self.assertEqual(self.state()['status'], 'PRO_EXPIRED')
        self.mp.debit = self.now - timedelta(hours=1)
        self.mp.payment_status = 'rejected'
        self.webhook()
        self.assertEqual(self.state()['status'], 'PRO_EXPIRED')

    def test_invalid_unsigned_or_body_only_webhook_rejected(self):
        self.start()
        reads = self.mp.reads
        self.assertEqual(self.webhook(valid=False).status_code, 401)
        response = self.client.post('/webhooks/mercadopago', json={'data': {'id': 'sub123'}, 'status': 'approved'})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.mp.reads, reads)

    def test_valid_repeated_webhook_is_idempotent(self):
        self.activate()
        self.webhook(request_id='same-request')
        before = self.state()
        reads = self.mp.reads
        result = self.webhook(request_id='same-request')
        self.assertTrue(result.json()['duplicate'])
        self.assertEqual(self.state(), before)
        self.assertEqual(self.mp.reads, reads)

    def test_failed_webhook_is_retryable_and_keeps_previous_license(self):
        self.activate()
        before = self.state()
        self.mp.fail = True
        self.assertEqual(self.webhook(request_id='retry').status_code, 503)
        self.assertEqual(self.state(), before)
        self.mp.fail = False
        self.assertEqual(self.webhook(request_id='retry').status_code, 200)

    def test_old_notification_rechecks_current_provider_state(self):
        self.activate()
        self.mp.sub['status'] = 'cancelled'
        self.webhook()
        self.webhook()  # An "approved" body cannot resurrect it.
        self.assertEqual(self.state()['status'], 'PRO_EXPIRED')

    def test_invoice_and_payment_webhook_topics(self):
        self.start()
        self.mp.sub['status'] = 'authorized'
        self.mp.approved = True
        self.assertEqual(self.webhook('subscription_authorized_payment', '300').status_code, 200)
        self.assertEqual(self.webhook('payment', '400').status_code, 200)
        self.assertEqual(self.state()['status'], 'PRO_ACTIVE')

    def test_provider_mismatch_does_not_activate(self):
        self.start()
        self.mp.sub['status'] = 'authorized'
        self.mp.approved = True
        self.mp.sub['external_reference'] = 'another-license'
        self.assertEqual(self.webhook().status_code, 503)
        self.assertEqual(self.state()['status'], 'FREE')

    def test_get_reconciles_missing_webhook_and_provider_failure_does_not_extend_lease(self):
        self.activate()
        previous = self.state()['validated_at']
        self.now += timedelta(minutes=6)
        self.mp.fail = True
        response = self.client.get('/licenses/' + self.installation_id, headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.state()['validated_at'], previous)
        self.mp.fail = False
        self.mp.sub['status'] = 'paused'
        self.client.get('/licenses/' + self.installation_id, headers=self.headers)
        self.assertEqual(self.state()['status'], 'PRO_EXPIRED')

    def test_pending_creation_timeout_reconciles_without_second_post(self):
        original = self.mp.create_subscription
        def lost_response(*args):
            original(*args)
            raise ProviderError('Connection lost')
        self.mp.create_subscription = lost_response
        self.assertEqual(self.start().status_code, 503)
        self.assertEqual(self.start().status_code, 200)
        self.assertEqual(self.mp.creates, 1)

    def test_production_mode_allowed_and_invalid_amount_rejected(self):
        create_app(replace(self.settings, mode='production'))
        with self.assertRaises(ValueError):
            create_app(replace(self.settings, monthly_ars=Decimal('NaN')))

    def test_official_price_default_and_environment_override(self):
        env = {
            'MERCADOPAGO_ACCESS_TOKEN': self.settings.access_token,
            'MERCADOPAGO_WEBHOOK_SECRET': self.settings.webhook_secret,
            'STOCK_LICENSE_PRIVATE_KEY': self.settings.signing_key,
            'MERCADOPAGO_TEST_SELLER_ID': self.settings.seller_id,
            'MERCADOPAGO_TEST_BUYER_ID': self.settings.buyer_id,
            'STOCK_BACK_URL': self.settings.back_url,
            'STOCK_WEBHOOK_URL': self.settings.webhook_url,
            'STOCK_BACKEND_DB': str(self.settings.database_path),
        }
        with patch.dict(os.environ, env, clear=True), patch('dotenv.load_dotenv'):
            self.assertEqual(Settings.from_env().monthly_ars, Decimal('49999.00'))
            os.environ['STOCK_PRO_MONTHLY_ARS'] = '52000.00'
            self.assertEqual(Settings.from_env().monthly_ars, Decimal('52000.00'))

    def test_adapter_blocks_real_accounts_before_post(self):
        calls = []
        def handle(request):
            calls.append(request.method)
            return httpx.Response(200, json={'id': 100, 'tags': [], 'site_id': 'MLA'})
        adapter = MercadoPago(self.settings, httpx.MockTransport(handle))
        with self.assertRaises(ProviderError):
            adapter.create_subscription('ref', '49999')
        self.assertNotIn('POST', calls)

    def test_adapter_builds_monthly_test_request(self):
        import json
        sent = []
        def handle(request):
            if request.method == 'POST':
                sent.append(json.loads(request.content))
                return httpx.Response(201, json={'id': 'sub'})
            buyer = request.url.path.endswith('/200')
            return httpx.Response(200, json={'id': 200 if buyer else 100, 'tags': ['test_user'],
                'site_id': 'MLA', 'email': 'buyer@testuser.com'})
        MercadoPago(self.settings, httpx.MockTransport(handle)).create_subscription('server-ref', '49999.00')
        self.assertEqual(sent[0]['auto_recurring'], {'frequency': 1, 'frequency_type': 'months', 'transaction_amount': 49999.0, 'currency_id': 'ARS'})
        self.assertEqual(sent[0]['status'], 'pending')
        self.assertEqual(sent[0]['external_reference'], 'server-ref')
        self.assertEqual(sent[0]['notification_url'], self.settings.webhook_url)

    def test_signed_query_cannot_be_replaced_by_body_id(self):
        self.start()
        ts, request_id = self.signature_ts, 'body-mismatch'
        digest = hmac.new(self.settings.webhook_secret.encode(),
            f'id:sub123;request-id:{request_id};ts:{ts};'.encode(), hashlib.sha256).hexdigest()
        response = self.client.post('/webhooks/mercadopago?data.id=sub123',
            headers={'x-signature': f'ts={ts},v1={digest}', 'x-request-id': request_id},
            json={'type': 'subscription_preapproval', 'data': {'id': 'other'}})
        self.assertEqual(response.status_code, 400)

    def test_expired_signature_and_duplicate_query_rejected(self):
        self.start()
        self.signature_ts = str(int(time.time()) - 600)
        self.assertEqual(self.webhook().status_code, 401)
        response = self.client.post('/webhooks/mercadopago?data.id=sub123&data.id=other', json={})
        self.assertEqual(response.status_code, 401)

    def test_fresh_get_after_expiry_does_not_renew_unpaid_pro(self):
        self.activate()
        self.now += timedelta(days=40)
        response = self.client.get('/licenses/' + self.installation_id, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.state()['status'], 'PRO_EXPIRED')
