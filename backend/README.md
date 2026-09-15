# STOCK Pro — etapa 8, exclusivamente TEST

Backend FastAPI separado del inventario. No se desplegó ningún servicio ni se realizaron pagos.
Las pruebas automáticas usan un proveedor simulado y claves efímeras; no sustituyen la prueba con Mercado Pago TEST.

## Inicio local (PowerShell)

Desde `D:\NOVARIX STOCK`:

```powershell
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
Copy-Item backend/.env.example backend/.env
backend/.venv/Scripts/python.exe -m backend.generate_keys
```

Copiar `.env.example` solamente si aún no existe `.env`; no sobrescribir una configuración existente.
El generador escribe la clave privada en `backend/.env` y muestra únicamente la pública.
Completar en ese archivo:

| Variable | Valor necesario |
|---|---|
| `STOCK_MP_MODE` | `test`; cualquier otro modo se rechaza |
| `MERCADOPAGO_ACCESS_TOKEN` | Credencial de la aplicación de la cuenta **vendedora de prueba** |
| `MERCADOPAGO_WEBHOOK_SECRET` | Secreto de firma de notificaciones de esa aplicación |
| `MERCADOPAGO_TEST_SELLER_ID` | ID del vendedor TEST argentino |
| `MERCADOPAGO_TEST_BUYER_ID` | ID de un comprador TEST argentino distinto |
| `STOCK_PRO_MONTHLY_ARS` | `49999.00` — STOCK PRO — $49.999 ARS / mes. Se mantiene la configuración por entorno; valor predeterminado `49999.00` |
| `STOCK_LICENSE_PRIVATE_KEY` | Clave generada en el paso anterior, únicamente en el backend |
| `STOCK_WEBHOOK_URL` | URL HTTPS pública temporal terminada en `/webhooks/mercadopago` |
| `STOCK_BACK_URL` | URL HTTPS de retorno que controle el operador; volver a ella no activa PRO |
| `STOCK_BACKEND_DB` | Ruta de SQLite del backend, distinta del inventario |

