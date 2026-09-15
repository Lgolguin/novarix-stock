import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QAbstractItemView
from dataclasses import replace

from novarix_stock.database import StockDatabase, StockError
from novarix_stock.models import AppPlan, ProductDraft
from novarix_stock.stock_movement_history import (
    MOVEMENT_FILTERS,
    query_stock_movements,
    summarize_movements,
)
from novarix_stock.ui.main_window import MainWindow


class StockMovementHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'movements.db'
        self.db = StockDatabase(self.path)
        self.draft = ProductDraft('MOV-001', 'Producto MOV-001', 100000, 150000, 0, initial_stock=50)
        self.product = self.db.add_product(self.draft)

    def tearDown(self):
        self.temp.cleanup()

    def _run_mov_001(self):
        self.db.register_entry(self.product.id, 20)
        sale = self.db.register_sale(self.product.id, 3)
        self.db.adjust_stock(self.product.id, 65)
        self.db.cancel_sale(sale.id, "error de carga")
        return sale.id

    def test_mov_001_sequence(self):
        sale_id = self._run_mov_001()
        movements = query_stock_movements(self.db)
        self.assertEqual(len(movements), 4)
        expected = [
            ("ANULACIÓN DE VENTA", 3, 65, 68, "Anulación de venta: error de carga"),
            ("AJUSTE", -2, 67, 65, "Ajuste de stock"),
            ("VENTA", 3, 70, 67, "Venta registrada"),
            ("ENTRADA", 20, 50, 70, "Entrada de mercadería"),
        ]
        for movement, (typ, qty, before, after, detail) in zip(movements, expected):
            self.assertEqual(movement.product_sku, self.product.sku)
            self.assertEqual(movement.product_name, self.product.name)
            self.assertEqual(movement.movement_type, typ)
            self.assertEqual(movement.quantity, qty)
            self.assertEqual(movement.stock_before, before)
            self.assertEqual(movement.stock_after, after)
            self.assertEqual(movement.detail, detail)
        self.assertTrue(all(m.id for m in movements))

    def test_filter_by_type(self):
        self._run_mov_001()
        expected_types = {
            "ENTRADAS": "ENTRADA",
            "VENTAS": "VENTA",
            "AJUSTES": "AJUSTE",
            "ANULACIONES": "ANULACIÓN DE VENTA",
        }
        for name, expected_count in (
            ("ENTRADAS", 1),
            ("VENTAS", 1),
            ("AJUSTES", 1),
            ("ANULACIONES", 1),
        ):
            rows = query_stock_movements(self.db, movement_type=name)
            self.assertEqual(len(rows), expected_count, name)
            self.assertTrue(
                all(r.movement_type == expected_types[name] for r in rows),
                name,
            )

    def test_search_by_sku_and_name(self):
        self._run_mov_001()
        self.assertEqual(len(query_stock_movements(self.db, search='MOV-001')), 4)
        self.assertEqual(len(query_stock_movements(self.db, search='producto mov')), 4)
        self.assertEqual(len(query_stock_movements(self.db, search='NOEXISTE')), 0)
        self.assertEqual(len(query_stock_movements(self.db, search="%' OR 1=1 --")), 0)

    def test_filter_by_product_id(self):
        self._run_mov_001()
        other = self.db.add_product(replace(self.draft, sku='OTRO-001', name='Otro'))
        self.db.register_entry(other.id, 5)
        self.assertEqual(len(query_stock_movements(self.db, product_id=self.product.id)), 4)
        self.assertEqual(len(query_stock_movements(self.db, product_id=other.id)), 1)
        self.assertEqual(len(query_stock_movements(self.db)), 5)

    def test_deleted_product_history_survives_reopen(self):
        self._run_mov_001()
        before = query_stock_movements(self.db, product_id=self.product.id)
        self.db.delete_product(self.product.id)
        self.assertEqual(query_stock_movements(StockDatabase(self.path), product_id=self.product.id), before)

    def test_all_license_states(self):
        self._run_mov_001()
        self.db.set_plan(AppPlan.PRO_ACTIVE)
        others = [self.db.add_product(replace(self.draft, sku=f'FILL-{i}')) for i in range(15)]
        locked = others[-1]
        self.db.register_sale(locked.id, 1)
        for plan in (AppPlan.FREE, AppPlan.PRO_ACTIVE, AppPlan.PRO_EXPIRED):
            self.db.set_plan(plan)
            self.assertEqual(len(query_stock_movements(self.db, product_id=self.product.id)), 4)
            self.assertEqual(len(query_stock_movements(self.db, product_id=locked.id)), 1)

    def test_invalid_filter_raises(self):
        with self.assertRaises(ValueError):
            query_stock_movements(self.db, movement_type='AYER')
        self.assertEqual(MOVEMENT_FILTERS, {"TODOS", "ENTRADAS", "VENTAS", "AJUSTES", "ANULACIONES"})

    def test_summarize_movements(self):
        self._run_mov_001()
        summary = summarize_movements(query_stock_movements(self.db))
        self.assertEqual(summary["total"], 4)
        self.assertEqual(summary["ENTRADA"], 20)
        self.assertEqual(summary["VENTA"], 3)
        self.assertEqual(summary["AJUSTE"], -2)
        self.assertEqual(summary["ANULACIÓN DE VENTA"], 3)

    def test_ui_open_filter_search_and_deleted_product(self):
        app = QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)
        self._run_mov_001()
        window = MainWindow(self.db)
        try:
            window.table.selectRow(0)
            app.processEvents()
            window.movement_history_button.click()
            app.processEvents()
            history = window.movement_history_window
            self.assertTrue(history.isVisible())
            self.assertEqual(history.table.rowCount(), 4)
            self.assertEqual(history.table.editTriggers(), QAbstractItemView.EditTrigger.NoEditTriggers)
            self.assertEqual(history.table.item(0, 4).text(), "ANULACIÓN DE VENTA")

            history.type_buttons["ENTRADAS"].click()
            app.processEvents()
            self.assertEqual(history.table.rowCount(), 1)
            self.assertEqual(history.table.item(0, 4).text(), "ENTRADA")

            history.type_buttons["TODOS"].click()
            history.search.setText('NOEXISTE')
            app.processEvents()
            self.assertEqual(history.table.rowCount(), 0)
            history.search.clear()
            app.processEvents()
            self.assertEqual(history.table.rowCount(), 4)

            history.table.sortItems(5, Qt.SortOrder.AscendingOrder)
            app.processEvents()
            self.assertEqual(history.table.item(0, 5).text(), "-2")

            self.db.delete_product(self.product.id)
            history.refresh_button.click()
            app.processEvents()
            self.assertEqual(history.table.rowCount(), 4)
            self.assertEqual(history.table.item(0, 2).text(), "MOV-001")
        finally:
            window.close()
            if window.movement_history_window:
                window.movement_history_window.close()
            app.processEvents()


if __name__ == '__main__':
    unittest.main()
