@echo off
setlocal enabledelayedexpansion
title DeepSeek Bridge Portable Launcher

echo ====================================================================
echo        DEEPSEEK BRIDGE PORTABLE - 1-CLICK BOOTSTRAP
echo ====================================================================

:: 1. Verifikasi Python
where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python tidak ditemukan di PATH.
    echo Silakan install Python 3.10+ dari https://www.python.org/
    pause
    exit /b 1
)

:: 2. Setup Isolated Virtual Environment (.venv)
if not exist ".venv" (
    echo [*] Membuat virtual environment lokal (.venv)...
    python -m venv .venv
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Gagal membuat virtualenv.
        pause
        exit /b 1
    )
)

:: 3. Aktivasi Environment
call .venv\Scripts\activate.bat

:: 4. Install dependensi jika package belum terinstall
python -c "import deepseek_bridge" >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [*] Menginstal dependensi portable dan driver browser...
    pip install -e .
    python -m playwright install chromium
)

:: 5. Inisialisasi Environment & Pre-Flight Check
python -m deepseek_bridge init
python -m deepseek_bridge check

echo.
echo ====================================================================
echo Tekan sembarang tombol untuk menjalankan server Gateway...
echo Atau tutup jendela ini jika hanya ingin melakukan cek.
echo ====================================================================
pause >nul

python -m deepseek_bridge serve
