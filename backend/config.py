from dataclasses import dataclass, field
from decimal import Decimal
import os
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    access_token: str = field(repr=False)
    webhook_secret: str = field(repr=False)
    signing_key: str = field(repr=False)
    monthly_ars: Decimal
    seller_id: str
    buyer_id: str
    back_url: str
    webhook_url: str
    database_path: Path
    mode: str = 'test'

    def validate(self):
        if self.mode not in ('test', 'production'):
            raise ValueError('STOCK_MP_MODE debe ser test o production.')
        if not self.access_token or not self.webhook_secret or not self.signing_key or not self.seller_id.isdigit():
            raise ValueError('Falta configuraci?n obligatoria del backend.')
        if self.mode == 'test':
            if not self.buyer_id.isdigit():
                raise ValueError('Falta MERCADOPAGO_TEST_BUYER_ID para modo TEST.')
            if self.seller_id == self.buyer_id:
                raise ValueError('Comprador y vendedor TEST deben ser distintos.')
        if (not self.monthly_ars.is_finite() or self.monthly_ars <= 0
                or self.monthly_ars > Decimal('10000000')
                or self.monthly_ars != self.monthly_ars.quantize(Decimal('.01'))):
            raise ValueError('STOCK_PRO_MONTHLY_ARS debe ser un importe positivo con hasta 2 decimales.')
        for url in (self.back_url, self.webhook_url):
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError('Las URLs de retorno y webhook deben ser HTTPS.')

    @classmethod
    def from_env(cls):
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).with_name('.env'))
        mode = os.getenv('STOCK_MP_MODE', 'test').strip().lower()
        seller_id = (os.getenv('MERCADOPAGO_TEST_SELLER_ID', '') if mode == 'test'
                     else os.getenv('MERCADOPAGO_SELLER_ID', ''))
        buyer_id = os.getenv('MERCADOPAGO_TEST_BUYER_ID', '') if mode == 'test' else ''
        result = cls(
            access_token=os.getenv('MERCADOPAGO_ACCESS_TOKEN', ''),
            webhook_secret=os.getenv('MERCADOPAGO_WEBHOOK_SECRET', ''),
            signing_key=os.getenv('STOCK_LICENSE_PRIVATE_KEY', ''),
            monthly_ars=Decimal(os.getenv('STOCK_PRO_MONTHLY_ARS', '49999.00')),
            seller_id=seller_id,
            buyer_id=buyer_id,
            back_url=os.getenv('STOCK_BACK_URL', ''),
            webhook_url=os.getenv('STOCK_WEBHOOK_URL', ''),
            database_path=Path(os.getenv('STOCK_BACKEND_DB', str(Path(__file__).parent / 'data/licenses.db'))),
            mode=mode,
        )
        result.validate()
        return result
