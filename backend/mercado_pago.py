from decimal import Decimal
import re
from urllib.parse import urlsplit, parse_qs
import httpx


class ProviderError(Exception):
    """Safe public message: never include provider responses or headers."""


def resource_id(value):
    value = str(value)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise ProviderError('Identificador de Mercado Pago invÃ¡lido.')
    return value


def checkout_url(value, subscription_id):
    url = urlsplit(str(value))
    if (url.scheme != 'https' or url.hostname != 'www.mercadopago.com.ar'
            or url.port not in (None, 443) or url.username or url.password
            or url.path != '/subscriptions/checkout'
            or parse_qs(url.query).get('preapproval_id') != [subscription_id]):
        raise ProviderError('Mercado Pago no devolviÃ³ un checkout seguro.')
    return str(value)


class MercadoPago:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.transport = transport

    def request(self, method, path, **kwargs):
        try:
            with httpx.Client(base_url='https://api.mercadopago.com',
                              timeout=httpx.Timeout(8, connect=3),
                              follow_redirects=False, transport=self.transport) as client:
                response = client.request(method, path, headers={
                    'Authorization': f'Bearer {self.settings.access_token}'}, **kwargs)
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict):
                    raise ValueError()
                return result
        except (httpx.HTTPError, ValueError) as e:
            raise ProviderError('Mercado Pago no estÃ¡ disponible o rechazÃ³ la solicitud TEST.') from None

    def test_accounts(self):
        seller = self.request('GET', '/users/me')
        buyer = self.request('GET', '/users/' + self.settings.buyer_id)
        for account, expected in ((seller, self.settings.seller_id), (buyer, self.settings.buyer_id)):
            if (str(account.get('id')) != expected or (account is seller and 'test_user' not in account.get('tags', []))
                    or account.get('site_id') != 'MLA'):
                raise ProviderError('Se requieren dos cuentas TEST de Argentina. OperaciÃ³n bloqueada.')
        email = buyer.get('email') or 'test_user_892623633165634879@testuser.com'
        if '@' not in email:
            raise ProviderError('La cuenta compradora TEST no tiene email utilizable.')
        return email

    def create_subscription(self, reference, amount, payer_email=None):
        if self.settings.mode == 'test':
            email = self.test_accounts()
            reason = 'STOCK PRO ? TEST'
        else:
            email = (payer_email or '').strip().lower()
            if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
                raise ProviderError('Ingres? un email v?lido para Mercado Pago.')
            seller = self.request('GET', '/users/me')
            if str(seller.get('id')) != self.settings.seller_id or seller.get('site_id') != 'MLA':
                raise ProviderError('La cuenta vendedora de Mercado Pago no coincide con la configuraci?n.')
            reason = 'STOCK PRO'
        return self.request('POST', '/preapproval', json={
            'reason': reason, 'external_reference': reference,
            'payer_email': email, 'status': 'pending',
            'auto_recurring': {'frequency': 1, 'frequency_type': 'months',
                               'transaction_amount': float(Decimal(amount)), 'currency_id': 'ARS'},
            'back_url': self.settings.back_url,
            'notification_url': self.settings.webhook_url,
        })

    def subscription(self, subscription_id):
        return self.request('GET', '/preapproval/' + resource_id(subscription_id))

    def find_subscription(self, reference):
        result = self.request('GET', '/preapproval/search', params={'external_reference': reference})
        rows = [row for row in result.get('results', []) if row.get('external_reference') == reference]
        if len(rows) > 1:
            raise ProviderError('Se requiere revisar la suscripciÃ³n TEST en Mercado Pago.')
        return rows[0] if rows else None

    def invoices(self, subscription_id):
        result = self.request('GET', '/authorized_payments/search', params={
            'preapproval_id': resource_id(subscription_id)})
        return result.get('results', [])

    def invoice(self, invoice_id):
        return self.request('GET', '/authorized_payments/' + resource_id(invoice_id))

    def payment(self, payment_id):
        return self.request('GET', '/v1/payments/' + resource_id(payment_id))