```powershell
backend/.venv/Scripts/python.exe -m uvicorn backend.app:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

No registrar cuerpos HTTP, cabeceras Authorization, firmas ni variables de entorno. Mantener el servidor local con un solo worker en esta etapa. El repositorio usa transacciones SQLite; las operaciones remotas se serializan durante la reconciliación. Para servicio a escala se debe cambiar ese mecanismo y la base del servidor.

## Configurar el cliente sin secretos de Mercado Pago

En otra consola PowerShell, desde el proyecto:

```powershell
.venv/Scripts/python.exe -m pip install -r requirements.txt
$env:STOCK_LICENSE_API_URL = 'http://127.0.0.1:8000'
$env:STOCK_LICENSE_PUBLIC_KEY = 'PEGAR_SOLO_LA_CLAVE_PUBLICA'
$env:STOCK_DATA_DIR = Join-Path $env:TEMP ('novarix-pro-test-' + [guid]::NewGuid())
.venv/Scripts/python.exe main.py
```

Conservar la misma `STOCK_DATA_DIR` para probar persistencia y renovaciones. Al finalizar, cerrar STOCK y eliminar exclusivamente esa carpeta temporal. El inventario real no se utiliza en estas pruebas. No copiar `backend/.env` al cliente. `client.env.example` enumera solamente las variables públicas; no se carga automáticamente.

La aplicación usa `LicensedStockDatabase` desde `main.py`: el plan efectivo proviene de la firma, reutilizando los límites y bloqueos existentes. `StockDatabase` conserva su interfaz anterior para el motor y las pruebas heredadas; sus estados locales manuales no otorgan PRO en el punto de entrada normal. Las licencias manuales de etapas anteriores no son evidencia comercial firmada.

## Mercado Pago TEST y webhook

Crear vendedor y comprador de prueba **Argentina** siguiendo la documentación oficial. La aplicación/credencial debe pertenecer al vendedor TEST. No identificar una credencial de prueba solo por su prefijo: el adaptador consulta `/users/me` y `/users/{buyer_id}` y exige `test_user`, `MLA` e IDs configurados antes de crear una suscripción. El email del comprador se obtiene en el backend. Esta etapa admite solo ese comprador TEST configurado.

El flujo crea `POST /preapproval` en estado `pending`, mensual, ARS y sin capturar tarjetas. Devuelve `init_point`, validado contra el dominio exacto de Mercado Pago Argentina. El cliente muestra primero el importe ARS y solicita abrir el checkout. Ingresar allí con el comprador TEST y utilizar únicamente tarjetas de prueba.

Se envía `notification_url=STOCK_WEBHOOK_URL`. Configurar los eventos `subscription_preapproval`, `subscription_authorized_payment` y `payment` para la aplicación de prueba. La documentación actual advierte particularidades de configuración para Suscripciones: verificar en el panel que la cuenta admite el secreto y la ruta de notificaciones elegida. Si llegan notificaciones sin `x-signature`, **no relajar la validación**: se rechazan y debe resolverse la configuración con Mercado Pago. La consulta autenticada de licencia también reconcilia directamente la API.

La URL debe ser accesible desde Mercado Pago: `localhost` no sirve como destino del webhook. Se requiere un túnel HTTPS temporal provisto por el operador; no se creó ni publicó uno automáticamente. El retorno HTTPS puede ser una página temporal del operador; no recibe ni otorga licencias. La API no garantiza una URL de autogestión por suscripción, por eso GESTIONAR PRO ofrece validar la licencia y abrir la cuenta general de Mercado Pago.

## API y almacenamiento

- `POST /installations/register`: UUID y bearer aleatorio de instalación. Registro idempotente; un bearer diferente no puede apropiarse del UUID.
- `GET /plans/pro`: moneda, monto ARS y modo test.
- `POST /subscriptions/start`: requiere bearer y monto previamente mostrado. El monto real siempre lo determina el backend.
- `GET /licenses/{installation_id}`: requiere bearer; devuelve únicamente la licencia firmada.
- `POST /webhooks/mercadopago`: exige firma HMAC-SHA256, `x-request-id`, timestamp y `data.id` de la query coherente con el cuerpo.

`backend/data/licenses.db` guarda instalaciones, hash del bearer, licencia, referencia aleatoria del servidor, preapproval real, importe de ese contrato, estado, vigencia y eventos procesados. `installation.db` del cliente guarda solo UUID, credencial de instalación, certificado firmado y reloj observado. No se añaden suscripciones comerciales a `data/novarix_stock.db`. La clave privada y secretos de Mercado Pago existen únicamente en la configuración del backend.

La firma usa `id:{data.id en minúsculas};request-id:{x-request-id};ts:{ts};`. Se aceptan timestamps en segundos o milisegundos con tolerancia de 5 minutos, comparación constante y límite de 16 KiB. Una entrega repetida es idempotente; los errores remotos no se marcan procesados. Las notificaciones tardías se resuelven consultando el estado actual, no aplicando el estado del JSON. Si Mercado Pago reintenta con un timestamp original antiguo, devolverá 401: consultar el registro de notificaciones y reconciliar mediante el cliente, sin deshabilitar la firma.

La activación exige preapproval autorizado, referencia, vendedor, comprador, frecuencia, moneda e importe válidos, más pago aprobado consultado por API. La vigencia corresponde al mes calendario de la factura pagada; un nuevo intento rechazado no elimina un período anterior que sigue pagado. Cancelación o pausa revocan PRO al validarse. La autorización sola no da acceso gratuito mientras el primer pago sigue pendiente. Las respuestas no incluyen tarjetas ni payloads completos del proveedor.

## Política offline

Certificado Ed25519 vinculado al UUID, público verificado localmente, audiencia exclusiva TEST. Dura como máximo **72 horas desde la última comprobación remota exitosa**, con el límite adicional del período pagado más 72 horas. El backend no emite una prórroga si falla Mercado Pago. Una cancelación confirmada se aplica inmediatamente al recibir el certificado nuevo. Vencida la tolerancia offline, pasa a PRO VENCIDO usando la lógica existente (10 primeros productos activos, excedentes bloqueados, ningún borrado).

Validación al iniciar, al volver a la aplicación (mínimo 60 segundos entre intentos), cada 15 minutos y mediante GESTIONAR PRO → ACTUALIZAR LICENCIA. El backend limita reconciliaciones normales a una cada 5 minutos; el webhook fuerza comprobación. Timeout de conexión 3 s; solicitudes cliente hasta 20 s, proveedor 8 s por solicitud. La UI usa tareas de fondo. Se rechazan firmas alteradas, certificados ajenos, respuestas anteriores y retrocesos ordinarios del reloj. Cambiar `license_state.status` o su fecha no cambia el plan efectivo. Modificar el código, la clave pública confiable o restaurar todo el sistema queda fuera de esta protección comercial local.

## Reintentos de creación

Una tentativa se guarda antes del POST a Mercado Pago. Si la respuesta se pierde, el siguiente intento busca la referencia del servidor y reutiliza la suscripción. Nunca repite ciegamente el POST. Si no se puede encontrar, devuelve 409 y requiere revisión del operador en TEST. Tras confirmar en el panel/API que no existe ninguna suscripción para esa referencia, el operador puede descartar solo esa tentativa `creating` en la base temporal de backend. No borrar un contrato remoto activo para resolver un error de red. Una suscripción pendiente se reutiliza; las canceladas permiten iniciar otra. Las pausadas se gestionan en Mercado Pago para evitar duplicados.

## Pruebas y alcance

Instalar también las dependencias del backend en el entorno donde se ejecuta la suite integrada:

```powershell
.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
.venv/Scripts/python.exe -m unittest discover -s . -v
```

La suite mantiene las 45 pruebas anteriores, adaptando solo el texto esperado del antiguo aviso “próximamente”. Agrega casos de checkout, firmas, identidad, activación, cancelación, renovación, falsificación local, tolerancia offline, idempotencia, caída del proveedor, cuentas reales rechazadas y UI.

E2E externo pendiente de credenciales TEST y endpoint HTTPS: FREE → checkout TEST → pago TEST → webhook firmado → PRO → más de 10 productos → cancelar/pausar → PRO VENCIDO → primeros 10 activos → renovar → desbloqueo. Ejecutar todo con bases temporales. Las pruebas simuladas no realizaron compras ni crearon suscripciones en Mercado Pago.

Antes de producción quedan: validación E2E con Mercado Pago y sus particularidades de notificaciones, gestión de compradores reales, autenticación/recuperación de instalaciones, endurecimiento operativo y límites de tráfico, almacenamiento y rotación de claves, políticas comerciales de renovación, base de servidor, reconciliación programada, despliegue HTTPS y distribución confiable de la clave pública. No se implementaron producción, EXE ni instalador.

## Fuentes oficiales consultadas

- [Suscripciones pendientes](https://www.mercadopago.com.ar/developers/es/docs/subscriptions/integration-configuration/subscription-no-associated-plan/pending-payments)
- [Crear suscripción](https://www.mercadopago.com.ar/developers/es/reference/online-payments/subscriptions/create-preapproval/post)
- [Cuentas de prueba](https://www.mercadopago.com.ar/developers/es/docs/subscriptions/additional-content/your-integrations/test/accounts)
- [Compra de prueba](https://www.mercadopago.com.ar/developers/es/docs/subscriptions/integration-test/payment-approval)
- [Webhooks](https://www.mercadopago.com.ar/developers/es/docs/subscriptions/additional-content/your-integrations/notifications/webhooks)
- [Facturas de suscripción](https://www.mercadopago.com.ar/developers/es/reference/online-payments/subscriptions/authorized-payment-search/get)
- [Ed25519](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/)
