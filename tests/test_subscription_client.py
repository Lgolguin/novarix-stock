import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import httpx
from fastapi.testclient import TestClient
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QThreadPool
from PySide6.QtTest import QTest

from backend.app import create_app
from backend.tests.support import settings_for, FakeMP
from novarix_stock.licensing import InstallationStore, LicenseClient, LicensedStockDatabase, LicenseError, safe_checkout
from novarix_stock.database import PlanLimitError
from novarix_stock.models import ProductDraft, AppPlan
from novarix_stock.ui.main_window import MainWindow
from novarix_stock.ui.pro_controller import ProController


class SubscriptionClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.now = datetime.now(timezone.utc)
        self.settings, self.public_key = settings_for(root / 'backend.db')
        self.provider = FakeMP(lambda: self.now)
        self.api = TestClient(create_app(self.settings, self.provider, lambda: self.now))
        self.online = True
        def transport(request):
            if not self.online:
                raise httpx.ConnectError('offline')
            response = self.api.request(request.method, str(request.url), headers=dict(request.headers), content=request.content)
            return httpx.Response(response.status_code, json=response.json())
        self.client = LicenseClient(InstallationStore(root / 'installation.db'), 'https://backend.test',
            self.public_key, httpx.MockTransport(transport), lambda: self.now)
        self.db = LicensedStockDatabase(root / 'stock.db', self.client)

    def tearDown(self):
        self.api.close()
        self.temp.cleanup()

    def event(self):
        request_id, ts = str(uuid.uuid4()), str(int(time.time()))
        digest = hmac.new(self.settings.webhook_secret.encode(), f'id:sub123;request-id:{request_id};ts:{ts};'.encode(), hashlib.sha256).hexdigest()
        response = self.api.post('/webhooks/mercadopago?data.id=sub123',
            headers={'x-request-id': request_id, 'x-signature': f'ts={ts},v1={digest}'},
            json={'type': 'subscription_preapproval', 'data': {'id': 'sub123'}})
        self.assertEqual(response.status_code, 200)

    def activate(self):
        self.client.start('49999.00')
        self.provider.sub['status'] = 'authorized'
        self.provider.approved = True
        self.event()
        self.client.refresh()

    def add_products(self, count):
        return [self.db.add_product(ProductDraft(f'SKU-{n}', f'Producto {n}', 100, 200, 0, initial_stock=10)) for n in range(count)]

    def test_installation_uuid_and_credential_are_persistent(self):
        first = self.client.store.read()
        second = InstallationStore(self.client.store.path).read()
        self.assertEqual(first, second)
        self.assertEqual(str(uuid.UUID(first['installation_id'])), first['installation_id'])

    def test_free_starts_test_checkout_and_stays_free_before_payment(self):
        offer = self.client.offer()
        result = self.client.start(offer['monthly_amount'])
        self.assertTrue(result['checkout_url'].startswith('https://www.mercadopago.com.ar/'))
        self.assertEqual(self.client.refresh().status, AppPlan.FREE)

    def test_signed_activation_cancellation_renewal_reuses_stock_limits(self):
        self.activate()
        products = self.add_products(20)
        self.db.register_sale(products[19].id, 2)
        self.assertEqual(self.db.get_locked_product_ids(), set())
        self.provider.sub['status'] = 'cancelled'
        self.event()
        self.client.refresh()
        self.assertEqual(self.db.get_plan(), AppPlan.PRO_EXPIRED)
        self.assertEqual(self.db.get_locked_product_ids(), {p.id for p in products[10:]})
        self.assertEqual(len(self.db.list_sales()), 1)
        with self.assertRaises(PlanLimitError):
            self.db.add_product(ProductDraft('EXTRA', 'Extra', 1, 2, 0))
        self.provider.sub['status'] = 'authorized'
        self.event()
        self.client.refresh()
        self.assertEqual(self.db.get_plan(), AppPlan.PRO_ACTIVE)
        self.assertEqual(self.db.get_locked_product_ids(), set())

    def test_offline_preserves_valid_pro_but_not_forever(self):
        self.activate()
        self.online = False
        self.now += timedelta(days=2)
        with self.assertRaises(LicenseError):
            self.client.refresh()
        self.assertEqual(self.db.get_plan(), AppPlan.PRO_ACTIVE)
        self.now += timedelta(days=2)
        self.assertEqual(self.db.get_plan(), AppPlan.PRO_EXPIRED)

    def test_last_local_validation_edit_cannot_extend_signed_lease(self):
        self.activate()
        self.now += timedelta(days=4)
        self.db.update_license(status=AppPlan.PRO_ACTIVE, license_id='local',
            expires_at=self.now + timedelta(days=100), last_validated_at=self.now)
        self.assertEqual(self.db.get_plan(), AppPlan.PRO_EXPIRED)

    def test_unsigned_local_status_cannot_grant_pro(self):
        self.db.set_plan(AppPlan.PRO_ACTIVE)
        with self.db._connect() as db:
            db.execute("UPDATE license_state SET status='PRO_ACTIVE', expires_at=NULL")
        self.assertEqual(self.db.get_plan(), AppPlan.FREE)
        self.add_products(10)
        with self.assertRaises(PlanLimitError):
            self.db.add_product(ProductDraft('EXTRA', 'Extra', 1, 2, 0))

    def test_modified_signature_or_payload_rejected(self):
        self.activate()
        token = self.client.store.read()['lease']
        encoded, signature = token.split('.')
        payload = json.loads(base64.urlsafe_b64decode(encoded))
        payload['valid_until'] += 999999
        changed = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        self.client.store.save(changed + '.' + signature)
        self.assertEqual(self.db.get_plan(), AppPlan.PRO_EXPIRED)

    def test_signed_lease_cannot_move_to_another_installation(self):
        self.activate()
        other = LicenseClient(InstallationStore(Path(self.temp.name) / 'other.db'), public_key=self.public_key)
        with self.assertRaises(LicenseError):
            other.verify(self.client.store.read()['lease'])

    def test_untrusted_public_key_and_non_https_backend_rejected(self):
        self.activate()
        self.client.public_key = base64.b64encode(b'0' * 32).decode()
        self.assertEqual(self.db.get_plan(), AppPlan.PRO_EXPIRED)
        with self.assertRaises(LicenseError):
            LicenseClient(self.client.store, 'http://remote.example', self.public_key)

    def test_checkout_url_allowlist(self):
        for url in ('https://www.mercadopago.com.ar.evil.test/subscriptions/checkout?preapproval_id=x',
                    'http://www.mercadopago.com.ar/subscriptions/checkout?preapproval_id=x',
                    'file:///secret', 'https://attacker@www.mercadopago.com.ar/subscriptions/checkout?preapproval_id=x'):
            with self.assertRaises(LicenseError):
                safe_checkout(url)

    def test_older_signed_response_cannot_override_cancellation(self):
        self.activate()
        old_token = self.client.store.read()['lease']
        self.provider.sub['status'] = 'cancelled'
        self.event()
        self.client.refresh()
        with patch.object(self.client, '_request', return_value={'lease': old_token}):
            with self.assertRaises(LicenseError):
                self.client.refresh()
        self.assertEqual(self.db.get_plan(), AppPlan.PRO_EXPIRED)

    def test_invalid_backend_config_keeps_signed_offline_access(self):
        self.activate()
        with patch.dict(os.environ, {'STOCK_LICENSE_API_URL': 'http://not-local.example',
                                    'STOCK_LICENSE_PUBLIC_KEY': self.public_key}):
            client = LicenseClient.from_env(self.client.store.path)
        self.assertEqual(client.effective_state().status, AppPlan.PRO_ACTIVE)
        with self.assertRaises(LicenseError):
            client.refresh()

    def test_client_files_and_cache_have_no_provider_secrets(self):
        self.activate()
        contents = self.client.store.path.read_bytes()
        for secret in (self.settings.access_token, self.settings.webhook_secret, self.settings.signing_key):
            self.assertNotIn(secret.encode(), contents)
        root = Path(__file__).parents[1] / 'novarix_stock'
        for source in root.rglob('*.py'):
            text = source.read_text(encoding='utf-8-sig')
            self.assertNotIn('MERCADOPAGO_ACCESS_TOKEN', text)
            self.assertNotIn('MERCADOPAGO_WEBHOOK_SECRET', text)
            self.assertNotIn('STOCK_LICENSE_PRIVATE_KEY', text)

    def test_ui_checkout_confirmation_and_signed_plan_refresh(self):
        app = QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)
        window = MainWindow(self.db)
        controller = ProController(window, self.client)
        def drain():
            for _ in range(500):
                app.processEvents()
                if not controller.busy:
                    return
                time.sleep(.01)
            self.fail('License task did not finish')
        try:
            window.show()
            app.processEvents()
            drain()
            self.assertIn('STOCK PRO — $49.999 ARS / mes', window.statusBar().currentMessage())
            with patch('novarix_stock.ui.pro_controller.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes) as question, patch('novarix_stock.ui.pro_controller.QDesktopServices.openUrl', return_value=True) as browser:
                window.pro_button.click()
                drain()
                self.assertIn('49.999,00', question.call_args.args[2])
                self.assertIn('ARS', question.call_args.args[2])
                self.assertEqual(question.call_args.args[1], 'STOCK PRO — $49.999 ARS / mes')
                browser.assert_called_once()
            self.provider.sub['status'] = 'authorized'
            self.provider.approved = True
            self.event()
            controller.refresh()
            drain()
            self.assertEqual(window.plan_label.text(), 'PLAN PRO')
            self.assertIn('Suscripción activa', window.statusBar().currentMessage())
            self.assertTrue(controller.manage_button.isVisible())
        finally:
            controller.timer.stop()
            QThreadPool.globalInstance().waitForDone(3000)
            window.close()
            window.deleteLater()
            app.processEvents()
