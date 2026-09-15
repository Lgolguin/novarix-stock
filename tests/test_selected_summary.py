import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from novarix_stock.database import StockDatabase
from novarix_stock.models import ProductDraft, ImportedProductDraft
from novarix_stock.summary import SalesSummary
from novarix_stock.ui.main_window import MainWindow


class SelectedSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = StockDatabase(Path(self.temp.name) / 'stock.db')
        self.gorro_draft = ProductDraft('GORRO', 'GORRO', 100000, 150000, 0, initial_stock=100)
        self.gorro = self.db.add_product(self.gorro_draft)
        self.pelota = self.db.add_product(ProductDraft('PELOTA', 'PELOTA', 200000, 300000, 0, initial_stock=100))
        self.window = MainWindow(self.db)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def select(self, product):
        row = next(row for row in range(self.window.table.rowCount())
                   if self.window.table.item(row, self.window.PHOTO).data(Qt.ItemDataRole.UserRole) == product.id)
        self.window.table.selectRow(row)
        self.app.processEvents()

    def global_values(self):
        return tuple(label.text() for label in (self.window.units_sold_value,
            self.window.total_invoiced_value, self.window.total_cost_value, self.window.total_profit_value))

    def test_gorro_pelota_selection_and_deletion_keep_global_history(self):
        self.db.register_sale(self.gorro.id, 20)
        self.db.register_sale(self.pelota.id, 10)
        self.window._refresh_table()
        expected = ('30', '$ 60.000,00', '$ 40.000,00', '$ 20.000,00')
        self.assertEqual(self.global_values(), expected)
        for product, stock, sold in ((self.gorro, 80, 20), (self.pelota, 90, 10)):
            self.select(product)
            self.assertEqual(self.window.product_summary_identity.text(), f'{product.name}  •  SKU: {product.sku}')
            self.assertEqual(self.window.product_summary_values.text(),
                f'Stock actual: {stock}   •   Vendidos: {sold}   •   Facturado: $ 30.000,00   •   Costo vendido: $ 20.000,00   •   Ganancia: $ 10.000,00')
            self.assertEqual(self.global_values(), expected)
        before = self.db.list_sales()
        self.db.delete_product(self.gorro.id)
        self.window._refresh_table()
        self.assertIsNone(self.db.get_product(self.gorro.id))
        self.assertNotIn(self.gorro.id, self.window.products_by_id)
        self.assertEqual(self.global_values(), expected)
        self.assertEqual(self.window.units_in_stock_value.text(), '90')
        self.assertEqual(self.window.stock_value_value.text(), '$ 180.000,00')
        self.assertEqual(self.db.list_sales(), before)
        self.assertEqual(StockDatabase(self.db.database_path).get_sales_summary(), SalesSummary(30, 6000000, 4000000, 2000000))

    def test_no_selection_no_sales_and_clear_selection(self):
        self.assertEqual(self.window.product_summary_identity.text(), 'Seleccioná un producto para ver su resumen.')
        self.select(self.gorro)
        self.assertEqual(self.db.get_sales_summary(self.gorro.id), SalesSummary(0, 0, 0, 0))
        self.assertEqual(self.window.product_summary_values.text(),
            'Stock actual: 100   •   Vendidos: 0   •   Facturado: $ 0,00   •   Costo vendido: $ 0,00   •   Ganancia: $ 0,00')
        self.window.table.clearSelection()
        self.assertEqual(self.window.product_summary_values.text(), '')
        self.assertEqual(self.window.product_summary_identity.text(), 'Seleccioná un producto para ver su resumen.')

    def test_price_changes_do_not_reconstruct_history_and_refresh_updates_stock(self):
        self.db.register_sale(self.gorro.id, 20)
        self.db.update_product(self.gorro.id, replace(self.gorro_draft, purchase_price_cents=900000, sale_price_cents=1000000))
        self.db.register_entry(self.gorro.id, 5)
        self.window._refresh_table(self.gorro.id)
        self.assertEqual(self.db.get_sales_summary(self.gorro.id), SalesSummary(20, 3000000, 2000000, 1000000))
        self.assertIn('Stock actual: 85', self.window.product_summary_values.text())
        self.assertIn('Facturado: $ 30.000,00', self.window.product_summary_values.text())

    def test_imported_sold_counter_is_not_real_sale_history(self):
        product = self.db.import_product(ImportedProductDraft('IMP', 'Importado', 100, 200, 10, 4, 6, 0))
        self.window._refresh_table(product.id)
        self.assertEqual(self.window.units_sold_value.text(), '0')
        self.assertEqual(self.db.get_sales_summary(product.id), SalesSummary(0, 0, 0, 0))
        self.assertIn('Stock actual: 4', self.window.product_summary_values.text())
        self.assertIn('Vendidos: 0', self.window.product_summary_values.text())


if __name__ == '__main__':
    unittest.main()
