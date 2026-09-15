# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets')],
    hiddenimports=['novarix_stock', 'novarix_stock.config', 'novarix_stock.database', 'novarix_stock.models', 'novarix_stock.summary', 'novarix_stock.licensing', 'novarix_stock.images', 'novarix_stock.backup', 'novarix_stock.excel_io', 'novarix_stock.ui', 'novarix_stock.ui.main_window', 'novarix_stock.ui.dialogs', 'novarix_stock.ui.theme', 'novarix_stock.ui.pro_controller', 'novarix_stock.ui.sales_history_window', 'novarix_stock.ui.stock_movement_history_window', 'openpyxl', 'httpx', 'cryptography'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='STOCK by NOVARIX',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets/stock-icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='STOCK by NOVARIX',
)
