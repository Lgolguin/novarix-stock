# STOCK by NOVARIX — Etapa 8

Aplicación de escritorio local para administrar el stock de un pequeño negocio.

## Suscripciones PRO (TEST)

La etapa 8 agrega backend FastAPI separado, checkout de Mercado Pago TEST,
webhooks verificados y licencias firmadas con tolerancia offline de 72 horas.
Ver [guía de configuración y pruebas](backend/README.md). No se habilitó producción.
El backend requiere sus propias credenciales TEST; el cliente recibe solo configuración pública.

## Planes FREE / PRO

FREE permite hasta 10 productos, tanto por alta manual como por importación Excel; el producto 11 se bloquea.
STOCK PRO — $49.999 ARS / mes permite productos ilimitados mientras está activo.
Al vencer PRO se conservan todos los productos y datos: los primeros 10 productos históricos permanecen activos y los restantes se muestran como BLOQUEADO — PRO, sin edición, ventas ni entradas.
Al eliminar un activo se habilita automáticamente el bloqueado más antiguo; al renovar PRO se desbloquean todos.
El backend usa `STOCK_PRO_MONTHLY_ARS=49999.00`, manteniendo la configuración por entorno.

## Inicio rápido

1. Instalar Python 3.11 o superior desde python.org marcando **Add Python to PATH**.
2. Ejecutar `setup.bat` una sola vez.
3. Ejecutar `run_novarix_stock.bat` para abrir el programa.

La información se guarda automáticamente en `data/novarix_stock.db`. Las fotografías elegidas se copian a `data/product_images`, por lo que continúan disponibles aunque se mueva la imagen original.

## Alcance de esta etapa

- Alta, edición y eliminación de productos.
- Entrada de mercadería y registro de ventas.
- SKU, precios, stock acumulado, stock actual, vendidos y stock mínimo.
- Indicador visual de reposición.
- Persistencia local con SQLite.
- Importación y exportación de productos en formato `.xlsx`.
- Resumen compacto de unidades, vendidos, reposición y valor actual del stock.

## Anulación total de ventas

En Historial de ventas seleccionar una venta y pulsar **ANULAR VENTA**.
Confirmar la anulación total e indicar un motivo opcional. No se admite anulación parcial ni doble.
La venta original conserva todos sus datos y precios; continúa visible como **ANULADA**.
La tabla separada `sale_cancellations` guarda venta original, fecha/hora UTC, cantidad,
facturación/costo/ganancia revertidos, motivo y si se devolvió stock.
Para un producto existente se suma la cantidad a Stock actual y se resta de Vendidos;
Stock total se conserva, incluso después de ajustes manuales. Toda la operación es atómica.
Si el producto fue eliminado se anula el impacto financiero sin recrearlo ni devolver stock,
dejando `stock_returned=0`. Una inconsistencia de Vendidos impide la operación completa.
Los totales globales, por producto y del historial excluyen anuladas, también en filtros
de hoy/mes; se corrige el período de la venta original. Los filtros siguen mostrando la venta.
La licencia no impide anular: se devuelve stock a productos bloqueados sin desbloquearlos.

## Ajuste manual de stock

Seleccionar un producto y pulsar **AJUSTAR STOCK** en el encabezado de Inventario.
Ingresar el nuevo stock físico entero, mayor o igual a cero, y guardar.
El ajuste conserva Vendidos y no crea ventas ni cambia importes históricos.
Para mantener la invariancia existente, **Stock total = nuevo Stock actual + Vendidos**:
Stock total representa el total conciliado, por lo que puede disminuir por un ajuste.
La diferencia queda auditada en `stock_adjustments`, separada de `sales`, con ID,
producto, SKU/nombre históricos, stock anterior/nuevo, diferencia y fecha/hora UTC.
El stock y su auditoría se guardan en una única transacción; si falla, se revierte todo.
Los ajustes sobreviven a la eliminación del producto. Un ajuste al mismo valor registra diferencia cero.
Los productos BLOQUEADO — PRO no permiten ajustes. Los indicadores y el resumen seleccionado
se actualizan al guardar; los totales financieros históricos no cambian.

## Formato Excel

La primera fila debe contener estas columnas (el orden puede variar):

`Foto del producto`, `Nombre`, `Precio de compra`, `Precio de venta`, `Stock total`, `Stock actual`, `Vendidos`, `SKU`, `Stock mínimo`.

La fotografía es opcional. Si se informa una ruta relativa, se interpreta desde la carpeta donde está el Excel. Para conservar la coherencia del inventario, `Stock total` debe ser igual a `Stock actual + Vendidos`. Las filas inválidas y los SKU duplicados se omiten y se informan claramente al finalizar.

## Pruebas

Con el entorno preparado (incluye instalar `backend/requirements.txt` en el mismo venv):

```bat
.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.venv\Scripts\python.exe -m unittest discover -s . -v
```

Ejecuta la suite completa de inventario, ventas, historial, finanzas, planes, Excel, interfaz, backend y licencias firmadas.
