from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from novarix_stock.config import DATABASE_PATH
from novarix_stock.models import (
    AppPlan,
    ImportedProductDraft,
    LicenseState,
    Product,
    ProductDraft,
    Sale,
)
from novarix_stock.summary import FinancialSummary, SalesSummary


FREE_PRODUCT_LIMIT = 10
FREE_PRODUCT_LIMIT_MESSAGE = (
    "Alcanzaste el límite de 10 productos de STOCK Free.\n"
    "Desbloqueá STOCK Pro para cargar productos ilimitados."
)
LOCKED_PRODUCT_MESSAGE = (
    "Este producto está bloqueado porque excede los 10 productos activos "
    "del plan actual. Renovando STOCK Pro se desbloquea automáticamente."
)


class StockError(Exception):
    """Error de negocio que puede mostrarse directamente al usuario."""


class PlanLimitError(StockError):
    """El plan actual no permite crear más productos."""


class ProductLockedError(StockError):
    """El plan actual no permite operar sobre este producto."""


class StockDatabase:
    def __init__(self, database_path: str | Path = DATABASE_PATH) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._create_schema()

    def _next_movement_timestamp(self, connection: sqlite3.Connection) -> str:
        """Return a persistently monotonic UTC timestamp for stock movements."""
        row = connection.execute(
            "SELECT value FROM app_settings WHERE key = 'movement_clock'"
        ).fetchone()
        current = datetime.now(timezone.utc)
        previous = self._text_to_datetime(row["value"]) if row else None
        timestamp = (
            current
            if previous is None or current > previous
            else previous + timedelta(microseconds=1)
        )
        value = timestamp.isoformat()
        connection.execute(
            """
            INSERT INTO app_settings (key, value) VALUES ('movement_clock', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (value,),
        )
        return value

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            with connection:
                yield connection
        finally:
            connection.close()

    def _create_schema(self) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sku TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    name TEXT NOT NULL,
                    purchase_price_cents INTEGER NOT NULL CHECK (purchase_price_cents >= 0),
                    sale_price_cents INTEGER NOT NULL CHECK (sale_price_cents >= 0),
                    stock_total INTEGER NOT NULL DEFAULT 0 CHECK (stock_total >= 0),
                    stock_current INTEGER NOT NULL DEFAULT 0 CHECK (stock_current >= 0),
                    sold INTEGER NOT NULL DEFAULT 0 CHECK (sold >= 0),
                    minimum_stock INTEGER NOT NULL DEFAULT 0 CHECK (minimum_stock >= 0),
                    photo_path TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sales (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER NOT NULL,
                    product_sku TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    quantity INTEGER NOT NULL CHECK (quantity > 0),
                    purchase_price_cents INTEGER NOT NULL
                        CHECK (purchase_price_cents >= 0),
                    sale_price_cents INTEGER NOT NULL
                        CHECK (sale_price_cents >= 0),
                    sold_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute("""
                CREATE TABLE IF NOT EXISTS stock_adjustments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER NOT NULL,
                    product_sku TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    stock_before INTEGER NOT NULL CHECK (stock_before >= 0),
                    stock_after INTEGER NOT NULL CHECK (stock_after >= 0),
                    difference INTEGER NOT NULL CHECK (difference = stock_after - stock_before),
                    adjusted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Adjustment snapshots also survive product deletion; no cascading FK.
            connection.execute("CREATE INDEX IF NOT EXISTS idx_adjustments_product_id "
                               "ON stock_adjustments(product_id)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS stock_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id INTEGER NOT NULL,
                    product_sku TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    quantity INTEGER NOT NULL CHECK (quantity > 0),
                    stock_before INTEGER NOT NULL CHECK (stock_before >= 0),
                    stock_after INTEGER NOT NULL CHECK (stock_after >= 0),
                    entry_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_stock_entries_product_id ON stock_entries(product_id)"
            )
            connection.execute("""
                CREATE TABLE IF NOT EXISTS sale_cancellations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sale_id INTEGER NOT NULL UNIQUE,
                    cancelled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    quantity INTEGER NOT NULL CHECK (quantity > 0),
                    invoiced_cents INTEGER NOT NULL,
                    cost_cents INTEGER NOT NULL,
                    profit_cents INTEGER NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    stock_returned INTEGER NOT NULL CHECK (stock_returned IN (0, 1))
                )
            """)
            self._ensure_column(connection, "sales", "stock_before", "INTEGER")
            self._ensure_column(connection, "sales", "stock_after", "INTEGER")
            self._ensure_column(connection, "sale_cancellations", "stock_before", "INTEGER")
            self._ensure_column(connection, "sale_cancellations", "stock_after", "INTEGER")
            # Historical snapshots must outlive products. Rebuild legacy tables
            # atomically, retaining IDs, timestamps, cents and AUTOINCREMENT state.
            if connection.execute("PRAGMA foreign_key_list(sales)").fetchall():
                sequence = connection.execute(
                    "SELECT seq FROM sqlite_sequence WHERE name = 'sales'"
                ).fetchone()
                connection.execute("""
                    CREATE TABLE sales_history_migration (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        product_id INTEGER NOT NULL,
                        product_sku TEXT NOT NULL,
                        product_name TEXT NOT NULL,
                        quantity INTEGER NOT NULL CHECK (quantity > 0),
                        purchase_price_cents INTEGER NOT NULL CHECK (purchase_price_cents >= 0),
                        sale_price_cents INTEGER NOT NULL CHECK (sale_price_cents >= 0),
                        sold_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        stock_before INTEGER,
                        stock_after INTEGER
                    )
                """)
                connection.execute("INSERT INTO sales_history_migration SELECT * FROM sales")
                connection.execute("DROP TABLE sales")
                connection.execute("ALTER TABLE sales_history_migration RENAME TO sales")
                if sequence:
                    connection.execute(
                        "UPDATE sqlite_sequence SET seq = ? WHERE name = 'sales'",
                        (sequence[0],),
                    )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_sales_product_id ON sales(product_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_sales_sold_at ON sales(sold_at)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO app_settings (key, value) VALUES ('plan', 'FREE')"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS license_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    status TEXT NOT NULL CHECK (
                        status IN ('FREE', 'PRO_ACTIVE', 'PRO_EXPIRED')
                    ),
                    license_id TEXT,
                    expires_at TEXT,
                    last_validated_at TEXT
                )
                """
            )
            legacy_plan = str(
                connection.execute(
                    "SELECT value FROM app_settings WHERE key = 'plan'"
                ).fetchone()["value"]
            )
            migrated_status = (
                AppPlan.PRO_ACTIVE.value
                if legacy_plan in {"PRO", "PRO_ACTIVE"}
                else AppPlan.PRO_EXPIRED.value
                if legacy_plan == "PRO_EXPIRED"
                else AppPlan.FREE.value
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO license_state (
                    id, status, license_id, expires_at, last_validated_at
                ) VALUES (1, ?, NULL, NULL, NULL)
                """,
                (migrated_status,),
            )

    @staticmethod
    def _validate_draft(draft: ProductDraft) -> ProductDraft:
        sku = draft.sku.strip()
        name = draft.name.strip()
        if not name:
            raise StockError("El nombre del producto es obligatorio.")
        numeric_values = (
            draft.purchase_price_cents,
            draft.sale_price_cents,
            draft.minimum_stock,
            draft.initial_stock,
        )
        if any(value < 0 for value in numeric_values):
            raise StockError("Los precios y las cantidades no pueden ser negativos.")
        return ProductDraft(
            sku=sku,
            name=name,
            purchase_price_cents=draft.purchase_price_cents,
            sale_price_cents=draft.sale_price_cents,
            minimum_stock=draft.minimum_stock,
            photo_path=draft.photo_path,
            initial_stock=draft.initial_stock,
        )

    @staticmethod
    def _resolve_sku(connection: sqlite3.Connection, sku: str) -> str:
        if sku:
            return sku

        highest_number = 0
        rows = connection.execute(
            "SELECT sku FROM products WHERE sku LIKE 'STK-%'"
        ).fetchall()
        for row in rows:
            match = re.fullmatch(r"STK-(\d+)", str(row["sku"]), re.IGNORECASE)
            if match:
                highest_number = max(highest_number, int(match.group(1)))
        return f"STK-{highest_number + 1:04d}"

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection, table: str, column: str, column_type: str
    ) -> None:
        columns = {
            str(row["name"])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

    def list_products(self) -> list[Product]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM products ORDER BY name COLLATE NOCASE, id"
            ).fetchall()
        return [self._row_to_product(row) for row in rows]

    def get_plan(self) -> AppPlan:
        with self._connect() as connection:
            return self._get_plan(connection)

    def set_plan(self, plan: AppPlan) -> None:
        current = self.get_license_state()
        self.update_license(
            status=AppPlan(plan),
            license_id=current.license_id,
            expires_at=current.expires_at,
            last_validated_at=current.last_validated_at,
        )

    def get_license_state(self) -> LicenseState:
        with self._connect() as connection:
            return self._get_license_state(connection)

    def update_license(
        self,
        *,
        status: AppPlan,
        license_id: str | None,
        expires_at: datetime | None,
        last_validated_at: datetime | None,
    ) -> LicenseState:
        status = AppPlan(status)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE license_state
                SET status = ?, license_id = ?, expires_at = ?, last_validated_at = ?
                WHERE id = 1
                """,
                (
                    status.value,
                    license_id.strip() if license_id and license_id.strip() else None,
                    self._datetime_to_text(expires_at),
                    self._datetime_to_text(last_validated_at),
                ),
            )
            connection.execute(
                "UPDATE app_settings SET value = ? WHERE key = 'plan'",
                (status.value,),
            )
        return self.get_license_state()

    def can_add_product(self) -> bool:
        with self._connect() as connection:
            if self._get_plan(connection) is AppPlan.PRO_ACTIVE:
                return True
            count = int(connection.execute("SELECT COUNT(*) FROM products").fetchone()[0])
            return count < FREE_PRODUCT_LIMIT

    def get_locked_product_ids(self) -> set[int]:
        with self._connect() as connection:
            return self._locked_product_ids(connection)

    def is_product_locked(self, product_id: int) -> bool:
        with self._connect() as connection:
            return product_id in self._locked_product_ids(connection)

    def get_product(self, product_id: int) -> Product | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM products WHERE id = ?", (product_id,)
            ).fetchone()
        return self._row_to_product(row) if row else None

    def list_sales(self, product_id: int | None = None) -> list[Sale]:
        with self._connect() as connection:
            if product_id is None:
                rows = connection.execute(
                    "SELECT sales.*, (SELECT cancelled_at FROM sale_cancellations WHERE sale_id = sales.id) AS cancelled_at FROM sales ORDER BY sold_at, id"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT sales.*, (SELECT cancelled_at FROM sale_cancellations WHERE sale_id = sales.id) AS cancelled_at FROM sales WHERE product_id = ? ORDER BY sold_at, id",
                    (product_id,),
                ).fetchall()
        return [self._row_to_sale(row) for row in rows]

    def cancel_sale(self, sale_id: int, reason: str = "") -> None:
        if not isinstance(reason, str):
            raise StockError("El motivo debe ser texto.")
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                sale = connection.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
                if sale is None:
                    raise StockError("La venta ya no existe.")
                if connection.execute("SELECT 1 FROM sale_cancellations WHERE sale_id = ?", (sale_id,)).fetchone():
                    raise StockError("La venta ya está ANULADA.")
                product = connection.execute("SELECT * FROM products WHERE id = ?", (sale["product_id"],)).fetchone()
                quantity = int(sale["quantity"])
                stock_before = None
                stock_after = None
                if product is not None:
                    if product["sold"] < quantity:
                        raise StockError("El contador Vendidos no permite revertir esta venta. Revisá la integridad del inventario.")
                    stock_before = int(product["stock_current"])
                    stock_after = stock_before + quantity
                    # A historical correction is allowed regardless of the license.
                    connection.execute(
                        "UPDATE products SET stock_current = stock_current + ?, sold = sold - ?, "
                        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (quantity, quantity, sale["product_id"]),
                    )
                invoiced = quantity * int(sale["sale_price_cents"])
                cost = quantity * int(sale["purchase_price_cents"])
                connection.execute(
                    "INSERT INTO sale_cancellations "
                    "(sale_id, quantity, invoiced_cents, cost_cents, profit_cents, reason, stock_returned, stock_before, stock_after, cancelled_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (sale_id, quantity, invoiced, cost, invoiced - cost, reason.strip(), int(product is not None), stock_before, stock_after, self._next_movement_timestamp(connection)),
                )
        except (sqlite3.Error, OverflowError) as error:
            raise StockError("No se pudo anular la venta.") from error

    def get_sales_summary(self, product_id: int | None = None) -> SalesSummary:
        """Aggregate immutable sale snapshots, including deleted products globally."""
        where = "" if product_id is None else " WHERE product_id = ?"
        parameters = () if product_id is None else (product_id,)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COALESCE(SUM(quantity), 0) AS units_sold,
                          COALESCE(SUM(quantity * sale_price_cents), 0) AS invoiced,
                          COALESCE(SUM(quantity * purchase_price_cents), 0) AS cost,
                          COALESCE(SUM(quantity * (sale_price_cents - purchase_price_cents)), 0) AS profit
                   FROM sales WHERE NOT EXISTS
                   (SELECT 1 FROM sale_cancellations WHERE sale_id = sales.id)"""
                + ("" if product_id is None else " AND product_id = ?"), parameters,
            ).fetchone()
        return SalesSummary(int(row["units_sold"]), int(row["invoiced"]),
                            int(row["cost"]), int(row["profit"]))

    def get_financial_summary(self) -> FinancialSummary:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    COALESCE(SUM(quantity * sale_price_cents), 0) AS total_invoiced,
                    COALESCE(SUM(quantity * purchase_price_cents), 0) AS total_cost,
                    COALESCE(SUM(
                        quantity * (sale_price_cents - purchase_price_cents)
                    ), 0) AS total_profit,
                    COALESCE(SUM(CASE
                        WHEN date(sold_at, 'localtime') = date('now', 'localtime')
                        THEN quantity * sale_price_cents ELSE 0 END
                    ), 0) AS today_invoiced,
                    COALESCE(SUM(CASE
                        WHEN date(sold_at, 'localtime') = date('now', 'localtime')
                        THEN quantity * (sale_price_cents - purchase_price_cents)
                        ELSE 0 END
                    ), 0) AS today_profit,
                    COALESCE(SUM(CASE
                        WHEN strftime('%Y-%m', sold_at, 'localtime') =
                             strftime('%Y-%m', 'now', 'localtime')
                        THEN quantity * sale_price_cents ELSE 0 END
                    ), 0) AS month_invoiced,
                    COALESCE(SUM(CASE
                        WHEN strftime('%Y-%m', sold_at, 'localtime') =
                             strftime('%Y-%m', 'now', 'localtime')
                        THEN quantity * (sale_price_cents - purchase_price_cents)
                        ELSE 0 END
                    ), 0) AS month_profit
                FROM sales
                WHERE NOT EXISTS (SELECT 1 FROM sale_cancellations WHERE sale_id = sales.id)
                """
            ).fetchone()
        return FinancialSummary(
            total_invoiced_cents=int(row["total_invoiced"]),
            total_cost_cents=int(row["total_cost"]),
            total_profit_cents=int(row["total_profit"]),
            today_invoiced_cents=int(row["today_invoiced"]),
            today_profit_cents=int(row["today_profit"]),
            month_invoiced_cents=int(row["month_invoiced"]),
            month_profit_cents=int(row["month_profit"]),
        )

    def add_product(self, draft: ProductDraft) -> Product:
        draft = self._validate_draft(draft)
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._ensure_product_capacity(connection)
                sku = self._resolve_sku(connection, draft.sku)
                cursor = connection.execute(
                    """
                    INSERT INTO products (
                        sku, name, purchase_price_cents, sale_price_cents,
                        stock_total, stock_current, sold, minimum_stock, photo_path
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        sku,
                        draft.name,
                        draft.purchase_price_cents,
                        draft.sale_price_cents,
                        draft.initial_stock,
                        draft.initial_stock,
                        draft.minimum_stock,
                        draft.photo_path,
                    ),
                )
                product_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as error:
            if "products.sku" in str(error):
                raise StockError("Ya existe un producto con ese SKU/código interno.") from error
            raise StockError("No se pudo guardar el producto.") from error
        return self._require_product(product_id)

    def import_product(self, draft: ImportedProductDraft) -> Product:
        sku = draft.sku.strip()
        name = draft.name.strip()
        if not name:
            raise StockError("El nombre del producto es obligatorio.")
        numeric_values = (
            draft.purchase_price_cents,
            draft.sale_price_cents,
            draft.stock_total,
            draft.stock_current,
            draft.sold,
            draft.minimum_stock,
        )
        if any(value < 0 for value in numeric_values):
            raise StockError("Los precios y las cantidades no pueden ser negativos.")
        if draft.stock_total != draft.stock_current + draft.sold:
            raise StockError(
                "Stock total debe ser igual a Stock actual más Vendidos."
            )
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._ensure_product_capacity(connection)
                sku = self._resolve_sku(connection, sku)
                cursor = connection.execute(
                    """
                    INSERT INTO products (
                        sku, name, purchase_price_cents, sale_price_cents,
                        stock_total, stock_current, sold, minimum_stock, photo_path
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sku,
                        name,
                        draft.purchase_price_cents,
                        draft.sale_price_cents,
                        draft.stock_total,
                        draft.stock_current,
                        draft.sold,
                        draft.minimum_stock,
                        draft.photo_path,
                    ),
                )
                product_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as error:
            if "products.sku" in str(error):
                raise StockError("Ya existe un producto con ese SKU/código interno.") from error
            raise StockError("No se pudo importar el producto.") from error
        return self._require_product(product_id)

    def update_product(self, product_id: int, draft: ProductDraft) -> Product:
        draft = self._validate_draft(draft)
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._ensure_product_unlocked(connection, product_id)
                sku = self._resolve_sku(connection, draft.sku)
                cursor = connection.execute(
                    """
                    UPDATE products
                    SET sku = ?, name = ?, purchase_price_cents = ?,
                        sale_price_cents = ?, minimum_stock = ?, photo_path = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        sku,
                        draft.name,
                        draft.purchase_price_cents,
                        draft.sale_price_cents,
                        draft.minimum_stock,
                        draft.photo_path,
                        product_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise StockError("El producto ya no existe.")
        except sqlite3.IntegrityError as error:
            if "products.sku" in str(error):
                raise StockError("Ya existe un producto con ese SKU/código interno.") from error
            raise StockError("No se pudo actualizar el producto.") from error
        return self._require_product(product_id)

    def delete_product(self, product_id: int) -> Product:
        product = self._require_product(product_id)
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM products WHERE id = ?", (product_id,))
            if cursor.rowcount != 1:
                raise StockError("El producto ya no existe.")
        return product

    def adjust_stock(self, product_id: int, new_stock: int) -> Product:
        if type(new_stock) is not int or new_stock < 0:
            raise StockError("El stock contado debe ser un entero mayor o igual a cero.")
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._ensure_product_unlocked(connection, product_id)
                product = connection.execute(
                    "SELECT sku, name, stock_current, sold FROM products WHERE id = ?",
                    (product_id,),
                ).fetchone()
                if product is None:
                    raise StockError("El producto ya no existe.")
                # Preserve stock_total = stock_current + sold, without a sale.
                total = new_stock + int(product["sold"])
                if total > 9_223_372_036_854_775_807:
                    raise StockError("El stock contado excede el máximo permitido.")
                connection.execute(
                    "UPDATE products SET stock_current = ?, stock_total = ?, "
                    "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (new_stock, total, product_id),
                )
                connection.execute(
                    "INSERT INTO stock_adjustments "
                    "(product_id, product_sku, product_name, stock_before, stock_after, difference, adjusted_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (product_id, product["sku"], product["name"], product["stock_current"],
                     new_stock, new_stock - int(product["stock_current"]), self._next_movement_timestamp(connection)),
                )
        except sqlite3.Error as error:
            raise StockError("No se pudo guardar el ajuste de stock.") from error
        return self._require_product(product_id)

    def list_stock_adjustments(self, product_id: int | None = None) -> list[dict]:
        with self._connect() as connection:
            where = "" if product_id is None else " WHERE product_id = ?"
            parameters = () if product_id is None else (product_id,)
            rows = connection.execute(
                "SELECT * FROM stock_adjustments" + where + " ORDER BY id", parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_stock_entries(self, product_id: int | None = None) -> list[dict]:
        with self._connect() as connection:
            where = "" if product_id is None else " WHERE product_id = ?"
            parameters = () if product_id is None else (product_id,)
            rows = connection.execute(
                "SELECT * FROM stock_entries" + where + " ORDER BY id", parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_sale_cancellations(self, product_id: int | None = None) -> list[dict]:
        with self._connect() as connection:
            where = "" if product_id is None else " WHERE product_id = ?"
            parameters = () if product_id is None else (product_id,)
            if product_id is None:
                rows = connection.execute(
                    """
                    SELECT sale_cancellations.*, sales.product_id,
                           sales.product_sku, sales.product_name
                    FROM sale_cancellations
                    JOIN sales ON sales.id = sale_cancellations.sale_id
                    ORDER BY sale_cancellations.id
                    """
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT sale_cancellations.*, sales.product_id,
                           sales.product_sku, sales.product_name
                    FROM sale_cancellations
                    JOIN sales ON sales.id = sale_cancellations.sale_id
                    WHERE sales.product_id = ?
                    ORDER BY sale_cancellations.id
                    """,
                    (product_id,),
                ).fetchall()
        return [dict(row) for row in rows]

    def register_entry(self, product_id: int, quantity: int) -> Product:
        if quantity <= 0:
            raise StockError("La cantidad debe ser mayor que cero.")
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._ensure_product_unlocked(connection, product_id)
                product = connection.execute(
                    "SELECT sku, name, stock_current FROM products WHERE id = ?",
                    (product_id,),
                ).fetchone()
                if product is None:
                    raise StockError("El producto ya no existe.")
                stock_before = int(product["stock_current"])
                stock_after = stock_before + quantity
                cursor = connection.execute(
                    """
                    UPDATE products
                    SET stock_total = stock_total + ?,
                        stock_current = stock_current + ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (quantity, quantity, product_id),
                )
                if cursor.rowcount != 1:
                    raise StockError("No se pudo actualizar el stock del producto.")
                connection.execute(
                    """
                    INSERT INTO stock_entries (
                        product_id, product_sku, product_name, quantity,
                        stock_before, stock_after, entry_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        product_id,
                        str(product["sku"]),
                        str(product["name"]),
                        quantity,
                        stock_before,
                        stock_after,
                        self._next_movement_timestamp(connection),
                    ),
                )
        except sqlite3.DatabaseError as error:
            raise StockError("No se pudo registrar la entrada.") from error
        return self._require_product(product_id)

    def register_sale(self, product_id: int, quantity: int) -> Product:
        if quantity <= 0:
            raise StockError("La cantidad debe ser mayor que cero.")
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._ensure_product_unlocked(connection, product_id)
                product = connection.execute(
                    """
                    SELECT sku, name, purchase_price_cents, sale_price_cents,
                           stock_current
                    FROM products
                    WHERE id = ?
                    """,
                    (product_id,),
                ).fetchone()
                if product is None:
                    raise StockError("El producto ya no existe.")
                available = int(product["stock_current"])
                if available < quantity:
                    raise StockError(
                        f"Stock insuficiente. Hay {available} unidades disponibles."
                    )
                cursor = connection.execute(
                    """
                    UPDATE products
                    SET stock_current = stock_current - ?,
                        sold = sold + ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND stock_current >= ?
                    """,
                    (quantity, quantity, product_id, quantity),
                )
                if cursor.rowcount != 1:
                    raise StockError("No se pudo actualizar el stock del producto.")
                connection.execute(
                    """
                    INSERT INTO sales (
                        product_id, product_sku, product_name, quantity,
                        purchase_price_cents, sale_price_cents,
                        stock_before, stock_after, sold_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        product_id,
                        str(product["sku"]),
                        str(product["name"]),
                        quantity,
                        int(product["purchase_price_cents"]),
                        int(product["sale_price_cents"]),
                        available,
                        available - quantity,
                        self._next_movement_timestamp(connection),
                    ),
                )
        except sqlite3.DatabaseError as error:
            raise StockError("No se pudo registrar la venta.") from error
        return self._require_product(product_id)

    def _require_product(self, product_id: int) -> Product:
        product = self.get_product(product_id)
        if product is None:
            raise StockError("El producto ya no existe.")
        return product

    def _get_plan(self, connection: sqlite3.Connection) -> AppPlan:
        return self._get_license_state(connection).status

    @staticmethod
    def _get_license_state(connection: sqlite3.Connection) -> LicenseState:
        row = connection.execute(
            """
            SELECT status, license_id, expires_at, last_validated_at
            FROM license_state
            WHERE id = 1
            """
        ).fetchone()
        if row is None:
            return LicenseState(AppPlan.FREE, None, None, None)
        try:
            status = AppPlan(str(row["status"]))
        except ValueError:
            status = AppPlan.FREE
        state = LicenseState(
            status=status,
            license_id=str(row["license_id"]) if row["license_id"] else None,
            expires_at=StockDatabase._text_to_datetime(row["expires_at"]),
            last_validated_at=StockDatabase._text_to_datetime(
                row["last_validated_at"]
            ),
        )
        if (
            state.status is AppPlan.PRO_ACTIVE
            and state.expires_at is not None
            and state.expires_at <= datetime.now(timezone.utc)
        ):
            connection.execute(
                "UPDATE license_state SET status = 'PRO_EXPIRED' WHERE id = 1"
            )
            connection.execute(
                "UPDATE app_settings SET value = 'PRO_EXPIRED' WHERE key = 'plan'"
            )
            return replace(state, status=AppPlan.PRO_EXPIRED)
        return state

    @staticmethod
    def _datetime_to_text(value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _text_to_datetime(value: object) -> datetime | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _ensure_product_capacity(self, connection: sqlite3.Connection) -> None:
        if self._get_plan(connection) is AppPlan.PRO_ACTIVE:
            return
        count = int(connection.execute("SELECT COUNT(*) FROM products").fetchone()[0])
        if count >= FREE_PRODUCT_LIMIT:
            raise PlanLimitError(FREE_PRODUCT_LIMIT_MESSAGE)

    def _locked_product_ids(self, connection: sqlite3.Connection) -> set[int]:
        if self._get_plan(connection) is AppPlan.PRO_ACTIVE:
            return set()
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(products)").fetchall()
        }
        order_by = "created_at ASC, id ASC" if "created_at" in columns else "id ASC"
        rows = connection.execute(
            f"SELECT id FROM products ORDER BY {order_by}"
        ).fetchall()
        return {int(row["id"]) for row in rows[FREE_PRODUCT_LIMIT:]}

    def _ensure_product_unlocked(
        self,
        connection: sqlite3.Connection,
        product_id: int,
    ) -> None:
        if product_id in self._locked_product_ids(connection):
            raise ProductLockedError(LOCKED_PRODUCT_MESSAGE)

    @staticmethod
    def _row_to_product(row: sqlite3.Row) -> Product:
        return Product(
            id=int(row["id"]),
            sku=str(row["sku"]),
            name=str(row["name"]),
            purchase_price_cents=int(row["purchase_price_cents"]),
            sale_price_cents=int(row["sale_price_cents"]),
            stock_total=int(row["stock_total"]),
            stock_current=int(row["stock_current"]),
            sold=int(row["sold"]),
            minimum_stock=int(row["minimum_stock"]),
            photo_path=str(row["photo_path"]) if row["photo_path"] else None,
        )

    @staticmethod
    def _row_to_sale(row: sqlite3.Row) -> Sale:
        def _int_or_none(key: str) -> int | None:
            if key not in row.keys():
                return None
            value = row[key]
            return None if value is None else int(value)

        return Sale(
            id=int(row["id"]),
            product_id=int(row["product_id"]),
            product_sku=str(row["product_sku"]),
            product_name=str(row["product_name"]),
            quantity=int(row["quantity"]),
            purchase_price_cents=int(row["purchase_price_cents"]),
            sale_price_cents=int(row["sale_price_cents"]),
            sold_at=str(row["sold_at"]),
            cancelled_at=row["cancelled_at"] if "cancelled_at" in row.keys() else None,
            stock_before=_int_or_none("stock_before"),
            stock_after=_int_or_none("stock_after"),
        )
