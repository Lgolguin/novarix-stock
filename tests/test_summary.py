from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from novarix_stock.database import StockDatabase
from novarix_stock.models import ProductDraft
from novarix_stock.summary import calculate_stock_summary


class SummaryTests(unittest.TestCase):
    def test_four_indicators_follow_stock_movements(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = StockDatabase(Path(temp_dir) / "summary.db")
            first = database.add_product(
                ProductDraft(
                    sku="SUM-1",
                    name="Primero",
                    purchase_price_cents=1000,
                    sale_price_cents=2000,
                    minimum_stock=2,
                    initial_stock=5,
                )
            )
            database.add_product(
                ProductDraft(
                    sku="SUM-2",
                    name="Segundo",
                    purchase_price_cents=2500,
                    sale_price_cents=4000,
                    minimum_stock=1,
                    initial_stock=1,
                )
            )
            database.register_entry(first.id, 3)
            database.register_sale(first.id, 6)

            summary = calculate_stock_summary(database.list_products())

            self.assertEqual(summary.units_in_stock, 3)
            self.assertEqual(summary.units_sold, 6)
            self.assertEqual(summary.products_to_restock, 2)
            self.assertEqual(summary.current_stock_value_cents, 4500)


if __name__ == "__main__":
    unittest.main()
