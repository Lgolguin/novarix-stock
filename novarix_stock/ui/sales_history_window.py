from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QFrame, QHBoxLayout,
                              QHeaderView, QLabel, QLineEdit, QMessageBox, QInputDialog,
                              QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)
import sqlite3
from novarix_stock.database import StockError

from novarix_stock.sales_history import (format_history_money, local_sale_time,
                                         query_history, summarize_sales)
from novarix_stock.ui.theme import APP_STYLESHEET


class HistoryItem(QTableWidgetItem):
    def __init__(self, text, sort_key):
        super().__init__(text)
        self.sort_key = sort_key
        self.setToolTip(text)

    def __lt__(self, other):
        return self.sort_key < other.sort_key


class SalesHistoryWindow(QDialog):
    sale_cancelled = Signal()

    def __init__(self, database, parent=None):
        super().__init__(parent)
        self.database = database
        self.period = 'TODAS'
        self.setWindowTitle('Historial de ventas — STOCK by NOVARIX')
        self.setStyleSheet(APP_STYLESHEET)
        self.resize(1250, 650)
        self.setMinimumSize(900, 460)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 22)
        root.setSpacing(14)
        title = QLabel('Historial de ventas')
        title.setObjectName('dialogTitle')
        root.addWidget(title)
        root.addWidget(QLabel('Precios originales de cada venta • Anulaciones auditadas • Hora local'))
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('Buscar por producto o SKU…')
        self.search.setClearButtonEnabled(True)
        filters.addWidget(self.search, 1)
        self.period_buttons = {}
        for period in ('TODAS', 'HOY', 'ESTE MES'):
            button = QPushButton(period)
            button.setCheckable(True)
            button.setChecked(period == self.period)
            button.clicked.connect(lambda checked=False, value=period: self.set_period(value))
            self.period_buttons[period] = button
            filters.addWidget(button)
        self.refresh_button = QPushButton('ACTUALIZAR')
        self.refresh_button.setObjectName('primaryButton')
        self.refresh_button.clicked.connect(self.refresh)
        filters.addWidget(self.refresh_button)
        self.cancel_sale_button = QPushButton('ANULAR VENTA')
        self.cancel_sale_button.setObjectName('dangerButton')
        self.cancel_sale_button.clicked.connect(self.cancel_selected_sale)
        filters.addWidget(self.cancel_sale_button)
        root.addLayout(filters)
        metrics = QHBoxLayout()
        self.summary_labels = []
        for name in ('TOTAL FACTURADO', 'COSTO TOTAL', 'GANANCIA TOTAL', 'UNIDADES VENDIDAS'):
            card = QFrame()
            card.setObjectName('metricCard')
            card.setProperty('accent', 'metricViolet' if name == 'GANANCIA TOTAL' else 'metricCyan')
            layout = QVBoxLayout(card)
            label = QLabel(name)
            label.setObjectName('metricTitle')
            value = QLabel()
            value.setObjectName('metricValue')
            layout.addWidget(label)
            layout.addWidget(value)
            metrics.addWidget(card)
            self.summary_labels.append(value)
        root.addLayout(metrics)
        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(['FECHA', 'HORA', 'SKU', 'PRODUCTO', 'CANTIDAD',
                                             'PRECIO UNITARIO', 'FACTURADO', 'COSTO', 'GANANCIA', 'ESTADO'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate((100, 85, 135, 230, 95, 135, 125, 125, 125)):
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setSortIndicator(0, Qt.SortOrder.DescendingOrder)
        root.addWidget(self.table, 1)
        self.status = QLabel()
        root.addWidget(self.status)
        self.search.textChanged.connect(self.refresh)
        self.setStyleSheet(APP_STYLESHEET + '\nQPushButton:checked { border: 1px solid #55eaff; background: #173c55; }')
        self.refresh()

    def cancel_selected_sale(self):
        if not self.table.selectedItems():
            QMessageBox.information(self, 'Anular venta', 'Seleccioná una venta para anular.')
            return
        item = self.table.item(self.table.currentRow(), 0)
        sale_id = item.data(Qt.ItemDataRole.UserRole)
        sale = next((sale for sale in self.database.list_sales() if sale.id == sale_id), None)
        if sale is None or sale.is_cancelled:
            QMessageBox.information(self, 'Anular venta', 'La venta ya está ANULADA o no existe.')
            return
        exists = self.database.get_product(sale.product_id) is not None
        effect = ('Se devolverá la cantidad al stock y se revertirán los importes.' if exists else
                  'El producto fue eliminado: se revertirán los importes sin recrearlo ni devolver stock.')
        reason, accepted = QInputDialog.getText(self, 'Confirmar anulación total',
            f'Venta #{sale.id} • {sale.product_name} • {sale.quantity} unidades\n'
            f'{effect}\nLa venta quedará visible como ANULADA.\nMotivo (opcional):')
        if not accepted:
            return
        try:
            self.database.cancel_sale(sale.id, reason)
        except StockError as error:
            QMessageBox.warning(self, 'Anular venta', str(error))
            return
        self.refresh()
        self.sale_cancelled.emit()

    def set_period(self, period):
        self.period = period
        for name, button in self.period_buttons.items():
            button.setChecked(name == period)
        self.refresh()

    def refresh(self):
        try:
            sales = query_history(self.database, self.search.text(), self.period)
        except (sqlite3.Error, ValueError) as error:
            self.status.setText('No se pudo actualizar. Los datos mostrados pueden estar desactualizados.')
            QMessageBox.warning(self, 'Historial de ventas', str(error))
            return
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(sales))
        for row, sale in enumerate(sales):
            timestamp = local_sale_time(sale)
            values = [
                (timestamp.strftime('%d/%m/%Y'), (timestamp, sale.id)),
                (timestamp.strftime('%H:%M:%S'), (timestamp.time(), sale.id)),
                (sale.product_sku, sale.product_sku.casefold()),
                (sale.product_name, sale.product_name.casefold()),
                (str(sale.quantity), sale.quantity),
                *[(format_history_money(value), value) for value in
                  (sale.sale_price_cents, sale.invoiced_cents, sale.cost_cents, sale.profit_cents)],
                ('ANULADA' if sale.is_cancelled else 'ACTIVA', int(sale.is_cancelled)),
            ]
            for column, (text, key) in enumerate(values):
                item = HistoryItem(text, key)
                item.setData(Qt.ItemDataRole.UserRole, sale.id)
                if column >= 4:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        self.table.clearSelection()
        summary = summarize_sales(sales)
        for label, value in zip(self.summary_labels, (summary.invoiced_cents, summary.cost_cents, summary.profit_cents)):
            label.setText(format_history_money(value))
        self.summary_labels[3].setText(str(summary.units))
        self.status.setText(f'{len(sales)} ventas visibles • Clic en los encabezados para ordenar' if sales
                            else 'Sin ventas para los filtros seleccionados.')

