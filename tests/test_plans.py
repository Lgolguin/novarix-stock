from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from novarix_stock.database import (
    FREE_PRODUCT_LIMIT,
    FREE_PRODUCT_LIMIT_MESSAGE,
    PlanLimitError,
    StockDatabase,
)
from novarix_stock.models import AppPlan, ProductDraft


class PlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "plans_test.db"
        self.database = StockDatabase(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _draft(index: int) -> ProductDraft:
        return ProductDraft(
            sku=f"PLAN-{index:03d}",
            name=f"Producto {index}",
            purchase_price_cents=1_000,
            sale_price_cents=1_500,
            minimum_stock=1,
            initial_stock=2,
        )

    def test_free_allows_ten_blocks_eleventh_and_reopens_a_slot(self) -> None:
        self.assertEqual(self.database.get_plan(), AppPlan.FREE)
        products = [
            self.database.add_product(self._draft(index))
            for index in range(FREE_PRODUCT_LIMIT)
        ]
        self.assertEqual(len(products), 10)
        self.assertFalse(self.database.can_add_product())

        with self.assertRaisesRegex(PlanLimitError, "Alcanzaste el límite de 10") as error:
            self.database.add_product(self._draft(10))
        self.assertEqual(str(error.exception), FREE_PRODUCT_LIMIT_MESSAGE)
        self.assertEqual(len(self.database.list_products()), 10)

        first = products[0]
        self.database.update_product(first.id, self._draft(0))
        entered = self.database.register_entry(first.id, 1)
        sold = self.database.register_sale(first.id, 1)
        self.assertEqual(entered.stock_total, 3)
        self.assertEqual((sold.stock_current, sold.sold), (2, 1))

        self.database.delete_product(products[-1].id)
        self.assertTrue(self.database.can_add_product())
        replacement = self.database.add_product(self._draft(10))
        self.assertEqual(replacement.sku, "PLAN-010")
        self.assertEqual(len(self.database.list_products()), 10)

    def test_pro_is_persisted_and_allows_unlimited_products(self) -> None:
        self.database.set_plan(AppPlan.PRO)
        for index in range(20):
            self.database.add_product(self._draft(index))

        reopened = StockDatabase(self.db_path)

        self.assertEqual(reopened.get_plan(), AppPlan.PRO)
        self.assertTrue(reopened.can_add_product())
        self.assertEqual(len(reopened.list_products()), 20)

    def test_existing_products_above_free_limit_are_preserved(self) -> None:
        self.database.set_plan(AppPlan.PRO)
        for index in range(17):
            self.database.add_product(self._draft(index))
        self.database.set_plan(AppPlan.FREE)

        with self.assertRaises(PlanLimitError):
            self.database.add_product(self._draft(17))

        self.assertEqual(len(self.database.list_products()), 17)


if __name__ == "__main__":
    unittest.main()
