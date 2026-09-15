"""Small asynchronous bridge between the existing window and the license service."""
import time
from decimal import Decimal

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton, QInputDialog

from novarix_stock.licensing import LicenseError, safe_checkout
from novarix_stock.models import AppPlan


class TaskSignals(QObject):
    done = Signal(object, object)


class NetworkTask(QRunnable):
    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.signals = TaskSignals()

    def run(self):
        try:
            value, error = self.operation(), None
        except LicenseError as exc:
            value, error = None, str(exc)
        except Exception:
            value, error = None, 'No se pudo actualizar STOCK Pro. ReintentÃ¡ mÃ¡s tarde.'
        self.signals.done.emit(value, error)


class ProController(QObject):
    def __init__(self, window, client):
        super().__init__(window)
        self.window, self.client = window, client
        self.busy = False
        self.last_attempt = 0
        self.callback = None
        self.task = None
        window.pro_controller = self
        self.manage_button = QPushButton('GESTIONAR PRO')
        self.manage_button.clicked.connect(self.manage)
        window.statusBar().addPermanentWidget(self.manage_button)
        self.timer = QTimer(self)
        self.timer.setInterval(15 * 60 * 1000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        QApplication.instance().applicationStateChanged.connect(self.on_focus)
        QTimer.singleShot(0, self.refresh)

    def on_focus(self, state):
        if state == Qt.ApplicationState.ApplicationActive and time.monotonic() - self.last_attempt > 60:
            self.refresh()

    def run(self, operation, callback):
        if self.busy:
            return
        self.busy = True
        self.callback = callback
        self.window.pro_button.setEnabled(False)
        self.manage_button.setEnabled(False)
        self.task = NetworkTask(operation)
        self.task.signals.done.connect(self.finished, Qt.ConnectionType.QueuedConnection)
        QThreadPool.globalInstance().start(self.task)

    @Slot(object, object)
    def finished(self, result, error):
        self.busy = False
        self.window.pro_button.setEnabled(True)
        self.manage_button.setEnabled(True)
        callback = self.callback
        self.callback = None
        callback(result, error)

    def refresh(self, interactive=False):
        if self.busy:
            return
        self.last_attempt = time.monotonic()
        self.run(self.client.refresh, lambda value, error: self.refreshed(value, error, interactive))

    def refreshed(self, value, error, interactive=False):
        self.window._refresh_table()
        state = self.client.effective_state()
        if error:
            message = error
        elif state.status is AppPlan.PRO_ACTIVE:
            message = 'Suscripción activa'
        elif state.status is AppPlan.PRO_EXPIRED:
            message = 'Suscripción vencida o sin validación vigente'
        else:
            message = 'PLAN FREE • STOCK PRO — $49.999 ARS / mes'
        self.window.statusBar().showMessage(message)
        if interactive:
            QMessageBox.information(self.window, 'STOCK Pro', message)

    def unlock(self):
        self.run(self.client.offer, self.show_offer)

    def show_offer(self, offer, error):
        if error:
            QMessageBox.information(self.window, 'STOCK Pro', error)
            return
        amount = f"{Decimal(offer['monthly_amount']):,.2f}".replace(',', '_').replace('.', ',').replace('_', '.')
        test_mode = offer.get('mode') == 'test'
        if test_mode:
            intro = 'MODO TEST ? Solo cuentas y tarjetas de prueba.\n\n'
            question = '?Abrir checkout TEST?'
        else:
            intro = 'Suscripci?n mensual de STOCK PRO.\n\n'
            question = '?Abrir Mercado Pago para continuar?'
        answer = QMessageBox.question(
            self.window,
            'STOCK PRO — $49.999 ARS / mes',
            intro + f'Monto mensual configurado: ARS $ {amount}.\n'
            + 'El pago se completa en Mercado Pago. ' + question,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            email = 'test@novarix.local'
            if not test_mode:
                email, ok = QInputDialog.getText(
                    self.window, 'STOCK Pro', 'Email de Mercado Pago:'
                )
                if not ok:
                    return
                email = email.strip()
                if not email:
                    QMessageBox.information(
                        self.window,
                        'STOCK Pro',
                        'Ingres? el email de tu cuenta de Mercado Pago.',
                    )
                    return
            self.run(
                lambda: self.client.start(offer['monthly_amount'], email),
                self.open_checkout,
            )

    def open_checkout(self, result, error):
        if error:
            QMessageBox.information(self.window, 'STOCK Pro', error)
            return
        url = result.get('checkout_url')
        if not url:
            self.refresh(interactive=True)
            return
        try:
            url = safe_checkout(url)
        except LicenseError as exc:
            QMessageBox.warning(self.window, 'STOCK Pro', str(exc))
            return
        if not QDesktopServices.openUrl(QUrl(url)):
            QMessageBox.warning(self.window, 'STOCK Pro', 'No se pudo abrir el navegador predeterminado.')
            return
        self.window.statusBar().showMessage('Checkout abierto. Al volver se comprobará la licencia; también podés usar GESTIONAR PRO.')
        self.last_attempt = 0

    def manage(self):
        dialog = QMessageBox(self.window)
        dialog.setWindowTitle('Gestionar STOCK Pro')
        state = self.client.effective_state()
        dialog.setText('STOCK PRO â€” $49.999 ARS / mes\n'
                       'ActualizÃ¡ la licencia despuÃ©s de completar el checkout.\n'
                       'Para administrar la suscripciÃ³n, ingresÃ¡ con tu cuenta en Mercado Pago.')
        if state.last_validated_at:
            dialog.setInformativeText('Ãšltima validaciÃ³n: ' + state.last_validated_at.astimezone().strftime('%d/%m/%Y %H:%M'))
        refresh = dialog.addButton('ACTUALIZAR LICENCIA', QMessageBox.ButtonRole.ActionRole)
        account = dialog.addButton('ABRIR MERCADO PAGO', QMessageBox.ButtonRole.ActionRole)
        dialog.addButton(QMessageBox.StandardButton.Close)
        dialog.exec()
        if dialog.clickedButton() == refresh:
            self.refresh(interactive=True)
        elif dialog.clickedButton() == account:
            QDesktopServices.openUrl(QUrl('https://www.mercadopago.com.ar/'))

