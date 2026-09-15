import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QDialog
from novarix_stock.database import StockDatabase, StockError, ProductLockedError
from novarix_stock.models import ProductDraft, AppPlan
from novarix_stock.ui.main_window import MainWindow
from novarix_stock.ui.dialogs import StockAdjustmentDialog


class StockAdjustmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'adjustments.db'
        self.db = StockDatabase(self.path)
        self.draft = ProductDraft('GORRA-AJUSTE', 'GORRA AJUSTE', 100000, 150000, 20, initial_stock=100)
        self.product = self.db.add_product(self.draft)

    def tearDown(self):
        self.temp.cleanup()

    def test_down_then_up_audit_and_restock(self):
        financial = self.db.get_financial_summary()
        for target, restock in ((18, True), (25, False)):
            result = self.db.adjust_stock(self.product.id, target)
            self.assertEqual((result.stock_current, result.stock_total, result.sold), (target, target, 0))
            self.assertEqual(result.needs_restock, restock)
            self.assertEqual(self.db.get_financial_summary(), financial)
            self.assertEqual(self.db.list_sales(), [])
        rows = self.db.list_stock_adjustments(self.product.id)
        self.assertEqual([(r['stock_before'], r['stock_after'], r['difference']) for r in rows], [(100, 18, -82), (18, 25, 7)])
        for row in rows:
            self.assertEqual(row['product_sku'], self.product.sku)
            self.assertEqual(row['product_name'], self.product.name)
            self.assertTrue(row['adjusted_at'])

    def test_existing_sales_and_financial_history_unchanged(self):
        self.db.register_sale(self.product.id, 10)
        sales = self.db.list_sales()
        financial = self.db.get_financial_summary()
        for target in (18, 25, 0):
            result = self.db.adjust_stock(self.product.id, target)
            self.assertEqual((result.stock_current, result.stock_total, result.sold), (target, target + 10, 10))
            self.assertEqual(self.db.list_sales(), sales)
            self.assertEqual(self.db.get_financial_summary(), financial)
        self.db.register_entry(self.product.id, 5)
        result = self.db.register_sale(self.product.id, 2)
        self.assertEqual((result.stock_current, result.sold, result.stock_total), (3, 12, 15))

    def test_invalid_values_and_missing_product_do_not_write(self):
        for value in (-1, 1.5, '18', True, None, 2**63):
            with self.subTest(value=value), self.assertRaises(StockError):
                self.db.adjust_stock(self.product.id, value)
        with self.assertRaises(StockError):
            self.db.adjust_stock(999999, 18)
        self.assertEqual(self.db.get_product(self.product.id), self.product)
        self.assertEqual(self.db.list_stock_adjustments(), [])

    def test_audit_failure_rolls_back_stock(self):
        with self.db._connect() as conn:
            conn.execute("CREATE TRIGGER reject_adjustment BEFORE INSERT ON stock_adjustments BEGIN SELECT RAISE(ABORT, 'forced'); END")
        with self.assertRaises(StockError):
            self.db.adjust_stock(self.product.id, 18)
        self.assertEqual(self.db.get_product(self.product.id), self.product)
        self.assertEqual(self.db.list_stock_adjustments(), [])

    def test_history_survives_rename_deletion_and_reopening(self):
        self.db.adjust_stock(self.product.id, 18)
        rows = self.db.list_stock_adjustments()
        self.db.update_product(self.product.id, replace(self.draft, name='Nuevo nombre', sku='NUEVO'))
        self.db.delete_product(self.product.id)
        reopened = StockDatabase(self.path)
        self.assertEqual(reopened.list_stock_adjustments(self.product.id), rows)
        self.assertEqual(reopened.list_stock_adjustments(999), [])

    def test_pro_expired_block_and_renewal(self):
        self.db.set_plan(AppPlan.PRO_ACTIVE)
        others = [self.db.add_product(replace(self.draft, sku=f'PRO-{i}')) for i in range(10)]
        locked = others[-1]
        self.db.adjust_stock(locked.id, 25)
        self.db.set_plan(AppPlan.PRO_EXPIRED)
        rows = self.db.list_stock_adjustments()
        with self.assertRaises(ProductLockedError):
            self.db.adjust_stock(locked.id, 18)
        self.assertEqual(self.db.get_product(locked.id).stock_current, 25)
        self.assertEqual(self.db.list_stock_adjustments(), rows)
        self.db.adjust_stock(self.product.id, 18)
        self.db.set_plan(AppPlan.PRO_ACTIVE)
        self.assertEqual(self.db.adjust_stock(locked.id, 18).stock_current, 18)

    def test_ui_adjustment_refresh_selection_cancel_and_block(self):
        window = MainWindow(self.db)
        window.show()
        self.app.processEvents()
        try:
            with patch('novarix_stock.ui.main_window.QMessageBox.information') as info:
                window.adjust_stock_button.click()
                info.assert_called_once()
            window._refresh_table(self.product.id)
            with patch.object(StockAdjustmentDialog, 'exec', return_value=QDialog.DialogCode.Rejected):
                window.adjust_stock_button.click()
            self.assertEqual(self.db.list_stock_adjustments(), [])
            for target, restock in ((18, '1'), (25, '0')):
                def accept(dialog):
                    self.assertEqual(dialog.stock_input.minimum(), 0)
                    dialog.stock_input.setValue(target)
                    return QDialog.DialogCode.Accepted
                with patch.object(StockAdjustmentDialog, 'exec', accept):
                    window.adjust_stock_button.click()
                self.assertEqual(window.units_in_stock_value.text(), str(target))
                self.assertEqual(window.restock_products_value.text(), restock)
                self.assertEqual(window.units_sold_value.text(), '0')
                self.assertEqual(window.total_invoiced_value.text(), '$ 0,00')
                self.assertEqual(window.total_cost_value.text(), '$ 0,00')
                self.assertEqual(window.total_profit_value.text(), '$ 0,00')
                self.assertEqual(window.stock_value_value.text(), f'$ {target}.000,00')
                self.assertIn(f'Stock actual: {target}', window.product_summary_values.text())
                status = window.table.item(window.table.currentRow(), window.STATUS).text()
                self.assertIn('REPOSICIÓN' if target == 18 else 'OK', status)
            window.table.clearSelection()
            with patch('novarix_stock.ui.main_window.QMessageBox.information') as info:
                window.adjust_stock_button.click()
                info.assert_called_once()
            self.db.set_plan(AppPlan.PRO_ACTIVE)
            others = [self.db.add_product(replace(self.draft, sku=f'LOCK-{i}')) for i in range(10)]
            self.db.set_plan(AppPlan.PRO_EXPIRED)
            window._refresh_table(others[-1].id)
            self.assertFalse(window.adjust_stock_button.isEnabled())
            with patch('novarix_stock.ui.main_window.QMessageBox.information') as info:
                window._adjust_stock()
                info.assert_called_once()
        finally:
            window.close()
            window.deleteLater()
            self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
