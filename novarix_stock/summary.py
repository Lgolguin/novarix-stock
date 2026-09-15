from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from novarix_stock.models import Product


@dataclass(frozen=True, slots=True)
class StockSummary:
    units_in_stock: int
    units_sold: int
    products_to_restock: int
    current_stock_value_cents: int


@dataclass(frozen=True, slots=True)
class FinancialSummary:
    total_invoiced_cents: int
    total_cost_cents: int
    total_profit_cents: int
    today_invoiced_cents: int
    today_profit_cents: int
    month_invoiced_cents: int
    month_profit_cents: int


@dataclass(frozen=True, slots=True)
class SalesSummary:
    units_sold: int
    invoiced_cents: int
    cost_cents: int
    profit_cents: int


def calculate_stock_summary(products: Iterable[Product]) -> StockSummary:
    units_in_stock = 0
    units_sold = 0
    products_to_restock = 0
    current_stock_value_cents = 0

    for product in products:
        units_in_stock += product.stock_current
        units_sold += product.sold
        products_to_restock += int(product.needs_restock)
        current_stock_value_cents += product.stock_current * product.purchase_price_cents

    return StockSummary(
        units_in_stock=units_in_stock,
        units_sold=units_sold,
        products_to_restock=products_to_restock,
        current_stock_value_cents=current_stock_value_cents,
    )
