from __future__ import annotations

import tempfile
import unittest
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from novarix_stock.database import PlanLimitError, ProductLockedError, StockDatabase
from novarix_stock.models import AppPlan, ImportedProductDraft, ProductDraft


class LicenseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "license_test.db"
        self.database = StockDatabase(self.db_path)
        self.now = datetime.now(timezone.utc).replace(microsecond=0)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _draft(index: int) -> ProductDraft:
        return ProductDraft(
            sku=f"LIC-{index:03d}",
            name=f"Producto licencia {index}",
            purchase_price_cents=1_000,
            sale_price_cents=1_500,
            minimum_stock=1,
            initial_stock=2,
        )

    @staticmethod
    def _import_draft(index: int) -> ImportedProductDraft:
        return ImportedProductDraft(
            sku=f"LIC-IMP-{index:03d}",
            name=f"Importado licencia {index}",
            purchase_price_cents=1_000,
            sale_price_cents=1_500,
            stock_total=0,
            stock_current=0,
            sold=0,
            minimum_stock=0,
        )

    def test_free_license_is_created_with_empty_metadata(self) -> None:
        license_state = self.database.get_license_state()

        self.assertEqual(license_state.status, AppPlan.FREE)
        self.assertIsNone(license_state.license_id)
        self.assertIsNone(license_state.expires_at)
        self.assertIsNone(license_state.last_validated_at)
        self.assertEqual(self.database.get_plan(), AppPlan.FREE)

    def test_backend_update_persists_active_pro_metadata(self) -> None:
        expires_at = self.now + timedelta(days=30)
        validated_at = self.now

        saved = self.database.update_license(
            status=AppPlan.PRO_ACTIVE,
            license_id="SUB-NOVARIX-001",
            expires_at=expires_at,
            last_validated_at=validated_at,
        )
        for index in range(20):
            self.database.add_product(self._draft(index))
        reopened = StockDatabase(self.db_path)

        self.assertEqual(saved.status, AppPlan.PRO_ACTIVE)
        self.assertEqual(saved.license_id, "SUB-NOVARIX-001")
        self.assertEqual(saved.expires_at, expires_at)
        self.assertEqual(saved.last_validated_at, validated_at)
        self.assertEqual(reopened.get_license_state(), saved)
        self.assertEqual(reopened.get_plan(), AppPlan.PRO_ACTIVE)
        self.assertTrue(reopened.can_add_product())
        self.assertEqual(len(reopened.list_products()), 20)

    def test_expired_pro_preserves_products_and_non_creation_operations(self) -> None:
        self.database.update_license(
            status=AppPlan.PRO_ACTIVE,
            license_id="SUB-EXPIRED-001",
            expires_at=self.now + timedelta(days=1),
            last_validated_at=self.now,
        )
        products = [self.database.add_product(self._draft(index)) for index in range(17)]
        self.database.update_license(
            status=AppPlan.PRO_EXPIRED,
            license_id="SUB-EXPIRED-001",
            expires_at=self.now - timedelta(days=1),
            last_validated_at=self.now,
        )

        self.assertEqual(len(self.database.list_products()), 17)
        self.assertEqual(self.database.get_plan(), AppPlan.PRO_EXPIRED)
        self.assertFalse(self.database.can_add_product())
        with self.assertRaises(PlanLimitError):
            self.database.add_product(self._draft(17))
        with self.assertRaises(PlanLimitError):
            self.database.import_product(self._import_draft(17))

        first = products[0]
        self.database.update_product(first.id, self._draft(0))
        entered = self.database.register_entry(first.id, 1)
        sold = self.database.register_sale(first.id, 1)
        self.assertEqual(entered.stock_total, 3)
        self.assertEqual((sold.stock_current, sold.sold), (2, 1))

        self.database.delete_product(products[-1].id)
        self.assertFalse(self.database.can_add_product())
        self.database.delete_product(products[-2].id)
        self.assertFalse(self.database.can_add_product())
        self.database.delete_product(products[-3].id)
        for product in products[9:14]:
            self.database.delete_product(product.id)
        self.assertTrue(self.database.can_add_product())
        replacement = self.database.add_product(self._draft(17))
        self.assertEqual(replacement.sku, "LIC-017")
        self.assertEqual(len(self.database.list_products()), 10)

    def test_past_expiration_transitions_active_license_to_expired(self) -> None:
        validated_at = self.now - timedelta(hours=2)
        self.database.update_license(
            status=AppPlan.PRO_ACTIVE,
            license_id="SUB-AUTO-EXPIRE",
            expires_at=self.now - timedelta(seconds=1),
            last_validated_at=validated_at,
        )

        effective_plan = self.database.get_plan()
        persisted = StockDatabase(self.db_path).get_license_state()

        self.assertEqual(effective_plan, AppPlan.PRO_EXPIRED)
        self.assertEqual(persisted.status, AppPlan.PRO_EXPIRED)
        self.assertEqual(persisted.license_id, "SUB-AUTO-EXPIRE")
        self.assertEqual(persisted.last_validated_at, validated_at)
        self.assertTrue(self.database.can_add_product())

    def test_backend_can_renew_an_expired_license(self) -> None:
        self.database.update_license(
            status=AppPlan.PRO_ACTIVE,
            license_id="SUB-RENEW",
            expires_at=self.now + timedelta(days=1),
            last_validated_at=self.now,
        )
        for index in range(17):
            self.database.add_product(self._draft(index))
        self.database.update_license(
            status=AppPlan.PRO_EXPIRED,
            license_id="SUB-RENEW",
            expires_at=self.now - timedelta(days=1),
            last_validated_at=self.now,
        )
        self.assertFalse(self.database.can_add_product())

        renewed = self.database.update_license(
            status=AppPlan.PRO_ACTIVE,
            license_id="SUB-RENEW",
            expires_at=self.now + timedelta(days=30),
            last_validated_at=self.now + timedelta(minutes=1),
        )

        self.assertEqual(renewed.status, AppPlan.PRO_ACTIVE)
        self.assertTrue(self.database.can_add_product())
        self.database.add_product(self._draft(17))
        self.assertEqual(len(self.database.list_products()), 18)

    def test_expired_pro_locks_only_products_after_the_oldest_ten(self) -> None:
        self.database.update_license(
            status=AppPlan.PRO_ACTIVE,
            license_id="SUB-LOCK-ORDER",
            expires_at=self.now + timedelta(days=1),
            last_validated_at=self.now,
        )
        products = [self.database.add_product(self._draft(index)) for index in range(20)]

        self.database.update_license(
            status=AppPlan.PRO_EXPIRED,
            license_id="SUB-LOCK-ORDER",
            expires_at=self.now - timedelta(seconds=1),
            last_validated_at=self.now,
        )

        self.assertEqual(len(self.database.list_products()), 20)
        self.assertEqual(
            self.database.get_locked_product_ids(),
            {product.id for product in products[10:]},
        )

        first = products[0]
        self.database.update_product(first.id, self._draft(0))
        self.database.register_entry(first.id, 1)
        self.database.register_sale(first.id, 1)
        self.assertGreater(self.database.get_financial_summary().total_invoiced_cents, 0)

        eleventh = products[10]
        before = self.database.get_product(eleventh.id)
        with self.assertRaises(ProductLockedError):
            self.database.update_product(eleventh.id, self._draft(10))
        with self.assertRaises(ProductLockedError):
            self.database.register_entry(eleventh.id, 1)
        with self.assertRaises(ProductLockedError):
            self.database.register_sale(eleventh.id, 1)
        self.assertEqual(self.database.get_product(eleventh.id), before)
        self.assertEqual(self.database.list_sales(eleventh.id), [])

        self.database.delete_product(products[4].id)
        self.assertFalse(self.database.is_product_locked(eleventh.id))
        self.assertEqual(
            self.database.get_locked_product_ids(),
            {product.id for product in products[11:]},
        )
        promoted = self.database.register_entry(eleventh.id, 1)
        self.assertEqual(promoted.stock_total, 3)

        self.database.update_license(
            status=AppPlan.PRO_ACTIVE,
            license_id="SUB-LOCK-ORDER",
            expires_at=self.now + timedelta(days=30),
            last_validated_at=self.now + timedelta(minutes=1),
        )
        self.assertEqual(self.database.get_locked_product_ids(), set())
        self.database.register_entry(products[-1].id, 1)
        self.assertEqual(len(self.database.list_products()), 19)

    def test_legacy_pro_plan_migrates_to_active_license(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy_pro.db"
        with closing(sqlite3.connect(legacy_path)) as connection:
            with connection:
                connection.execute(
                    "CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO app_settings (key, value) VALUES ('plan', 'PRO')"
                )

        migrated = StockDatabase(legacy_path)

        self.assertEqual(migrated.get_plan(), AppPlan.PRO_ACTIVE)
        self.assertEqual(migrated.get_license_state().status, AppPlan.PRO_ACTIVE)
        self.assertTrue(migrated.can_add_product())


if __name__ == "__main__":
    unittest.main()
