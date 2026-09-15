@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Se necesita Python 3.11 o superior.
    echo Descarguelo desde https://www.python.org/downloads/windows/
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" python -m venv .venv
if errorlevel 1 goto :error

".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo.
echo NOVARIX STOCK esta listo. Abra run_novarix_stock.bat.
pause
exit /b 0

:error
echo.
echo No se pudo completar la preparacion.
pause
exit /b 1

