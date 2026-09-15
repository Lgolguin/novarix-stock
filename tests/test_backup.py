from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

from novarix_stock.backup import (  # noqa: E402
    BACKUP_FORMAT,
    BACKUP_VERSION,
    DATABASE_NAME,
    IMAGE_DIR_NAME,
    MANIFEST_NAME,
    BackupError,
    BackupManifest,
    create_backup,
    restore_backup,
    suggested_backup_name,
    validate_backup,
)
from novarix_stock.database import StockDatabase  # noqa: E402
from novarix_stock.models import AppPlan, ProductDraft  # noqa: E402
from novarix_stock.stock_movement_history import query_stock_movements  # noqa: E402
from novarix_stock.ui.main_window import MainWindow  # noqa: E402


class BackupCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.db_path = self.data_dir / DATABASE_NAME
        self.image_dir = self.data_dir / IMAGE_DIR_NAME
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self.database = StockDatabase(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _make_image(self, name: str, content: bytes) -> Path:
        path = self.image_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def _seed_inventory(self) -> tuple[dict, dict, dict, dict]:
        photo1 = self._make_image("p1.jpg", b"photo-one")
        photo2 = self._make_image("sub/p2.png", b"photo-two")

        product1 = self.database.add_product(
            ProductDraft(
                sku="BK-001",
                name="Producto backup A",
                purchase_price_cents=10_000,
                sale_price_cents=15_000,
                minimum_stock=2,
                initial_stock=10,
                photo_path=str(photo1),
            )
        )
        product2 = self.database.add_product(
            ProductDraft(
                sku="BK-002",
                name="Producto backup B",
                purchase_price_cents=20_000,
                sale_price_cents=30_000,
                minimum_stock=1,
                initial_stock=5,
                photo_path=str(photo2),
            )
        )

        self.database.register_entry(product1.id, 5)
        self.database.register_sale(product1.id, 3)
        self.database.adjust_stock(product2.id, 8)

        sales = self.database.list_sales()
        self.database.cancel_sale(sales[0].id, reason="error de carga")

        self.database.set_plan(AppPlan.PRO)

        return {"p1": product1, "p2": product2}, {"photo1": photo1, "photo2": photo2}

    def _snapshot(self) -> dict:
        return {
            "products": self.database.list_products(),
            "sales": self.database.list_sales(),
            "entries": self.database.list_stock_entries(),
            "adjustments": self.database.list_stock_adjustments(),
            "cancellations": self.database.list_sale_cancellations(),
            "financials": self.database.get_financial_summary(),
            "movements": query_stock_movements(self.database),
            "plan": self.database.get_plan(),
            "images": sorted(
                [
                    (path.relative_to(self.image_dir).as_posix(), path.read_bytes())
                    for path in self.image_dir.rglob("*")
                    if path.is_file()
                ]
            ),
        }

    def test_create_backup_contains_database_images_and_manifest(self) -> None:
        self._seed_inventory()
        backup_path = self.data_dir / suggested_backup_name()

        create_backup(self.db_path, self.image_dir, backup_path)

        self.assertTrue(backup_path.exists())
        with zipfile.ZipFile(backup_path, "r") as archive:
            names = set(archive.namelist())
            self.assertIn(DATABASE_NAME, names)
            self.assertIn(MANIFEST_NAME, names)
            self.assertIn(f"{IMAGE_DIR_NAME}/p1.jpg", names)
            self.assertIn(f"{IMAGE_DIR_NAME}/sub/p2.png", names)

            raw_manifest = json.loads(archive.read(MANIFEST_NAME))
            manifest = BackupManifest(**raw_manifest)
            self.assertEqual(manifest.format, BACKUP_FORMAT)
            self.assertEqual(manifest.version, BACKUP_VERSION)
            self.assertEqual(manifest.app_name, "STOCK by NOVARIX")
            self.assertEqual(manifest.database_name, DATABASE_NAME)
            self.assertEqual(manifest.image_dir_name, IMAGE_DIR_NAME)
            self.assertEqual(
                sorted(manifest.files),
                sorted(
                    [
                        DATABASE_NAME,
                        MANIFEST_NAME,
                        f"{IMAGE_DIR_NAME}/p1.jpg",
                        f"{IMAGE_DIR_NAME}/sub/p2.png",
                    ]
                ),
            )
            self.assertTrue(datetime.fromisoformat(manifest.created_at))

    def test_restore_backup_restores_full_state(self) -> None:
        self._seed_inventory()
        original = self._snapshot()
        backup_path = self.data_dir / "restore_test.zip"
        create_backup(self.db_path, self.image_dir, backup_path)

        # Modify current data significantly.
        self.database.delete_product(original["products"][0].id)
        self.database.add_product(
            ProductDraft(
                sku="NEW-001",
                name="Producto nuevo",
                purchase_price_cents=1_000,
                sale_price_cents=2_000,
                minimum_stock=0,
                initial_stock=1,
            )
        )
        self.database.set_plan(AppPlan.FREE)
        self._make_image("extra.txt", b"should-disappear")

        restore_backup(backup_path, self.data_dir)

        restored = self._snapshot()
        self.assertEqual(len(restored["products"]), 2)
        self.assertEqual(restored["products"], original["products"])
        self.assertEqual(restored["sales"], original["sales"])
        self.assertEqual(restored["entries"], original["entries"])
        self.assertEqual(restored["adjustments"], original["adjustments"])
        self.assertEqual(restored["cancellations"], original["cancellations"])
        self.assertEqual(restored["financials"], original["financials"])
        self.assertEqual(
            [m.movement_type for m in restored["movements"]],
            [m.movement_type for m in original["movements"]],
        )
        self.assertEqual(restored["plan"], AppPlan.PRO)
        self.assertEqual(restored["images"], original["images"])

    def test_backup_does_not_include_installation_db(self) -> None:
        self._seed_inventory()
        installation = self.data_dir / "installation.db"
        installation.write_bytes(b"secret-installation")
        backup_path = self.data_dir / "no_installation.zip"

        create_backup(self.db_path, self.image_dir, backup_path)

        with zipfile.ZipFile(backup_path, "r") as archive:
            self.assertNotIn("installation.db", archive.namelist())
            for name in archive.namelist():
                self.assertFalse(name.endswith("installation.db"))

    def test_invalid_zip_rejected(self) -> None:
        foreign = self.data_dir / "foreign.zip"
        with zipfile.ZipFile(foreign, "w") as archive:
            archive.writestr("readme.txt", b"not a stock backup")

        with self.assertRaises(BackupError):
            validate_backup(foreign)

    def test_corrupt_database_rejected(self) -> None:
        self._seed_inventory()
        original_backup = self.data_dir / "original.zip"
        create_backup(self.db_path, self.image_dir, original_backup)

        corrupt_backup = self.data_dir / "corrupt.zip"
        with zipfile.ZipFile(original_backup, "r") as original:
            manifest = original.read(MANIFEST_NAME)
        with zipfile.ZipFile(corrupt_backup, "w") as archive:
            archive.writestr(MANIFEST_NAME, manifest)
            archive.writestr(DATABASE_NAME, b"not a sqlite database")

        with self.assertRaises(BackupError):
            validate_backup(corrupt_backup)

    def test_restore_rollback_on_failure(self) -> None:
        self._seed_inventory()
        backup_path = self.data_dir / "rollback.zip"
        create_backup(self.db_path, self.image_dir, backup_path)
        original_db_bytes = self.db_path.read_bytes()
        original_images = self._snapshot()["images"]

        with patch(
            "novarix_stock.backup._extract_member",
            side_effect=BackupError("forced extraction failure"),
        ):
            with self.assertRaises(BackupError):
                restore_backup(backup_path, self.data_dir)

        self.assertEqual(self.db_path.read_bytes(), original_db_bytes)
        self.assertEqual(self._snapshot()["images"], original_images)
        safety_backups = list(self.data_dir.glob("safety_backup_*.zip"))
        self.assertEqual(len(safety_backups), 1)
        with zipfile.ZipFile(safety_backups[0], "r") as archive:
            self.assertIn(DATABASE_NAME, archive.namelist())


class BackupUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def _seed(self, database: StockDatabase) -> None:
        database.add_product(
            ProductDraft(
                sku="UI-BK-001",
                name="Producto UI backup",
                purchase_price_cents=5_000,
                sale_price_cents=8_000,
                minimum_stock=1,
                initial_stock=4,
            )
        )

    def test_ui_has_backup_buttons(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = StockDatabase(Path(temp_dir) / DATABASE_NAME)
            self._seed(database)
            window = MainWindow(database)
            window.show()
            self.app.processEvents()

            self.assertEqual(window.backup_button.text(), "Crear backup")
            self.assertEqual(window.restore_button.text(), "Restaurar backup")
            self.assertEqual(window.backup_button.objectName(), "excelButton")
            self.assertEqual(window.restore_button.objectName(), "excelButton")
            window.close()

    def test_ui_create_and_restore_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            db_path = data_dir / DATABASE_NAME
            database = StockDatabase(db_path)
            self._seed(database)
            original_count = len(database.list_products())

            backup_path = data_dir / suggested_backup_name()

            window = MainWindow(database)
            window.show()
            self.app.processEvents()

            with patch.object(
                QFileDialog, "getSaveFileName", return_value=(str(backup_path), "")
            ) as save_dialog, patch.object(
                QMessageBox, "information", return_value=None
            ) as info_box:
                QTest.mouseClick(window.backup_button, Qt.MouseButton.LeftButton)
                self.app.processEvents()
                save_dialog.assert_called_once()
                info_box.assert_called_once()

            self.assertTrue(backup_path.exists())

            # Alter data so restoration is observable.
            database.add_product(
                ProductDraft(
                    sku="UI-BK-002",
                    name="Producto extra",
                    purchase_price_cents=1_000,
                    sale_price_cents=2_000,
                    minimum_stock=0,
                    initial_stock=1,
                )
            )
            window._refresh_table()
            self.app.processEvents()
            self.assertEqual(window.table.rowCount(), 2)

            with patch.object(
                QFileDialog, "getOpenFileName", return_value=(str(backup_path), "")
            ) as open_dialog, patch.object(
                QMessageBox,
                "warning",
                return_value=QMessageBox.StandardButton.Yes,
            ) as warning_box, patch.object(
                QMessageBox, "information", return_value=None
            ) as info_box:
                QTest.mouseClick(window.restore_button, Qt.MouseButton.LeftButton)
                self.app.processEvents()
                open_dialog.assert_called_once()
                warning_box.assert_called_once()
                info_box.assert_called_once()

            self.assertEqual(window.table.rowCount(), original_count)
            self.assertEqual(len(database.list_products()), original_count)
            window.close()


if __name__ == "__main__":
    unittest.main()
