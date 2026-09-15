"""Read-only unified stock movement history projection."""
from datetime import datetime, timezone
from typing import Iterable

from novarix_stock.database import StockDatabase
from novarix_stock.models import StockMovement


MOVEMENT_FILTERS = {"TODOS", "ENTRADAS", "VENTAS", "AJUSTES", "ANULACIONES"}


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone()


def query_stock_movements(
    database: StockDatabase,
    movement_type: str = "TODOS",
    search: str = "",
    product_id: int | None = None,
) -> list[StockMovement]:
    """Return a unified, sorted list of stock movements.

    Sources:
    - stock_entries   -> ENTRADA
    - sales           -> VENTA (non-cancelled)
    - stock_adjustments -> AJUSTE
    - sale_cancellations -> ANULACIÓN DE VENTA

    Historical SKU/name snapshots are used so the history survives product
    deletion. Results are ordered by timestamp descending, id descending.
    """
    if movement_type not in MOVEMENT_FILTERS:
        raise ValueError("Filtro de movimiento desconocido")

    term = search.strip().casefold()
    rows: list[StockMovement] = []

    if movement_type in ("TODOS", "ENTRADAS"):
        for row in database.list_stock_entries(product_id):
            rows.append(
                StockMovement(
                    id=int(row["id"]),
                    product_id=int(row["product_id"]),
                    product_sku=str(row["product_sku"]),
                    product_name=str(row["product_name"]),
                    movement_type="ENTRADA",
                    quantity=int(row["quantity"]),
                    stock_before=int(row["stock_before"]),
                    stock_after=int(row["stock_after"]),
                    detail="Entrada de mercadería",
                    timestamp=_parse_timestamp(str(row["entry_at"])),
                )
            )

    if movement_type in ("TODOS", "VENTAS"):
        for sale in database.list_sales(product_id):
            rows.append(
                StockMovement(
                    id=sale.id,
                    product_id=sale.product_id,
                    product_sku=sale.product_sku,
                    product_name=sale.product_name,
                    movement_type="VENTA",
                    quantity=sale.quantity,
                    stock_before=sale.stock_before,
                    stock_after=sale.stock_after,
                    detail="Venta registrada",
                    timestamp=_parse_timestamp(sale.sold_at),
                )
            )

    if movement_type in ("TODOS", "AJUSTES"):
        for row in database.list_stock_adjustments(product_id):
            rows.append(
                StockMovement(
                    id=int(row["id"]),
                    product_id=int(row["product_id"]),
                    product_sku=str(row["product_sku"]),
                    product_name=str(row["product_name"]),
                    movement_type="AJUSTE",
                    quantity=int(row["difference"]),
                    stock_before=int(row["stock_before"]),
                    stock_after=int(row["stock_after"]),
                    detail="Ajuste de stock",
                    timestamp=_parse_timestamp(str(row["adjusted_at"])),
                )
            )

    if movement_type in ("TODOS", "ANULACIONES"):
        for row in database.list_sale_cancellations(product_id):
            reason = str(row.get("reason") or "").strip()
            detail = (
                f"Anulación de venta: {reason}" if reason else "Anulación de venta"
            )
            rows.append(
                StockMovement(
                    id=int(row["id"]),
                    product_id=int(row["product_id"]),
                    product_sku=str(row["product_sku"]),
                    product_name=str(row["product_name"]),
                    movement_type="ANULACIÓN DE VENTA",
                    quantity=int(row["quantity"]),
                    stock_before=row.get("stock_before"),
                    stock_after=row.get("stock_after"),
                    detail=detail,
                    timestamp=_parse_timestamp(str(row["cancelled_at"])),
                )
            )

    if term:
        rows = [
            movement
            for movement in rows
            if term in movement.product_sku.casefold()
            or term in movement.product_name.casefold()
        ]

    return sorted(rows, key=lambda m: (m.timestamp, m.id), reverse=True)


def summarize_movements(movements: Iterable[StockMovement]) -> dict[str, int]:
    """Simple counters for the movement history metrics bar."""
    total = 0
    counts: dict[str, int] = {
        "ENTRADA": 0,
        "VENTA": 0,
        "AJUSTE": 0,
        "ANULACIÓN DE VENTA": 0,
    }
    for movement in movements:
        total += 1
        counts[movement.movement_type] += movement.quantity
    return {"total": total, **counts}
