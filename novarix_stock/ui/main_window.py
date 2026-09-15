from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from novarix_stock.backup import BackupError, create_backup, restore_backup, suggested_backup_name, validate_backup
from novarix_stock.config import APP_DIR, IMAGE_DIR
from novarix_stock.database import (
    FREE_PRODUCT_LIMIT_MESSAGE,
    LOCKED_PRODUCT_MESSAGE,
    PlanLimitError,
    StockDatabase,
    StockError,
)
from novarix_stock.excel_io import ExcelFileError, ExcelImportResult, StockExcelService
from novarix_stock.images import ProductImageStore
from novarix_stock.models import AppPlan, Product, ProductDraft
from novarix_stock.summary import FinancialSummary, StockSummary, calculate_stock_summary
from novarix_stock.ui.dialogs import ProductDialog, QuantityDialog, StockAdjustmentDialog
from novarix_stock.ui.theme import APP_STYLESHEET
from novarix_stock.ui.sales_history_window import SalesHistoryWindow
from novarix_stock.ui.stock_movement_history_window import StockMovementHistoryWindow


class MainWindow(QMainWindow):
    PHOTO, SKU, NAME, PURCHASE, SALE, TOTAL, CURRENT, SOLD, MINIMUM, STATUS = range(10)

    def __init__(self, database: StockDatabase) -> None:
        super().__init__()
        self.database = database
        self.history_window = None
        self.movement_history_window = None
        self.image_store = ProductImageStore(IMAGE_DIR)
        self.excel_service = StockExcelService(self.database, self.image_store)
        self.products_by_id: dict[int, Product] = {}
        self.locked_product_ids: set[int] = set()
        self.setWindowTitle("STOCK by NOVARIX")
        self.resize(1440, 820)
        self.setMinimumSize(1120, 680)
        self.setStyleSheet(APP_STYLESHEET)
        self._build_ui()
        self._refresh_table()

    def _open_sales_history(self) -> None:
        if self.history_window is None:
            self.history_window = SalesHistoryWindow(self.database, self)
            self.history_window.sale_cancelled.connect(self._refresh_after_cancellation)
        else:
            self.history_window.refresh()
        self.history_window.show()
        self.history_window.raise_()
        self.history_window.activateWindow()

    def _open_stock_movement_history(self) -> None:
        product = self._selected_product() if self.table.selectedItems() else None
        if self.movement_history_window is None:
            self.movement_history_window = StockMovementHistoryWindow(
                self.database, product.id if product else None, self
            )
        else:
            self.movement_history_window.product_id = product.id if product else None
            self.movement_history_window.only_product_checkbox.setChecked(product is not None)
            self.movement_history_window.only_product_checkbox.setEnabled(product is not None)
            self.movement_history_window.refresh()
        self.movement_history_window.show()
        self.movement_history_window.raise_()
        self.movement_history_window.activateWindow()

    def _refresh_after_cancellation(self) -> None:
        product = self._selected_product() if self.table.selectedItems() else None
        self._refresh_table(product.id if product else None)

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("appRoot")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(12)

        header_card = QFrame()
        header_card.setObjectName("headerCard")
        header_card.setFixedHeight(94)
        self._add_shadow(header_card, QColor(22, 197, 255, 42), 28, 4)
        header = QHBoxLayout(header_card)
        header.setContentsMargins(22, 16, 22, 16)
        header.setSpacing(15)

        brand_logo = QLabel()
        brand_logo.setObjectName("brandLogo")
        brand_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_logo.setFixedSize(56, 56)
        logo_pixmap = QPixmap(str(APP_DIR / "assets" / "stock-icon-s.png"))
        if not logo_pixmap.isNull():
            brand_logo.setPixmap(
                logo_pixmap.scaled(
                    48,
                    48,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        header.addWidget(brand_logo)

        titles = QVBoxLayout()
        titles.setSpacing(1)
        titles.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        brand_line = QHBoxLayout()
        brand_line.setSpacing(8)
        brand = QLabel("STOCK")
        brand.setObjectName("brand")
        brand_suffix = QLabel("by NOVARIX")
        brand_suffix.setObjectName("brandSuffix")
        brand_line.addWidget(brand)
        brand_line.addWidget(brand_suffix)
        brand_line.addStretch()
        subtitle = QLabel("Control de inventario local")
        subtitle.setObjectName("subtitle")
        titles.addLayout(brand_line)
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch()

        self.plan_label = QLabel("PLAN FREE")
        self.plan_label.setObjectName("planBadge")
        self.plan_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.plan_label)

        self.pro_button = QPushButton("DESBLOQUEAR PRO")
        self.pro_button.setObjectName("proButton")
        self.pro_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pro_button.clicked.connect(self._unlock_pro)
        header.addWidget(self.pro_button)

        self.counter_label = QLabel()
        self.counter_label.setObjectName("counter")
        self.counter_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.counter_label)
        root.addWidget(header_card)

        metrics = QHBoxLayout()
        metrics.setSpacing(11)
        self.units_in_stock_value = self._add_metric_card(
            metrics, "UNIDADES EN STOCK", "metricCyan"
        )
        self.units_sold_value = self._add_metric_card(
            metrics, "UNIDADES VENDIDAS", "metricBlue"
        )
        self.restock_products_value = self._add_metric_card(
            metrics, "PRODUCTOS EN REPOSICIÓN", "metricViolet"
        )
        self.stock_value_value = self._add_metric_card(
            metrics, "VALOR ACTUAL DEL STOCK", "metricCyan"
        )
        root.addLayout(metrics)

        financial_metrics = QHBoxLayout()
        financial_metrics.setSpacing(8)
        self.total_invoiced_value = self._add_metric_card(
            financial_metrics, "FACTURACIÓN TOTAL", "metricCyan"
        )
        self.total_cost_value = self._add_metric_card(
            financial_metrics, "COSTO TOTAL VENDIDO", "metricBlue"
        )
        self.total_profit_value = self._add_metric_card(
            financial_metrics, "GANANCIA TOTAL", "metricViolet"
        )
        self.today_invoiced_value = self._add_metric_card(
            financial_metrics, "FACTURADO HOY", "metricCyan"
        )
        self.today_profit_value = self._add_metric_card(
            financial_metrics, "GANANCIA HOY", "metricViolet"
        )
        self.month_invoiced_value = self._add_metric_card(
            financial_metrics, "FACTURADO ESTE MES", "metricBlue"
        )
        self.month_profit_value = self._add_metric_card(
            financial_metrics, "GANANCIA ESTE MES", "metricViolet"
        )
        root.addLayout(financial_metrics)

        self.product_summary_card = QFrame()
        self.product_summary_card.setObjectName("metricCard")
        self.product_summary_card.setProperty("accent", "metricViolet")
        selected_layout = QVBoxLayout(self.product_summary_card)
        selected_layout.setContentsMargins(14, 9, 14, 9)
        selected_layout.setSpacing(4)
        self.product_summary_title = QLabel("RESUMEN DEL PRODUCTO SELECCIONADO")
        self.product_summary_title.setObjectName("metricTitle")
        self.product_summary_identity = QLabel()
        self.product_summary_identity.setTextFormat(Qt.TextFormat.PlainText)
        self.product_summary_identity.setWordWrap(True)
        self.product_summary_values = QLabel()
        self.product_summary_values.setWordWrap(True)
        selected_layout.addWidget(self.product_summary_title)
        selected_layout.addWidget(self.product_summary_identity)
        selected_layout.addWidget(self.product_summary_values)
        root.addWidget(self.product_summary_card)

        action_bar = QFrame()
        action_bar.setObjectName("actionBar")
        actions = QHBoxLayout(action_bar)
        actions.setContentsMargins(10, 8, 10, 8)
        actions.setSpacing(8)
        self.add_button = QPushButton("+  Agregar producto")
        self.add_button.setObjectName("primaryButton")
        self.edit_button = QPushButton("Editar")
        self.entry_button = QPushButton("Entrada de mercadería")
        self.sale_button = QPushButton("Registrar venta")
        self.sale_button.setObjectName("saleButton")
        self.delete_button = QPushButton("Eliminar")
        self.delete_button.setObjectName("dangerButton")
        self.add_button.clicked.connect(self._add_product)
        self.edit_button.clicked.connect(self._edit_product)
        self.entry_button.clicked.connect(self._register_entry)
        self.sale_button.clicked.connect(self._register_sale)
        self.delete_button.clicked.connect(self._delete_product)
        for button in (
            self.add_button,
            self.edit_button,
            self.entry_button,
            self.sale_button,
            self.delete_button,
        ):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            actions.addWidget(button)
        actions.addStretch()
        self.import_button = QPushButton("Importar Excel")
        self.export_button = QPushButton("Exportar Excel")
        self.import_button.setObjectName("excelButton")
        self.export_button.setObjectName("excelButton")
        self.import_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.export_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.import_button.clicked.connect(self._import_excel)
        self.export_button.clicked.connect(self._export_excel)
        actions.addWidget(self.import_button)
        actions.addWidget(self.export_button)

        self.backup_button = QPushButton("Crear backup")
        self.restore_button = QPushButton("Restaurar backup")
        self.backup_button.setObjectName("excelButton")
        self.restore_button.setObjectName("excelButton")
        self.backup_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.restore_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.backup_button.clicked.connect(self._create_backup)
        self.restore_button.clicked.connect(self._restore_backup)
        actions.addWidget(self.backup_button)
        actions.addWidget(self.restore_button)
        root.addWidget(action_bar)

        table_card = QFrame()
        table_card.setObjectName("tableCard")
        self._add_shadow(table_card, QColor(0, 0, 0, 90), 22, 5)
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(1, 1, 1, 1)
        table_layout.setSpacing(0)

        table_heading = QWidget()
        table_heading.setObjectName("tableHeading")
        table_heading_layout = QHBoxLayout(table_heading)
        table_heading_layout.setContentsMargins(17, 11, 17, 10)
        table_title = QLabel("Inventario")
        table_title.setObjectName("tableTitle")
        table_hint = QLabel("Seleccioná una fila para operar  •  Doble clic para editar")
        table_hint.setObjectName("tableHint")
        table_heading_layout.addWidget(table_title)
        table_heading_layout.addStretch()
        table_heading_layout.addWidget(table_hint)
        self.adjust_stock_button = QPushButton("AJUSTAR STOCK")
        self.adjust_stock_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.adjust_stock_button.clicked.connect(self._adjust_stock)
        table_heading_layout.addWidget(self.adjust_stock_button)
        self.history_button = QPushButton("HISTORIAL DE VENTAS")
        self.history_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.history_button.clicked.connect(self._open_sales_history)
        table_heading_layout.addWidget(self.history_button)
        self.movement_history_button = QPushButton("HISTORIAL DE MOVIMIENTOS")
        self.movement_history_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.movement_history_button.clicked.connect(self._open_stock_movement_history)
        table_heading_layout.addWidget(self.movement_history_button)
        table_layout.addWidget(table_heading)

        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            [
                "FOTO", "SKU", "PRODUCTO", "COMPRA", "VENTA",
                "STOCK TOTAL", "STOCK ACTUAL", "VENDIDOS", "MÍNIMO", "ESTADO",
            ]
        )
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setIconSize(QSize(48, 48))
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(72)
        self.table.itemSelectionChanged.connect(self._update_action_state)
        self.table.itemSelectionChanged.connect(self._refresh_selected_summary)
        self.table.itemDoubleClicked.connect(lambda _item: self._edit_product())

        header_view = self.table.horizontalHeader()
        header_view.setFixedHeight(44)
        header_view.setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        header_view.setStretchLastSection(False)
        header_view.setMinimumSectionSize(68)
        for column in range(self.table.columnCount()):
            header_view.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        header_view.setSectionResizeMode(self.NAME, QHeaderView.ResizeMode.Stretch)
        column_widths = {
            self.PHOTO: 74,
            self.SKU: 106,
            self.PURCHASE: 116,
            self.SALE: 116,
            self.TOTAL: 106,
            self.CURRENT: 110,
            self.SOLD: 94,
            self.MINIMUM: 92,
            self.STATUS: 164,
        }
        for column, width in column_widths.items():
            self.table.setColumnWidth(column, width)
        table_layout.addWidget(self.table)
        root.addWidget(table_card, 1)
        self._update_action_state()

    @staticmethod
    def _add_metric_card(layout: QHBoxLayout, title: str, accent: str) -> QLabel:
        card = QFrame()
        card.setObjectName("metricCard")
        card.setProperty("accent", accent)
        card.setFixedHeight(86)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(17, 11, 17, 11)
        card_layout.setSpacing(3)
        title_row = QHBoxLayout()
        title_row.setSpacing(7)
        accent_dot = QFrame()
        accent_dot.setObjectName("metricDot")
        accent_dot.setProperty("accent", accent)
        accent_dot.setFixedSize(6, 6)
        title_label = QLabel(title)
        title_label.setObjectName("metricTitle")
        value_label = QLabel("0")
        value_label.setObjectName("metricValue")
        title_row.addWidget(accent_dot)
        title_row.addWidget(title_label)
        title_row.addStretch()
        card_layout.addLayout(title_row)
        card_layout.addWidget(value_label)
        layout.addWidget(card, 1)
        return value_label

    @staticmethod
    def _add_shadow(widget: QWidget, color: QColor, blur: int, y_offset: int) -> None:
        shadow = QGraphicsDropShadowEffect(widget)
        shadow.setBlurRadius(blur)
        shadow.setColor(color)
        shadow.setOffset(0, y_offset)
        widget.setGraphicsEffect(shadow)

    def _refresh_table(self, selected_product_id: int | None = None) -> None:
        products = self.database.list_products()
        self.products_by_id = {product.id: product for product in products}
        self.locked_product_ids = self.database.get_locked_product_ids()
        self.table.setRowCount(len(products))
        self.table.setUpdatesEnabled(False)
        selected_row = -1
        for row, product in enumerate(products):
            if product.id == selected_product_id:
                selected_row = row
            self._fill_row(row, product)
        self.table.setUpdatesEnabled(True)

        restock_count = sum(product.needs_restock for product in products)
        suffix = f"  •  {restock_count} para reponer" if restock_count else ""
        self.counter_label.setText(f"{len(products)} productos{suffix}")
        self._refresh_plan_state()
        self._refresh_summary(calculate_stock_summary(products))
        self._refresh_financial_summary(self.database.get_financial_summary())
        if selected_row >= 0:
            self.table.selectRow(selected_row)
            self.table.scrollToItem(self.table.item(selected_row, self.NAME))
        else:
            self.table.clearSelection()
        self._update_action_state()
        self._refresh_selected_summary()

    def _refresh_summary(self, summary: StockSummary) -> None:
        self.units_in_stock_value.setText(self._format_count(summary.units_in_stock))
        self.units_sold_value.setText(self._format_count(self.database.get_sales_summary().units_sold))
        self.restock_products_value.setText(self._format_count(summary.products_to_restock))
        self.stock_value_value.setText(self._format_money(summary.current_stock_value_cents))

    def _refresh_financial_summary(self, summary: FinancialSummary) -> None:
        self.total_invoiced_value.setText(self._format_money(summary.total_invoiced_cents))
        self.total_cost_value.setText(self._format_money(summary.total_cost_cents))
        self.total_profit_value.setText(self._format_money(summary.total_profit_cents))
        self.today_invoiced_value.setText(self._format_money(summary.today_invoiced_cents))
        self.today_profit_value.setText(self._format_money(summary.today_profit_cents))
        self.month_invoiced_value.setText(self._format_money(summary.month_invoiced_cents))
        self.month_profit_value.setText(self._format_money(summary.month_profit_cents))

    def _refresh_selected_summary(self) -> None:
        product = self._selected_product() if self.table.selectedItems() else None
        if product is None:
            self.product_summary_identity.setText("Seleccioná un producto para ver su resumen.")
            self.product_summary_values.clear()
            return
        summary = self.database.get_sales_summary(product.id)
        self.product_summary_identity.setText(f"{product.name}  •  SKU: {product.sku}")
        self.product_summary_values.setText(
            f"Stock actual: {self._format_count(product.stock_current)}   •   "
            f"Vendidos: {self._format_count(summary.units_sold)}   •   "
            f"Facturado: {self._format_money(summary.invoiced_cents)}   •   "
            f"Costo vendido: {self._format_money(summary.cost_cents)}   •   "
            f"Ganancia: {self._format_money(summary.profit_cents)}"
        )

    def _fill_row(self, row: int, product: Product) -> None:
        is_locked = product.id in self.locked_product_ids
        photo_item = QTableWidgetItem()
        photo_item.setData(Qt.ItemDataRole.UserRole, product.id)
        photo_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        sku_item = self._text_item(product.sku, left=True)
        sku_item.setForeground(QColor("#8ea3c2"))
        sku_item.setFont(QFont("Consolas", 9))
        name_item = self._text_item(product.name, left=True)
        name_font = QFont("Segoe UI", 10)
        name_font.setBold(True)
        name_item.setFont(name_font)
        name_item.setForeground(QColor("#f5f8ff"))
        purchase_item = self._text_item(self._format_money(product.purchase_price_cents), right=True)
        purchase_item.setForeground(QColor("#9cabc2"))
        sale_item = self._text_item(self._format_money(product.sale_price_cents), right=True)
        sale_item.setForeground(QColor("#69dfff"))
        current_item = self._number_item(product.stock_current)
        current_font = QFont("Segoe UI", 10)
        current_font.setBold(True)
        current_item.setFont(current_font)
        current_item.setForeground(QColor("#f4f8ff"))

        values = [
            photo_item,
            sku_item,
            name_item,
            purchase_item,
            sale_item,
            self._number_item(product.stock_total),
            current_item,
            self._number_item(product.sold),
            self._number_item(product.minimum_stock),
            self._status_item(product, is_locked),
        ]
        for column, item in enumerate(values):
            self.table.setItem(row, column, item)

        if is_locked:
            for column in range(self.table.columnCount()):
                item = self.table.item(row, column)
                item.setBackground(QColor("#171724"))
                if column != self.STATUS:
                    item.setForeground(QColor("#6f7890"))
        elif product.needs_restock:
            for column in range(self.table.columnCount()):
                item = self.table.item(row, column)
                item.setBackground(QColor("#181321"))

        self.table.setCellWidget(row, self.PHOTO, self._photo_cell(product))
        self.table.setCellWidget(row, self.STATUS, self._status_badge(product, is_locked))

    @staticmethod
    def _text_item(text: str, left: bool = False, right: bool = False) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        if left:
            alignment = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        elif right:
            alignment = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight
        else:
            alignment = Qt.AlignmentFlag.AlignCenter
        item.setTextAlignment(alignment)
        return item

    @staticmethod
    def _photo_cell(product: Product) -> QWidget:
        container = QWidget()
        container.setObjectName("photoCell")
        container.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(12, 8, 12, 8)
        label = QLabel()
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setFixedSize(48, 48)
        if product.photo_path and Path(product.photo_path).is_file():
            label.setObjectName("productPhoto")
            label.setPixmap(MainWindow._rounded_thumbnail(product.photo_path, 48))
        else:
            label.setObjectName("photoPlaceholder")
            label.setText(product.name[:1].upper() if product.name else "N")
        layout.addWidget(label)
        return container

    @staticmethod
    def _rounded_thumbnail(photo_path: str, size: int) -> QPixmap:
        source = QPixmap(photo_path)
        if source.isNull():
            return QPixmap()
        scaled = source.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        result = QPixmap(size, size)
        result.fill(Qt.GlobalColor.transparent)
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, size, size, 10, 10)
        painter.setClipPath(path)
        x_offset = (scaled.width() - size) // 2
        y_offset = (scaled.height() - size) // 2
        painter.drawPixmap(-x_offset, -y_offset, scaled)
        painter.end()
        return result

    @staticmethod
    def _status_badge(product: Product, is_locked: bool = False) -> QWidget:
        state = "locked" if is_locked else "restock" if product.needs_restock else "ok"
        container = QWidget()
        container.setObjectName("statusContainer")
        container.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(11, 18, 11, 18)
        layout.setSpacing(6)
        badge = QFrame()
        badge.setObjectName("statusBadge")
        badge.setProperty("state", state)
        badge_layout = QHBoxLayout(badge)
        badge_layout.setContentsMargins(9, 4, 9, 4)
        badge_layout.setSpacing(6)
        dot = QFrame()
        dot.setObjectName("statusDot")
        dot.setProperty("state", state)
        dot.setFixedSize(6, 6)
        text = QLabel(
            "BLOQUEADO — PRO"
            if is_locked
            else "REPOSICIÓN"
            if product.needs_restock
            else "OK"
        )
        text.setObjectName("statusText")
        text.setProperty("state", state)
        badge_layout.addWidget(dot)
        badge_layout.addWidget(text)
        layout.addWidget(badge)
        return container

    @classmethod
    def _number_item(cls, number: int) -> QTableWidgetItem:
        item = cls._text_item(f"{number:,}".replace(",", "."))
        item.setData(Qt.ItemDataRole.DisplayRole, number)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        return item

    @classmethod
    def _status_item(cls, product: Product, is_locked: bool = False) -> QTableWidgetItem:
        if is_locked:
            item = cls._text_item("BLOQUEADO — PRO")
            item.setForeground(QColor("#b199ff"))
        elif product.needs_restock:
            item = cls._text_item("●  REPOSICIÓN")
            item.setForeground(QColor("#ff7191"))
        else:
            item = cls._text_item("●  OK")
            item.setForeground(QColor("#63e6b5"))
        return item

    @staticmethod
    def _format_money(cents: int) -> str:
        value = cents / 100
        whole, decimal = f"{value:,.2f}".split(".")
        return f"$ {whole.replace(',', '.')},{decimal}"

    @staticmethod
    def _format_count(value: int) -> str:
        return f"{value:,}".replace(",", ".")

    def _selected_product(self) -> Product | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, self.PHOTO)
        if item is None:
            return None
        return self.products_by_id.get(int(item.data(Qt.ItemDataRole.UserRole)))

    def _update_action_state(self) -> None:
        product = self._selected_product()
        selected = product is not None
        can_operate = selected and product.id not in self.locked_product_ids
        for button in (self.edit_button, self.entry_button, self.sale_button):
            button.setEnabled(can_operate)
        self.delete_button.setEnabled(selected)
        # With no selection the button remains available to explain what is needed.
        actual_selection = bool(self.table.selectedItems())
        self.adjust_stock_button.setEnabled(not actual_selection or can_operate)

    def _add_product(self) -> None:
        if not self.database.can_add_product():
            self._show_plan_limit()
            return
        dialog = ProductDialog(self)
        if dialog.exec() != ProductDialog.DialogCode.Accepted:
            return
        draft = dialog.draft()
        imported_photo = None
        try:
            if draft.photo_path:
                imported_photo = self.image_store.import_image(draft.photo_path)
                draft = self._with_photo(draft, imported_photo)
            product = self.database.add_product(draft)
        except PlanLimitError:
            self.image_store.delete_managed_image(imported_photo)
            self._show_plan_limit()
            return
        except (StockError, OSError) as error:
            self.image_store.delete_managed_image(imported_photo)
            self._show_error(str(error))
            return
        self._refresh_table(product.id)

    def _edit_product(self) -> None:
        product = self._selected_product()
        if product is None:
            return
        if not self._can_operate_product(product):
            return
        dialog = ProductDialog(self, product)
        if dialog.exec() != ProductDialog.DialogCode.Accepted:
            return
        draft = dialog.draft()
        old_photo = product.photo_path
        imported_photo = None
        try:
            if draft.photo_path and draft.photo_path != old_photo:
                imported_photo = self.image_store.import_image(draft.photo_path)
                draft = self._with_photo(draft, imported_photo)
            updated = self.database.update_product(product.id, draft)
        except (StockError, OSError) as error:
            self.image_store.delete_managed_image(imported_photo)
            self._show_error(str(error))
            return
        if old_photo != updated.photo_path:
            self.image_store.delete_managed_image(old_photo)
        self._refresh_table(updated.id)

    def _adjust_stock(self) -> None:
        product = self._selected_product() if self.table.selectedItems() else None
        if product is None:
            QMessageBox.information(self, "Ajustar stock", "Seleccioná un producto para ajustar su stock.")
            return
        if not self._can_operate_product(product):
            return
        dialog = StockAdjustmentDialog(product, self)
        if dialog.exec() != StockAdjustmentDialog.DialogCode.Accepted:
            return
        try:
            updated = self.database.adjust_stock(product.id, dialog.new_stock())
        except StockError as error:
            self._show_error(str(error))
            return
        self._refresh_table(updated.id)

    def _register_entry(self) -> None:
        product = self._selected_product()
        if product is None:
            return
        if not self._can_operate_product(product):
            return
        dialog = QuantityDialog(
            "Entrada de mercadería", product.name, "Registrar entrada", self
        )
        if dialog.exec() != QuantityDialog.DialogCode.Accepted:
            return
        try:
            updated = self.database.register_entry(product.id, dialog.quantity())
        except StockError as error:
            self._show_error(str(error))
            return
        self._refresh_table(updated.id)

    def _register_sale(self) -> None:
        product = self._selected_product()
        if product is None:
            return
        if not self._can_operate_product(product):
            return
        if product.stock_current <= 0:
            self._show_error("No hay stock disponible para registrar una venta.")
            return
        dialog = QuantityDialog(
            "Registrar venta",
            f"{product.name}  •  Disponible: {product.stock_current}",
            "Confirmar venta",
            self,
            maximum=product.stock_current,
        )
        if dialog.exec() != QuantityDialog.DialogCode.Accepted:
            return
        try:
            updated = self.database.register_sale(product.id, dialog.quantity())
        except StockError as error:
            self._show_error(str(error))
            return
        self._refresh_table(updated.id)

    def _delete_product(self) -> None:
        product = self._selected_product()
        if product is None:
            return
        result = QMessageBox.question(
            self,
            "Eliminar producto",
            f"¿Eliminar “{product.name}”?\n\nEsta acción también quitará su stock guardado.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if result != QMessageBox.StandardButton.Yes:
            return
        try:
            deleted = self.database.delete_product(product.id)
        except StockError as error:
            self._show_error(str(error))
            return
        self.image_store.delete_managed_image(deleted.photo_path)
        self._refresh_table()

    def _import_excel(self) -> None:
        if not self.database.can_add_product():
            self._show_plan_limit()
            return
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Importar productos desde Excel",
            "",
            "Archivos Excel (*.xlsx)",
        )
        if not filename:
            return
        try:
            result = self.excel_service.import_file(filename)
        except ExcelFileError as error:
            self._show_error(str(error))
            return
        self._refresh_table()
        self._show_import_result(result)

    def _export_excel(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Exportar stock a Excel",
            "stock_novarix.xlsx",
            "Archivos Excel (*.xlsx)",
        )
        if not filename:
            return
        try:
            self.excel_service.export_file(filename)
        except ExcelFileError as error:
            self._show_error(str(error))
            return
        destination = str(Path(filename).with_suffix(".xlsx"))
        QMessageBox.information(
            self,
            "Exportación completa",
            f"Se exportaron {len(self.products_by_id)} productos.\n\n{destination}",
        )

    def _create_backup(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Crear backup de STOCK",
            suggested_backup_name(),
            "Archivos ZIP (*.zip)",
        )
        if not filename:
            return
        try:
            create_backup(
                self.database.database_path,
                self.database.database_path.parent / "product_images",
                filename,
            )
        except BackupError as error:
            self._show_error(str(error))
            return
        QMessageBox.information(
            self,
            "Backup creado",
            f"Se guardó el backup correctamente.\n\n{filename}",
        )

    def _restore_backup(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Restaurar backup de STOCK",
            "",
            "Archivos ZIP (*.zip)",
        )
        if not filename:
            return
        try:
            validate_backup(Path(filename))
        except BackupError as error:
            self._show_error(str(error))
            return

        reply = QMessageBox.warning(
            self,
            "Restaurar backup",
            "La restauración reemplazará los datos actuales de inventario.\n\n"
            "Se creará automáticamente un backup de seguridad de los datos actuales antes de continuar.\n\n"
            "¿Deseás continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            restore_backup(Path(filename), self.database.database_path.parent)
        except BackupError as error:
            self._show_error(str(error))
            return

        self._refresh_table()
        QMessageBox.information(
            self,
            "Restauración completada",
            "Los datos fueron restaurados correctamente.",
        )

    def _show_import_result(self, result: ExcelImportResult) -> None:
        lines = [f"Productos importados: {result.imported_count}"]
        if result.duplicate_skus:
            duplicates = ", ".join(result.duplicate_skus[:10])
            if len(result.duplicate_skus) > 10:
                duplicates += f" y {len(result.duplicate_skus) - 10} más"
            lines.append(f"SKU duplicados omitidos: {duplicates}")
        if result.row_errors:
            lines.append("")
            lines.append("Filas no importadas:")
            lines.extend(result.row_errors[:10])
            if len(result.row_errors) > 10:
                lines.append(f"… y {len(result.row_errors) - 10} errores más.")
        if result.plan_limit_reached:
            lines.append("")
            lines.append(FREE_PRODUCT_LIMIT_MESSAGE)

        message = "\n".join(lines)
        if result.duplicate_skus or result.row_errors or result.plan_limit_reached:
            QMessageBox.warning(self, "Importación finalizada con observaciones", message)
        else:
            QMessageBox.information(self, "Importación completa", message)

    @staticmethod
    def _with_photo(draft: ProductDraft, photo_path: str | None) -> ProductDraft:
        return ProductDraft(
            sku=draft.sku,
            name=draft.name,
            purchase_price_cents=draft.purchase_price_cents,
            sale_price_cents=draft.sale_price_cents,
            minimum_stock=draft.minimum_stock,
            photo_path=photo_path,
            initial_stock=draft.initial_stock,
        )

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, "No se pudo completar", message)

    def _can_operate_product(self, product: Product) -> bool:
        if product.id not in self.locked_product_ids:
            return True
        QMessageBox.information(self, "Producto bloqueado — STOCK Pro", LOCKED_PRODUCT_MESSAGE)
        return False

    def _refresh_plan_state(self) -> None:
        plan = self.database.get_plan()
        if plan is AppPlan.PRO_ACTIVE:
            self.plan_label.setText("PLAN PRO")
            self.pro_button.setVisible(False)
        elif plan is AppPlan.PRO_EXPIRED:
            self.plan_label.setText("PLAN PRO VENCIDO")
            self.pro_button.setVisible(True)
        else:
            self.plan_label.setText("PLAN FREE")
            self.pro_button.setVisible(True)

    def _show_plan_limit(self) -> None:
        QMessageBox.information(self, "Límite de STOCK Free", FREE_PRODUCT_LIMIT_MESSAGE)

    def _unlock_pro(self) -> None:
        controller = getattr(self, "pro_controller", None)
        if controller is not None:
            controller.unlock()
        else:
            QMessageBox.information(self, "STOCK Pro", "La conexión de STOCK Pro TEST no está configurada.")
