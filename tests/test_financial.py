from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from novarix_stock.database import StockDatabase, StockError
from novarix_stock.models import ProductDraft


class FinancialEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "financial_test.db"
        self.database = StockDatabase(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def _draft(sale_price_cents: int = 150_000) -> ProductDraft:
        return ProductDraft(
            sku="FIN-001",
            name="Producto financiero",
            purchase_price_cents=100_000,
            sale_price_cents=sale_price_cents,
            minimum_stock=1,
            initial_stock=10,
        )

    def test_sale_snapshots_prices_and_calculates_profit(self) -> None:
        product = self.database.add_product(self._draft())

        sold = self.database.register_sale(product.id, 4)

        self.assertEqual((sold.stock_current, sold.sold), (6, 4))
        sale = self.database.list_sales(product.id)[0]
        self.assertEqual(sale.product_id, product.id)
        self.assertEqual(sale.product_sku, "FIN-001")
        self.assertEqual(sale.product_name, "Producto financiero")
        self.assertEqual(sale.quantity, 4)
        self.assertEqual(sale.purchase_price_cents, 100_000)
        self.assertEqual(sale.sale_price_cents, 150_000)
        self.assertTrue(sale.sold_at)
        self.assertEqual(sale.invoiced_cents, 600_000)
        self.assertEqual(sale.cost_cents, 400_000)
        self.assertEqual(sale.profit_cents, 200_000)

        self.database.update_product(product.id, self._draft(sale_price_cents=200_000))

        historical_sale = self.database.list_sales(product.id)[0]
        self.assertEqual(historical_sale.sale_price_cents, 150_000)
        self.assertEqual(historical_sale.invoiced_cents, 600_000)
        summary = self.database.get_financial_summary()
        self.assertEqual(summary.total_invoiced_cents, 600_000)
        self.assertEqual(summary.total_cost_cents, 400_000)
        self.assertEqual(summary.total_profit_cents, 200_000)
        self.assertEqual(summary.today_invoiced_cents, 600_000)
        self.assertEqual(summary.today_profit_cents, 200_000)
        self.assertEqual(summary.month_invoiced_cents, 600_000)
        self.assertEqual(summary.month_profit_cents, 200_000)

        reopened = StockDatabase(self.db_path)
        self.assertEqual(reopened.list_sales(product.id), [historical_sale])
        self.assertEqual(reopened.get_financial_summary(), summary)

    def test_today_and_month_totals_exclude_old_sales(self) -> None:
        product = self.database.add_product(self._draft())
        self.database.register_sale(product.id, 2)
        self.database.register_sale(product.id, 1)
        old_sale_id = self.database.list_sales(product.id)[-1].id
        with closing(sqlite3.connect(self.db_path)) as connection:
            with connection:
                connection.execute(
                    "UPDATE sales SET sold_at = '2000-01-15 12:00:00' WHERE id = ?",
                    (old_sale_id,),
                )

        summary = self.database.get_financial_summary()

        self.assertEqual(summary.total_invoiced_cents, 450_000)
        self.assertEqual(summary.total_cost_cents, 300_000)
        self.assertEqual(summary.total_profit_cents, 150_000)
        self.assertEqual(summary.today_invoiced_cents, 300_000)
        self.assertEqual(summary.today_profit_cents, 100_000)
        self.assertEqual(summary.month_invoiced_cents, 300_000)
        self.assertEqual(summary.month_profit_cents, 100_000)

    def test_sale_insert_failure_rolls_back_stock_update(self) -> None:
        product = self.database.add_product(self._draft())
        with closing(sqlite3.connect(self.db_path)) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TRIGGER force_sale_failure
                    BEFORE INSERT ON sales
                    BEGIN
                        SELECT RAISE(ABORT, 'forced sale failure');
                    END
                    """
                )

        with self.assertRaisesRegex(StockError, "No se pudo registrar la venta"):
            self.database.register_sale(product.id, 4)

        unchanged = self.database.get_product(product.id)
        self.assertEqual((unchanged.stock_current, unchanged.sold), (10, 0))  # type: ignore[union-attr]
        self.assertEqual(self.database.list_sales(product.id), [])

    def test_deleting_product_preserves_historical_sales(self) -> None:
        product = self.database.add_product(self._draft())
        self.database.register_sale(product.id, 1)

        self.database.delete_product(product.id)

        self.assertEqual(len(self.database.list_sales()), 1)
        self.assertEqual(self.database.get_financial_summary().total_invoiced_cents, 150_000)


if __name__ == "__main__":
    unittest.main()
