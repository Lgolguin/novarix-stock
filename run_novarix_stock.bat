@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py
    exit /b %errorlevel%
)

where python >nul 2>nul
if errorlevel 1 (
    echo Python no esta instalado o no se encuentra en PATH.
    echo Instale Python 3.11 o superior y ejecute setup.bat.
    pause
    exit /b 1
)

python -c "import PySide6" >nul 2>nul
if errorlevel 1 (
    echo Falta PySide6. Ejecute setup.bat una vez.
    pause
    exit /b 1
)

python main.py

