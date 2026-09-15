import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, date, timezone
from dataclasses import replace
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QAbstractItemView, QInputDialog
from unittest.mock import patch
from novarix_stock.database import StockDatabase
from novarix_stock.models import ProductDraft, AppPlan
from novarix_stock.sales_history import query_history, summarize_sales, local_sale_time, format_history_money
from novarix_stock.ui.main_window import MainWindow


class SalesHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'history.db'
        self.db = StockDatabase(self.path)
        self.draft = ProductDraft('HIST-TEST-001', 'Producto historial prueba', 100000, 150000, 0, initial_stock=20)
        self.product = self.db.add_product(self.draft)
        self.db.register_sale(self.product.id, 2)
        self.db.register_sale(self.product.id, 3)

    def tearDown(self):
        self.temp.cleanup()

    def test_newest_first_with_id_tiebreak(self):
        rows = query_history(self.db)
        self.assertEqual([s.quantity for s in rows], [3, 2])
        self.assertEqual(rows, query_history(StockDatabase(self.path)))

    def test_sku_case_insensitive_and_literal(self):
        self.assertEqual(len(query_history(self.db, 'hist-test-001')), 2)
        self.assertEqual(query_history(self.db, "%' OR 1=1 --"), [])

    def test_name_case_insensitive_and_clear(self):
        self.assertEqual(len(query_history(self.db, 'HISTORIAL PRUEBA')), 2)
        self.assertEqual(len(query_history(self.db, '  ')), 2)

    def set_dates(self, values):
        with self.db._connect() as conn:
            for sale, value in zip(self.db.list_sales(), values):
                stamp = value.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                conn.execute('UPDATE sales SET sold_at=? WHERE id=?', (stamp, sale.id))

    def test_today_local_midnight_boundaries(self):
        self.set_dates([datetime(2026, 9, 10, 0, 0), datetime(2026, 9, 9, 23, 59, 59)])
        rows = query_history(self.db, period='HOY', today=date(2026, 9, 10))
        self.assertEqual([s.quantity for s in rows], [2])

    def test_month_includes_first_day_excludes_previous_month(self):
        self.set_dates([datetime(2026, 9, 1), datetime(2026, 8, 31, 23, 59, 59)])
        self.assertEqual([s.quantity for s in query_history(self.db, period='ESTE MES', today=date(2026, 9, 10))], [2])

    def test_filtered_totals_and_empty(self):
        self.set_dates([datetime(2026, 9, 10, 12), datetime(2026, 8, 1, 12)])
        summary = summarize_sales(query_history(self.db, 'hist-test', 'HOY', today=date(2026, 9, 10)))
        self.assertEqual((summary.invoiced_cents, summary.cost_cents, summary.profit_cents, summary.units), (300000, 200000, 100000, 2))
        self.assertEqual(summarize_sales([]).units, 0)
        self.assertEqual(summarize_sales([]).invoiced_cents, 0)

    def test_historical_prices_sku_name_unchanged(self):
        before = query_history(self.db)
        self.db.update_product(self.product.id, replace(self.draft, sku='NEW', name='Nuevo', purchase_price_cents=170000, sale_price_cents=200000))
        self.assertEqual(query_history(self.db, 'HIST-TEST-001'), before)
        summary = summarize_sales(before)
        self.assertEqual((summary.invoiced_cents, summary.cost_cents, summary.profit_cents, summary.units), (750000, 500000, 250000, 5))

    def test_deleted_product_history_survives_reopen(self):
        before = query_history(self.db)
        self.db.delete_product(self.product.id)
        self.assertEqual(query_history(StockDatabase(self.path)), before)

    def test_all_license_states_and_locked_product(self):
        self.db.set_plan(AppPlan.PRO_ACTIVE)
        for i in range(15):
            self.db.add_product(replace(self.draft, sku=f'FILL-{i}'))
        locked = self.db.add_product(replace(self.draft, sku='LOCKED'))
        self.db.register_sale(locked.id, 1)
        for plan in (AppPlan.FREE, AppPlan.PRO_ACTIVE, AppPlan.PRO_EXPIRED):
            self.db.set_plan(plan)
            self.assertEqual(len(query_history(self.db, 'LOCKED')), 1)
        self.assertTrue(self.db.is_product_locked(locked.id))

    def test_migration_preserves_every_snapshot_and_sequence(self):
        before = query_history(self.db)
        with self.db._connect() as conn:
            conn.execute('ALTER TABLE sales RENAME TO snapshots')
            conn.execute('''CREATE TABLE sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL,
                product_sku TEXT NOT NULL, product_name TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK(quantity>0),
                purchase_price_cents INTEGER NOT NULL CHECK(purchase_price_cents>=0),
                sale_price_cents INTEGER NOT NULL CHECK(sale_price_cents>=0),
                sold_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                stock_before INTEGER, stock_after INTEGER,
                FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE)''')
            conn.execute('INSERT INTO sales SELECT * FROM snapshots')
            conn.execute('DROP TABLE snapshots')
            conn.execute("UPDATE sqlite_sequence SET seq=99 WHERE name='sales'")
        migrated = StockDatabase(self.path)
        self.assertEqual(query_history(migrated), before)
        migrated.register_sale(self.product.id, 1)
        self.assertEqual(max(s.id for s in query_history(migrated)), 100)
        migrated.delete_product(self.product.id)
        self.assertEqual(len(query_history(StockDatabase(self.path))), 3)
        with migrated._connect() as conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_read_only_queries(self):
        before = self.path.read_bytes()
        query_history(self.db, 'hist', 'HOY')
        summarize_sales(query_history(self.db))
        self.assertEqual(self.path.read_bytes(), before)

    def test_integer_currency_and_negative_profit(self):
        self.assertEqual(format_history_money(-123456), '$ -1.234,56')
        self.assertEqual(format_history_money(150000), '$ 1.500,00')

    def test_ui_open_sort_search_refresh_and_deleted_product(self):
        app = QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)
        window = MainWindow(self.db)
        try:
            window.history_button.click()
            app.processEvents()
            history = window.history_window
            self.assertTrue(history.isVisible())
            self.assertEqual(history.table.rowCount(), 2)
            self.assertEqual(history.table.item(0, 4).text(), '3')
            self.assertEqual(history.table.editTriggers(), QAbstractItemView.EditTrigger.NoEditTriggers)
            self.assertEqual([label.text() for label in history.summary_labels], ['$ 7.500,00', '$ 5.000,00', '$ 2.500,00', '5'])
            history.table.sortItems(6, Qt.SortOrder.AscendingOrder)
            self.assertEqual(history.table.item(0, 6).text(), '$ 3.000,00')
            history.search.setText('missing')
            self.assertEqual(history.table.rowCount(), 0)
            history.search.clear()
            self.assertEqual(history.table.rowCount(), 2)
            history.period_buttons['HOY'].click()
            self.assertEqual(history.table.rowCount(), 2)
            self.db.update_product(self.product.id, replace(self.draft, sale_price_cents=200000))
            history.refresh_button.click()
            self.assertEqual(history.table.item(0, 5).text(), '$ 1.500,00')
            self.db.register_sale(self.product.id, 1)
            history.refresh_button.click()
            self.assertEqual(history.table.rowCount(), 3)
            self.db.delete_product(self.product.id)
            history.close()
            window.history_button.click()
            self.assertIs(window.history_window, history)
            self.assertEqual(history.table.rowCount(), 3)
        finally:
            window.close()
            if window.history_window:
                window.history_window.close()
            app.processEvents()

    def test_cancel_sale_restores_stock_sold_and_financials(self):
        product = self.db.add_product(ProductDraft('ANULA-001', 'Producto anulable', 10000, 20000, 0, initial_stock=10))
        self.db.register_sale(product.id, 3)
        sale = self.db.list_sales(product.id)[0]

        self.db.cancel_sale(sale.id, 'Cliente devolvió mercadería')

        updated = self.db.get_product(product.id)
        self.assertEqual(updated.stock_current, 10)
        self.assertEqual(updated.sold, 0)
        history = query_history(self.db)
        self.assertEqual(len(history), 3)
        cancelled = next(s for s in history if s.id == sale.id)
        self.assertTrue(cancelled.is_cancelled)
        self.assertIn('ANULADA', ('ANULADA' if cancelled.is_cancelled else 'ACTIVA',))
        self.assertEqual(self.db.get_sales_summary(product.id).units_sold, 0)
        self.assertEqual(self.db.get_sales_summary(product.id).invoiced_cents, 0)
        financial = self.db.get_financial_summary()
        self.assertEqual(financial.total_invoiced_cents, 750000)
        self.assertEqual(financial.total_profit_cents, 250000)
        with self.db._connect() as conn:
            row = conn.execute('SELECT * FROM sale_cancellations WHERE sale_id = ?', (sale.id,)).fetchone()
        self.assertEqual(row['quantity'], 3)
        self.assertEqual(row['invoiced_cents'], 60000)
        self.assertEqual(row['cost_cents'], 30000)
        self.assertEqual(row['profit_cents'], 30000)
        self.assertEqual(row['reason'], 'Cliente devolvió mercadería')
        self.assertEqual(row['stock_returned'], 1)

    def test_cancel_sale_is_idempotent_and_atomic(self):
        product = self.db.add_product(ProductDraft('ANULA-002', 'Producto doble anulación', 5000, 10000, 0, initial_stock=5))
        self.db.register_sale(product.id, 2)
        sale = self.db.list_sales(product.id)[0]
        self.db.cancel_sale(sale.id)
        before = self.db.get_product(product.id)
        with self.assertRaises(Exception):
            self.db.cancel_sale(sale.id)
        after = self.db.get_product(product.id)
        self.assertEqual(before.stock_current, after.stock_current)
        self.assertEqual(before.sold, after.sold)
        with self.db._connect() as conn:
            count = conn.execute('SELECT COUNT(*) FROM sale_cancellations WHERE sale_id = ?', (sale.id,)).fetchone()[0]
        self.assertEqual(count, 1)

    def test_cancel_sale_for_deleted_product_reverts_financials_without_restoring_stock(self):
        product = self.db.add_product(ProductDraft('ANULA-DEL', 'Producto eliminado', 8000, 16000, 0, initial_stock=4))
        self.db.register_sale(product.id, 4)
        sale = self.db.list_sales(product.id)[0]
        self.db.delete_product(product.id)
        self.db.cancel_sale(sale.id, 'Producto ya no existe')
        self.assertIsNone(self.db.get_product(product.id))
        self.assertEqual(self.db.get_sales_summary(product.id).units_sold, 0)
        with self.db._connect() as conn:
            row = conn.execute('SELECT * FROM sale_cancellations WHERE sale_id = ?', (sale.id,)).fetchone()
        self.assertEqual(row['stock_returned'], 0)
        self.assertEqual(row['quantity'], 4)

    def test_ui_cancel_sale_button_marks_row_anulada_and_refreshes_metrics(self):
        app = QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)
        product = self.db.add_product(ProductDraft('ANULA-UI', 'Producto UI anulable', 10000, 20000, 0, initial_stock=10))
        self.db.register_sale(product.id, 3)
        window = MainWindow(self.db)
        try:
            window.history_button.click()
            app.processEvents()
            history = window.history_window
            self.assertTrue(history.isVisible())
            history.search.setText('ANULA-UI')
            app.processEvents()
            self.assertEqual(history.table.rowCount(), 1)
            history.table.selectRow(0)
            app.processEvents()
            self.assertEqual(history.table.item(0, 9).text(), 'ACTIVA')
            with patch.object(QInputDialog, 'getText', return_value=('Devolución UI', True)):
                QTest.mouseClick(history.cancel_sale_button, Qt.MouseButton.LeftButton)
                app.processEvents()
            self.assertEqual(history.table.item(0, 9).text(), 'ANULADA')
            self.assertEqual(window.units_sold_value.text(), '5')
            self.assertEqual(self.db.get_product(product.id).stock_current, 10)
        finally:
            window.close()
            if window.history_window:
                window.history_window.close()
            app.processEvents()
