from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from novarix_stock.config import IMAGE_DIR


class ProductImageStore:
    ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

    def __init__(self, image_dir: str | Path = IMAGE_DIR) -> None:
        self.image_dir = Path(image_dir)
        self.image_dir.mkdir(parents=True, exist_ok=True)

    def import_image(self, source: str | Path) -> str:
        source_path = Path(source)
        suffix = source_path.suffix.lower()
        if suffix not in self.ALLOWED_SUFFIXES:
            suffix = ".jpg"
        destination = self.image_dir / f"{uuid.uuid4().hex}{suffix}"
        shutil.copy2(source_path, destination)
        return str(destination.resolve())

    def delete_managed_image(self, image_path: str | None) -> None:
        if not image_path:
            return
        candidate = Path(image_path)
        try:
            if candidate.resolve().parent == self.image_dir.resolve() and candidate.exists():
                candidate.unlink()
        except OSError:
            # La eliminación del producto no debe fallar por una foto bloqueada.
            pass

