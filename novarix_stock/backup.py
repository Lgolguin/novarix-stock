from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


class BackupError(Exception):
    """Error controlado de backup/restore con mensaje legible para el usuario."""


BACKUP_FORMAT = "novarix-stock-backup"
BACKUP_VERSION = 1
DATABASE_NAME = "novarix_stock.db"
IMAGE_DIR_NAME = "product_images"
MANIFEST_NAME = "manifest.json"

REQUIRED_TABLES = {
    "products",
    "sales",
    "sale_cancellations",
    "stock_entries",
    "stock_adjustments",
    "app_settings",
    "license_state",
}


@dataclass(frozen=True)
class BackupManifest:
    format: str
    version: int
    created_at: str
    app_name: str
    database_name: str
    image_dir_name: str
    files: list[str]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def suggested_backup_name() -> str:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    return f"STOCK_BACKUP_{stamp}.zip"


def _sqlite_backup(source_path: Path, destination_path: Path) -> None:
    """Create a consistent SQLite snapshot using the online backup API."""
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    try:
        destination = sqlite3.connect(destination_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()


def _relative_image_paths(image_dir: Path) -> Iterable[Path]:
    if not image_dir.exists():
        return
    for path in image_dir.rglob("*"):
        if path.is_file():
            yield path.relative_to(image_dir)


def create_backup(
    database_path: Path,
    image_dir: Path,
    output_path: Path,
    app_name: str = "STOCK by NOVARIX",
) -> None:
    """Create a single ZIP backup of inventory data and product images."""
    if not database_path.exists():
        raise BackupError("No se encontró la base de datos de inventario.")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            snapshot_db = tmp_dir / DATABASE_NAME
            _sqlite_backup(database_path, snapshot_db)

            files: list[str] = [DATABASE_NAME, MANIFEST_NAME]

            with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.write(snapshot_db, DATABASE_NAME)

                for relative in _relative_image_paths(image_dir):
                    full = image_dir / relative
                    arcname = f"{IMAGE_DIR_NAME}/{relative.as_posix()}"
                    archive.write(full, arcname)
                    files.append(arcname)

                manifest = BackupManifest(
                    format=BACKUP_FORMAT,
                    version=BACKUP_VERSION,
                    created_at=_now_iso(),
                    app_name=app_name,
                    database_name=DATABASE_NAME,
                    image_dir_name=IMAGE_DIR_NAME,
                    files=sorted(files),
                )
                archive.writestr(MANIFEST_NAME, json.dumps(asdict(manifest), indent=2))

        temp_path.replace(output_path)
    except BackupError:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise BackupError(f"No se pudo crear el backup: {exc}") from exc


def _read_manifest(archive: zipfile.ZipFile) -> BackupManifest:
    try:
        raw = archive.read(MANIFEST_NAME)
    except KeyError as exc:
        raise BackupError("El archivo no es un backup de STOCK: falta el manifest.") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BackupError("El manifest del backup está corrupto.") from exc

    try:
        manifest = BackupManifest(**data)
    except TypeError as exc:
        raise BackupError("El manifest del backup tiene un formato inválido.") from exc

    if manifest.format != BACKUP_FORMAT:
        raise BackupError("El archivo seleccionado no es un backup de STOCK.")
    if manifest.version != BACKUP_VERSION:
        raise BackupError("La versión del backup no es compatible con esta aplicación.")
    return manifest


def _validate_database(database_path: Path) -> None:
    try:
        connection = sqlite3.connect(database_path)
    except sqlite3.Error as exc:
        raise BackupError("La base de datos del backup no es válida.") from exc

    try:
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise BackupError("La base de datos del backup está corrupta.")

            fk_violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if fk_violations:
                raise BackupError("La base de datos del backup tiene violaciones de claves foráneas.")

            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            missing = REQUIRED_TABLES - tables
            if missing:
                raise BackupError(
                    f"La base de datos del backup no contiene las tablas esperadas: {', '.join(sorted(missing))}."
                )
        except sqlite3.Error as exc:
            raise BackupError("La base de datos del backup no es válida.") from exc
    finally:
        connection.close()


def validate_backup(archive_path: Path) -> BackupManifest:
    """Validate that a ZIP file is a healthy STOCK backup."""
    if not archive_path.exists():
        raise BackupError("No se encontró el archivo de backup.")

    try:
        archive = zipfile.ZipFile(archive_path, "r")
    except zipfile.BadZipFile as exc:
        raise BackupError("El archivo no es un ZIP válido.") from exc

    with archive:
        manifest = _read_manifest(archive)
        if DATABASE_NAME not in archive.namelist():
            raise BackupError("El backup no contiene la base de datos.")

        with tempfile.TemporaryDirectory() as tmp:
            extracted = Path(tmp) / DATABASE_NAME
            archive.extract(DATABASE_NAME, tmp)
            _validate_database(extracted)

    return manifest


def _safety_backup_path(data_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return data_dir / f"safety_backup_{stamp}.zip"


def _create_safety_backup(data_dir: Path, output_path: Path) -> None:
    """Archive current database and images before a restore operation."""
    database_path = data_dir / DATABASE_NAME
    image_dir = data_dir / IMAGE_DIR_NAME
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as archive:
        if database_path.exists():
            archive.write(database_path, DATABASE_NAME)
        if image_dir.exists():
            for relative in _relative_image_paths(image_dir):
                full = image_dir / relative
                archive.write(full, f"{IMAGE_DIR_NAME}/{relative.as_posix()}")


def _extract_member(
    archive: zipfile.ZipFile,
    member: str,
    destination: Path,
    temp_dir: Path,
) -> Path:
    if member not in archive.namelist():
        raise BackupError(f"El backup no contiene el miembro esperado: {member}")
    archive.extract(member, temp_dir)
    return temp_dir / member


def restore_backup(
    archive_path: Path,
    data_dir: Path,
    safety_backup_path: Path | None = None,
) -> None:
    """Restore inventory data from a validated backup, keeping a safety copy."""
    manifest = validate_backup(archive_path)
    if safety_backup_path is None:
        safety_backup_path = _safety_backup_path(data_dir)

    _create_safety_backup(data_dir, safety_backup_path)

    database_path = data_dir / DATABASE_NAME
    image_dir = data_dir / IMAGE_DIR_NAME

    database_old = data_dir / f"{DATABASE_NAME}.pre_restore"
    image_dir_old = data_dir / f"{IMAGE_DIR_NAME}.pre_restore"
    database_new = data_dir / f"{DATABASE_NAME}.new"
    image_dir_new = data_dir / f"{IMAGE_DIR_NAME}.new"

    for path in (database_old, image_dir_old, database_new, image_dir_new):
        if path.exists():
            _rm_tree_or_file(path)

    def rollback() -> None:
        """Revert any partial restore and re-raise as BackupError."""
        try:
            if database_new.exists():
                database_new.unlink(missing_ok=True)
            if image_dir_new.exists():
                _rm_tree_or_file(image_dir_new)
            if database_old.exists():
                if database_path.exists():
                    database_path.unlink(missing_ok=True)
                database_old.rename(database_path)
            if image_dir_old.exists():
                if image_dir.exists():
                    _rm_tree_or_file(image_dir)
                image_dir_old.rename(image_dir)
        except OSError as exc:
            raise BackupError(
                "La restauración falló y no se pudo recuperar el estado anterior. "
                f"Backup de seguridad en: {safety_backup_path}"
            ) from exc

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            with zipfile.ZipFile(archive_path, "r") as archive:
                _extract_member(archive, DATABASE_NAME, database_new, tmp_dir)
                if database_new.exists():
                    database_new.unlink()
                shutil.move(str(tmp_dir / DATABASE_NAME), str(database_new))

                image_members = [
                    name
                    for name in archive.namelist()
                    if name.startswith(f"{IMAGE_DIR_NAME}/")
                ]
                if image_members:
                    image_dir_new.mkdir(parents=True, exist_ok=True)
                    for member in image_members:
                        relative = Path(member).relative_to(IMAGE_DIR_NAME)
                        destination = image_dir_new / relative
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with archive.open(member) as source, destination.open("wb") as target:
                            shutil.copyfileobj(source, target)

        _validate_database(database_new)

        if database_path.exists():
            database_path.rename(database_old)
        database_new.rename(database_path)

        if image_dir.exists():
            image_dir.rename(image_dir_old)
        if image_dir_new.exists():
            image_dir_new.rename(image_dir)
        else:
            image_dir.mkdir(parents=True, exist_ok=True)

        for path in (database_old, image_dir_old):
            if path.exists():
                _rm_tree_or_file(path)

    except BackupError:
        rollback()
        raise
    except Exception as exc:
        rollback()
        raise BackupError(f"No se pudo restaurar el backup: {exc}") from exc


def _rm_tree_or_file(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)
