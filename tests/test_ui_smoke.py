from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from novarix_stock.database import StockDatabase  # noqa: E402
from novarix_stock.models import AppPlan, ProductDraft  # noqa: E402
from novarix_stock.ui.dialogs import ProductDialog  # noqa: E402
from novarix_stock.ui.main_window import MainWindow  # noqa: E402


class UiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def test_main_table_and_restock_alert_render(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = StockDatabase(Path(temp_dir) / "ui.db")
            product = database.add_product(
                ProductDraft(
                    sku="UI-001",
                    name="Producto visual",
                    purchase_price_cents=10000,
                    sale_price_cents=15000,
                    minimum_stock=2,
                    initial_stock=2,
                )
            )
            window = MainWindow(database)
            window.show()
            self.app.processEvents()

            self.assertEqual(window.table.columnCount(), 10)
            self.assertEqual(window.table.rowCount(), 1)
            self.assertEqual(window.table.item(0, window.NAME).text(), product.name)
            self.assertIn("REPOSICIÓN", window.table.item(0, window.STATUS).text())
            self.assertIn("1 para reponer", window.counter_label.text())
            self.assertEqual(window.units_in_stock_value.text(), "2")
            self.assertEqual(window.units_sold_value.text(), "0")
            self.assertEqual(window.restock_products_value.text(), "1")
            self.assertEqual(window.stock_value_value.text(), "$ 200,00")
            self.assertEqual(window.import_button.text(), "Importar Excel")
            self.assertEqual(window.export_button.text(), "Exportar Excel")
            self.assertEqual(window.plan_label.text(), "PLAN FREE")
            self.assertEqual(window.pro_button.text(), "DESBLOQUEAR PRO")
            with patch("novarix_stock.ui.main_window.QMessageBox.information") as info:
                QTest.mouseClick(window.pro_button, Qt.MouseButton.LeftButton)
                info.assert_called_once()
                self.assertEqual(
                    info.call_args.args[2],
                    "STOCK Pro no está disponible en este momento. Escribinos a soporte.",
                )
            self.assertEqual(database.get_plan(), AppPlan.FREE)
            window.close()

    def test_new_product_numeric_fields_replace_defaults_without_delete(self) -> None:
        dialog = ProductDialog()
        dialog.show()
        self.app.processEvents()

        QTest.mouseClick(dialog.sku_input, Qt.MouseButton.LeftButton)
        QTest.keyClicks(dialog.sku_input, "TEST-002")
        QTest.mouseClick(dialog.name_input, Qt.MouseButton.LeftButton)
        QTest.keyClicks(dialog.name_input, "Producto prueba 2")

        dialog.purchase_input.setFocus(Qt.FocusReason.TabFocusReason)
        self.app.processEvents()
        self.assertEqual(
            dialog.purchase_input.lineEdit().selectedText(),
            dialog.purchase_input.lineEdit().text(),
        )
        QTest.keyClick(dialog.purchase_input, Qt.Key.Key_Tab)
        self.app.processEvents()
        self.assertTrue(dialog.sale_input.hasFocus())
        self.assertEqual(
            dialog.sale_input.lineEdit().selectedText(),
            dialog.sale_input.lineEdit().text(),
        )

        numeric_entries = (
            (dialog.purchase_input, "1000"),
            (dialog.sale_input, "1500"),
            (dialog.minimum_input, "2"),
            (dialog.initial_input, "10"),
        )
        for field, text in numeric_entries:
            editor = field.lineEdit()
            self.assertFalse(editor.isReadOnly())
            self.assertNotEqual(field.focusPolicy(), Qt.FocusPolicy.NoFocus)
            QTest.mouseClick(editor, Qt.MouseButton.LeftButton)
            self.app.processEvents()
            self.assertEqual(editor.selectedText(), editor.text())
            QTest.keyClicks(editor, text)
            self.app.processEvents()

        draft = dialog.draft()
        self.assertEqual(draft.sku, "TEST-002")
        self.assertEqual(draft.name, "Producto prueba 2")
        self.assertEqual(draft.purchase_price_cents, 100_000)
        self.assertEqual(draft.sale_price_cents, 150_000)
        self.assertEqual(draft.minimum_stock, 2)
        self.assertEqual(draft.initial_stock, 10)
        self.assertEqual(dialog.purchase_input.minimum(), 0)
        self.assertEqual(dialog.sale_input.minimum(), 0)
        self.assertEqual(dialog.purchase_input.decimals(), 2)
        self.assertEqual(dialog.sale_input.decimals(), 2)
        self.assertEqual(dialog.minimum_input.minimum(), 0)
        self.assertEqual(dialog.initial_input.minimum(), 0)
        self.assertGreaterEqual(dialog.sku_input.width(), 250)
        self.assertGreaterEqual(dialog.purchase_input.lineEdit().width(), 200)
        dialog.close()

    def test_financial_summary_renders_registered_sales(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = StockDatabase(Path(temp_dir) / "financial_ui.db")
            product = database.add_product(
                ProductDraft(
                    sku="FIN-UI",
                    name="Producto financiero",
                    purchase_price_cents=100_000,
                    sale_price_cents=150_000,
                    minimum_stock=1,
                    initial_stock=10,
                )
            )
            database.register_sale(product.id, 4)

            window = MainWindow(database)
            window.show()
            self.app.processEvents()

            self.assertEqual(window.table.item(0, window.CURRENT).text(), "6")
            self.assertEqual(window.table.item(0, window.SOLD).text(), "4")
            self.assertEqual(window.total_invoiced_value.text(), "$ 6.000,00")
            self.assertEqual(window.total_cost_value.text(), "$ 4.000,00")
            self.assertEqual(window.total_profit_value.text(), "$ 2.000,00")
            self.assertEqual(window.today_invoiced_value.text(), "$ 6.000,00")
            self.assertEqual(window.today_profit_value.text(), "$ 2.000,00")
            self.assertEqual(window.month_invoiced_value.text(), "$ 6.000,00")
            self.assertEqual(window.month_profit_value.text(), "$ 2.000,00")
            window.close()

    def test_pro_plan_badge_hides_unlock_button(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = StockDatabase(Path(temp_dir) / "pro_ui.db")
            database.set_plan(AppPlan.PRO)

            window = MainWindow(database)
            window.show()
            self.app.processEvents()

            self.assertEqual(window.plan_label.text(), "PLAN PRO")
            self.assertFalse(window.pro_button.isVisible())
            window.close()

    def test_expired_pro_badge_keeps_unlock_button_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = StockDatabase(Path(temp_dir) / "expired_ui.db")
            now = datetime.now(timezone.utc).replace(microsecond=0)
            database.update_license(
                status=AppPlan.PRO_EXPIRED,
                license_id="SUB-UI-EXPIRED",
                expires_at=now - timedelta(days=1),
                last_validated_at=now,
            )

            window = MainWindow(database)
            window.show()
            self.app.processEvents()

            self.assertEqual(window.plan_label.text(), "PLAN PRO VENCIDO")
            self.assertTrue(window.pro_button.isVisible())
            window.close()

    def test_expired_pro_marks_and_disables_products_after_ten(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = StockDatabase(Path(temp_dir) / "expired_products_ui.db")
            now = datetime.now(timezone.utc).replace(microsecond=0)
            database.update_license(
                status=AppPlan.PRO_ACTIVE,
                license_id="SUB-UI-LOCKED",
                expires_at=now + timedelta(days=1),
                last_validated_at=now,
            )
            products = [
                database.add_product(
                    ProductDraft(
                        sku=f"UI-LOCK-{index:03d}",
                        name=f"Producto UI bloqueado {index}",
                        purchase_price_cents=1000,
                        sale_price_cents=1500,
                        minimum_stock=1,
                        initial_stock=2,
                    )
                )
                for index in range(20)
            ]
            database.update_license(
                status=AppPlan.PRO_EXPIRED,
                license_id="SUB-UI-LOCKED",
                expires_at=now - timedelta(seconds=1),
                last_validated_at=now,
            )

            window = MainWindow(database)
            window.show()
            self.app.processEvents()

            eleventh_row = next(
                row
                for row in range(window.table.rowCount())
                if window.table.item(row, window.PHOTO).data(Qt.ItemDataRole.UserRole)
                == products[10].id
            )
            window.table.selectRow(eleventh_row)
            self.app.processEvents()
            self.assertEqual(
                window.table.item(eleventh_row, window.STATUS).text(),
                "BLOQUEADO — PRO",
            )
            self.assertFalse(window.edit_button.isEnabled())
            self.assertFalse(window.entry_button.isEnabled())
            self.assertFalse(window.sale_button.isEnabled())
            self.assertTrue(window.delete_button.isEnabled())
            with patch("novarix_stock.ui.main_window.QMessageBox.information") as info:
                window._edit_product()
                info.assert_called_once()

            database.delete_product(products[4].id)
            window._refresh_table(products[10].id)
            self.app.processEvents()
            self.assertNotIn(products[10].id, window.locked_product_ids)

            database.update_license(
                status=AppPlan.PRO_ACTIVE,
                license_id="SUB-UI-LOCKED",
                expires_at=now + timedelta(days=30),
                last_validated_at=now,
            )
            window._refresh_table()
            self.assertEqual(window.locked_product_ids, set())
            window.close()


if __name__ == "__main__":
    unittest.main()
