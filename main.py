from __future__ import annotations

import sys
import os
from pathlib import Path

from PySide6.QtWidgets import QApplication

from novarix_stock.config import APP_NAME
from novarix_stock.config import DATABASE_PATH
from novarix_stock.licensing import LicenseClient, LicensedStockDatabase
from novarix_stock.ui.pro_controller import ProController
from novarix_stock.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("NOVARIX")
    app.setStyle("Fusion")

    data_dir = Path(os.getenv("STOCK_DATA_DIR", str(DATABASE_PATH.parent)))
    license_client = LicenseClient.from_env(data_dir / "installation.db")
    database = LicensedStockDatabase(data_dir / "novarix_stock.db", license_client)
    window = MainWindow(database)
    controller = ProController(window, license_client)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

