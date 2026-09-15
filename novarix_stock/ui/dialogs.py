from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSize, QTimer, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from novarix_stock.models import Product, ProductDraft


class ProductDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, product: Product | None = None) -> None:
        super().__init__(parent)
        self.product = product
        self.selected_photo = product.photo_path if product else None
        self.setWindowTitle("Editar producto" if product else "Agregar producto")
        self.setMinimumWidth(730)
        self.setModal(True)
        self._build_ui()
        if product:
            self._load_product(product)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(16)

        title = QLabel("Editar producto" if self.product else "Nuevo producto")
        title.setObjectName("dialogTitle")
        root.addWidget(title)

        content = QHBoxLayout()
        content.setSpacing(18)
        form = QFormLayout()
        form.setSpacing(11)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self.sku_input = QLineEdit()
        self.sku_input.setPlaceholderText("Ej.: REM-001")
        self.sku_input.setMinimumWidth(250)
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Nombre del producto")

        self.purchase_input = self._money_spinbox()
        self.sale_input = self._money_spinbox()
        self.minimum_input = QSpinBox()
        self.minimum_input.setMinimumWidth(250)
        self.minimum_input.setRange(0, 99_999_999)
        self.minimum_input.setSuffix(" u.")
        self.initial_input = QSpinBox()
        self.initial_input.setMinimumWidth(250)
        self.initial_input.setRange(0, 99_999_999)
        self.initial_input.setSuffix(" u.")

        self._auto_select_spinboxes = () if self.product else (
            self.purchase_input,
            self.sale_input,
            self.minimum_input,
            self.initial_input,
        )
        for spinbox in self._auto_select_spinboxes:
            spinbox.installEventFilter(self)
            spinbox.lineEdit().installEventFilter(self)

        form.addRow(self._field_label("SKU / código interno"), self.sku_input)
        form.addRow(self._field_label("Nombre"), self.name_input)
        form.addRow(self._field_label("Precio de compra"), self.purchase_input)
        form.addRow(self._field_label("Precio de venta"), self.sale_input)
        form.addRow(self._field_label("Stock mínimo"), self.minimum_input)
        if not self.product:
            form.addRow(self._field_label("Stock inicial"), self.initial_input)
        content.addLayout(form, 1)

        photo_column = QVBoxLayout()
        photo_column.setSpacing(9)
        self.photo_preview = QLabel("Sin foto")
        self.photo_preview.setObjectName("photoPreview")
        self.photo_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.photo_preview.setFixedSize(150, 150)
        choose_photo = QPushButton("Elegir foto")
        choose_photo.clicked.connect(self._choose_photo)
        remove_photo = QPushButton("Quitar foto")
        remove_photo.clicked.connect(self._remove_photo)
        photo_column.addWidget(self.photo_preview)
        photo_column.addWidget(choose_photo)
        photo_column.addWidget(remove_photo)
        photo_column.addStretch()
        content.addLayout(photo_column)
        root.addLayout(content)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Save
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Guardar")
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("primaryButton")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() in (QEvent.Type.FocusIn, QEvent.Type.MouseButtonRelease):
            for spinbox in self._auto_select_spinboxes:
                editor = spinbox.lineEdit()
                if watched is spinbox or watched is editor:
                    QTimer.singleShot(0, editor.selectAll)
                    break
        return super().eventFilter(watched, event)

    @staticmethod
    def _money_spinbox() -> QDoubleSpinBox:
        spinbox = QDoubleSpinBox()
        spinbox.setMinimumWidth(250)
        spinbox.setRange(0, 999_999_999.99)
        spinbox.setDecimals(2)
        spinbox.setPrefix("$ ")
        spinbox.setSingleStep(100)
        return spinbox

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    def _load_product(self, product: Product) -> None:
        self.sku_input.setText(product.sku)
        self.name_input.setText(product.name)
        self.purchase_input.setValue(product.purchase_price_cents / 100)
        self.sale_input.setValue(product.sale_price_cents / 100)
        self.minimum_input.setValue(product.minimum_stock)
        self._refresh_photo()

    def _choose_photo(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Elegir fotografía del producto",
            "",
            "Imágenes (*.png *.jpg *.jpeg *.webp *.bmp)",
        )
        if filename:
            self.selected_photo = filename
            self._refresh_photo()

    def _remove_photo(self) -> None:
        self.selected_photo = None
        self._refresh_photo()

    def _refresh_photo(self) -> None:
        if self.selected_photo and Path(self.selected_photo).is_file():
            pixmap = QPixmap(self.selected_photo)
            self.photo_preview.setPixmap(
                pixmap.scaled(
                    QSize(136, 136),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            self.photo_preview.setText("")
        else:
            self.photo_preview.clear()
            self.photo_preview.setText("Sin foto")

    def draft(self) -> ProductDraft:
        return ProductDraft(
            sku=self.sku_input.text(),
            name=self.name_input.text(),
            purchase_price_cents=round(self.purchase_input.value() * 100),
            sale_price_cents=round(self.sale_input.value() * 100),
            minimum_stock=self.minimum_input.value(),
            photo_path=self.selected_photo,
            initial_stock=0 if self.product else self.initial_input.value(),
        )


class StockAdjustmentDialog(QDialog):
    def __init__(self, product: Product, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Ajustar stock")
        self.setMinimumWidth(420)
        self.setModal(True)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(14)
        heading = QLabel("AJUSTAR STOCK")
        heading.setObjectName("dialogTitle")
        root.addWidget(heading)
        identity = QLabel(f"Producto: {product.name}\nSKU: {product.sku}")
        identity.setTextFormat(Qt.TextFormat.PlainText)
        identity.setWordWrap(True)
        root.addWidget(identity)
        root.addWidget(QLabel(f"Stock actual del sistema: {product.stock_current}"))
        root.addWidget(QLabel("Nuevo stock contado"))
        self.stock_input = QSpinBox()
        self.stock_input.setRange(0, 2_147_483_647)
        self.stock_input.setValue(product.stock_current)
        root.addWidget(self.stock_input)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Guardar")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primaryButton")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.stock_input.setFocus()
        self.stock_input.selectAll()

    def new_stock(self) -> int:
        return self.stock_input.value()


class QuantityDialog(QDialog):
    def __init__(
        self,
        title: str,
        product_name: str,
        action_text: str,
        parent: QWidget | None = None,
        maximum: int = 99_999_999,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(390)
        self.setModal(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(14)
        heading = QLabel(title)
        heading.setObjectName("dialogTitle")
        root.addWidget(heading)
        root.addWidget(QLabel(product_name))

        self.quantity_input = QSpinBox()
        self.quantity_input.setRange(1, max(1, maximum))
        self.quantity_input.setSuffix(" unidades")
        self.quantity_input.setValue(1)
        root.addWidget(self.quantity_input)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(action_text)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primaryButton")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def quantity(self) -> int:
        return self.quantity_input.value()
