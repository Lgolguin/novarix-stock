"""Read-only history projection using persisted sale snapshots, never products."""
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable

from novarix_stock.database import StockDatabase
from novarix_stock.models import Sale


def local_sale_time(sale: Sale) -> datetime:
    value = datetime.fromisoformat(sale.sold_at)
    if value.tzinfo is None:  # SQLite CURRENT_TIMESTAMP is UTC.
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone()


@dataclass(frozen=True)
class HistorySummary:
    invoiced_cents: int
    cost_cents: int
    profit_cents: int
    units: int


def summarize_sales(sales: Iterable[Sale]) -> HistorySummary:
    rows = [sale for sale in sales if not sale.is_cancelled]
    return HistorySummary(sum(s.invoiced_cents for s in rows),
                          sum(s.cost_cents for s in rows),
                          sum(s.profit_cents for s in rows),
                          sum(s.quantity for s in rows))


def query_history(database: StockDatabase, search: str = '', period: str = 'TODAS',
                  *, today: date | None = None) -> list[Sale]:
    today = today or date.today()
    term = search.strip().casefold()
    if period not in {'TODAS', 'HOY', 'ESTE MES'}:
        raise ValueError('Filtro de fecha desconocido')
    rows = []
    for sale in database.list_sales():
        if term and term not in sale.product_sku.casefold() and term not in sale.product_name.casefold():
            continue
        sold_on = local_sale_time(sale).date()
        if period == 'HOY' and sold_on != today:
            continue
        if period == 'ESTE MES' and (sold_on.year, sold_on.month) != (today.year, today.month):
            continue
        rows.append(sale)
    return sorted(rows, key=lambda s: (local_sale_time(s), s.id), reverse=True)


def format_history_money(cents: int) -> str:
    # Integer-only formatting keeps the exact persisted cents.
    whole, fraction = divmod(abs(cents), 100)
    return f"$ {'-' if cents < 0 else ''}{format(whole, ',').replace(',', '.')},{fraction:02d}"
