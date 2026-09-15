from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
import sqlite3

from novarix_stock.database import StockDatabase
from novarix_stock.stock_movement_history import (
    MOVEMENT_FILTERS,
    query_stock_movements,
    summarize_movements,
)
from novarix_stock.ui.theme import APP_STYLESHEET


class MovementItem(QTableWidgetItem):
    def __init__(self, text, sort_key):
        super().__init__(text)
        self.sort_key = sort_key
        self.setToolTip(text)

    def __lt__(self, other):
        return self.sort_key < other.sort_key


class StockMovementHistoryWindow(QDialog):
    DATE, TIME, SKU, PRODUCT, TYPE, QUANTITY, BEFORE, AFTER, DETAIL = range(9)

    def __init__(self, database: StockDatabase, product_id: int | None = None, parent=None):
        super().__init__(parent)
        self.database = database
        self.product_id = product_id
        self.movement_type = "TODOS"
        self.setWindowTitle("Historial de movimientos de stock — STOCK by NOVARIX")
        self.setStyleSheet(APP_STYLESHEET)
        self.resize(1320, 700)
        self.setMinimumSize(950, 480)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 22)
        root.setSpacing(14)

        title = QLabel("Historial de movimientos de stock")
        title.setObjectName("dialogTitle")
        root.addWidget(title)
        root.addWidget(QLabel("Movimientos de entrada, venta, ajuste y anulación • SKU/nombre históricos"))

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Buscar por producto o SKU…")
        self.search.setClearButtonEnabled(True)
        filters.addWidget(self.search, 1)

        self.type_buttons = {}
        for name in ("TODOS", "ENTRADAS", "VENTAS", "AJUSTES", "ANULACIONES"):
            button = QPushButton(name)
            button.setCheckable(True)
            button.setChecked(name == self.movement_type)
            button.clicked.connect(lambda checked=False, value=name: self.set_movement_type(value))
            self.type_buttons[name] = button
            filters.addWidget(button)

        self.only_product_checkbox = QCheckBox("Solo este producto")
        self.only_product_checkbox.setChecked(product_id is not None)
        self.only_product_checkbox.setEnabled(product_id is not None)
        self.only_product_checkbox.stateChanged.connect(self.refresh)
        filters.addWidget(self.only_product_checkbox)

        self.refresh_button = QPushButton("ACTUALIZAR")
        self.refresh_button.setObjectName("primaryButton")
        self.refresh_button.clicked.connect(self.refresh)
        filters.addWidget(self.refresh_button)
        root.addLayout(filters)

        metrics = QHBoxLayout()
        self.summary_labels = {}
        for name, accent in (
            ("MOVIMIENTOS VISIBLES", "metricCyan"),
            ("UNIDADES ENTRADA", "metricBlue"),
            ("UNIDADES VENTA", "metricViolet"),
            ("UNIDADES AJUSTE", "metricCyan"),
            ("UNIDADES ANULADAS", "metricBlue"),
        ):
            card = QFrame()
            card.setObjectName("metricCard")
            card.setProperty("accent", accent)
            layout = QVBoxLayout(card)
            label = QLabel(name)
            label.setObjectName("metricTitle")
            value = QLabel("0")
            value.setObjectName("metricValue")
            layout.addWidget(label)
            layout.addWidget(value)
            metrics.addWidget(card)
            self.summary_labels[name] = value
        root.addLayout(metrics)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels([
            "FECHA", "HORA", "SKU", "PRODUCTO", "TIPO", "CANTIDAD",
            "STOCK ANTERIOR", "STOCK POSTERIOR", "DETALLE",
        ])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate((100, 85, 130, 220, 110, 95, 120, 120, 200)):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setSortIndicator(0, Qt.SortOrder.DescendingOrder)
        root.addWidget(self.table, 1)

        self.status = QLabel()
        root.addWidget(self.status)

        self.search.textChanged.connect(self.refresh)
        self.setStyleSheet(
            APP_STYLESHEET
            + "\nQPushButton:checked { border: 1px solid #55eaff; background: #173c55; }"
        )
        self.refresh()

    def set_movement_type(self, movement_type: str) -> None:
        self.movement_type = movement_type
        for name, button in self.type_buttons.items():
            button.setChecked(name == movement_type)
        self.refresh()

    def _format_optional_int(self, value: int | None) -> str:
        return "-" if value is None else f"{value:,}".replace(",", ".")

    def refresh(self) -> None:
        try:
            product_id = self.product_id if self.only_product_checkbox.isChecked() else None
            movements = query_stock_movements(
                self.database,
                movement_type=self.movement_type,
                search=self.search.text(),
                product_id=product_id,
            )
        except (sqlite3.Error, ValueError) as error:
            self.status.setText("No se pudo actualizar. Los datos mostrados pueden estar desactualizados.")
            QMessageBox.warning(self, "Historial de movimientos", str(error))
            return

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(movements))
        for row, movement in enumerate(movements):
            timestamp = movement.timestamp
            values = [
                (timestamp.strftime("%d/%m/%Y"), (timestamp, movement.id)),
                (timestamp.strftime("%H:%M:%S"), (timestamp.time(), movement.id)),
                (movement.product_sku, movement.product_sku.casefold()),
                (movement.product_name, movement.product_name.casefold()),
                (movement.movement_type, movement.movement_type),
                (self._format_optional_int(movement.quantity), movement.quantity),
                (self._format_optional_int(movement.stock_before), movement.stock_before or -1),
                (self._format_optional_int(movement.stock_after), movement.stock_after or -1),
                (movement.detail, movement.detail.casefold()),
            ]
            for column, (text, key) in enumerate(values):
                item = MovementItem(text, key)
                item.setData(Qt.ItemDataRole.UserRole, movement.id)
                if column in (self.QUANTITY, self.BEFORE, self.AFTER):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        self.table.clearSelection()

        summary = summarize_movements(movements)
        self.summary_labels["MOVIMIENTOS VISIBLES"].setText(str(summary["total"]))
        self.summary_labels["UNIDADES ENTRADA"].setText(str(summary["ENTRADA"]))
        self.summary_labels["UNIDADES VENTA"].setText(str(summary["VENTA"]))
        self.summary_labels["UNIDADES AJUSTE"].setText(str(summary["AJUSTE"]))
        self.summary_labels["UNIDADES ANULADAS"].setText(str(summary["ANULACIÓN DE VENTA"]))

        self.status.setText(
            f"{len(movements)} movimientos visibles • Clic en los encabezados para ordenar"
            if movements
            else "Sin movimientos para los filtros seleccionados."
        )
