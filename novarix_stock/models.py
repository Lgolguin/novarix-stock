from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class AppPlan(StrEnum):
    FREE = "FREE"
    PRO_ACTIVE = "PRO_ACTIVE"
    PRO = "PRO_ACTIVE"
    PRO_EXPIRED = "PRO_EXPIRED"


@dataclass(frozen=True, slots=True)
class LicenseState:
    status: AppPlan
    license_id: str | None
    expires_at: datetime | None
    last_validated_at: datetime | None


@dataclass(frozen=True, slots=True)
class Product:
    id: int
    sku: str
    name: str
    purchase_price_cents: int
    sale_price_cents: int
    stock_total: int
    stock_current: int
    sold: int
    minimum_stock: int
    photo_path: str | None

    @property
    def needs_restock(self) -> bool:
        return self.stock_current <= self.minimum_stock


@dataclass(frozen=True, slots=True)
class Sale:
    id: int
    product_id: int
    product_sku: str
    product_name: str
    quantity: int
    purchase_price_cents: int
    sale_price_cents: int
    sold_at: str
    cancelled_at: str | None = None
    stock_before: int | None = None
    stock_after: int | None = None

    @property
    def is_cancelled(self) -> bool:
        return self.cancelled_at is not None

    @property
    def invoiced_cents(self) -> int:
        return self.quantity * self.sale_price_cents

    @property
    def cost_cents(self) -> int:
        return self.quantity * self.purchase_price_cents

    @property
    def profit_cents(self) -> int:
        return self.invoiced_cents - self.cost_cents


@dataclass(frozen=True, slots=True)
class StockMovement:
    id: int
    product_id: int
    product_sku: str
    product_name: str
    movement_type: str
    quantity: int
    stock_before: int | None
    stock_after: int | None
    detail: str
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class ProductDraft:
    sku: str
    name: str
    purchase_price_cents: int
    sale_price_cents: int
    minimum_stock: int
    photo_path: str | None = None
    initial_stock: int = 0


@dataclass(frozen=True, slots=True)
class ImportedProductDraft:
    sku: str
    name: str
    purchase_price_cents: int
    sale_price_cents: int
    stock_total: int
    stock_current: int
    sold: int
    minimum_stock: int
    photo_path: str | None = None
