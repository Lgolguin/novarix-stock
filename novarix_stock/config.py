from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "STOCK by NOVARIX"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

if getattr(sys, "frozen", False):
    _base = Path(sys._MEIPASS)
    APP_DIR = _base
    DATA_DIR = Path(os.environ["APPDATA"]) / "NOVARIX" / "STOCK"
else:
    APP_DIR = PROJECT_ROOT
    DATA_DIR = PROJECT_ROOT / "data"

DATABASE_PATH = DATA_DIR / "novarix_stock.db"
IMAGE_DIR = DATA_DIR / "product_images"

