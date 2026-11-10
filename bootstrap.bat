@echo off
setlocal enabledelayedexpansion
title DeepSeek Bridge Portable Launcher

:: 0. Kunci direktori kerja ke folder tempat script ini berada
cd /d "%~dp0"

echo ====================================================================
echo        DEEPSEEK BRIDGE PORTABLE - 1-CLICK BOOTSTRAP (WINDOWS)
echo ====================================================================

:: 1. Deteksi Python 3.10+ di sistem (Menghindari Windows Store fake stub)
set "PYTHON_CMD="

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    py -3 -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
    if !ERRORLEVEL! EQU 0 (
        set "PYTHON_CMD=py -3"
    )
)

if not defined PYTHON_CMD (
    where python >nul 2>nul
    if %ERRORLEVEL% EQU 0 (
        python -c "import sys; exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
        if !ERRORLEVEL! EQU 0 (
            set "PYTHON_CMD=python"
        )
    )
)

if not defined PYTHON_CMD (
    echo.
    echo [ERROR] Python 3.10+ tidak ditemukan di sistem.
    echo Silakan install Python 3.10 atau versi lebih baru dari:
    echo   https://www.python.org/downloads/
    echo PENTING: Pastikan opsi 'Add python.exe to PATH' dicentang saat instalasi!
    echo.
    pause
    exit /b 1
)

echo [*] Interpreter sistem terdeteksi: !PYTHON_CMD!

:: 2. Setup Isolated Virtual Environment (.venv)
if exist ".venv" (
    if not exist ".venv\Scripts\python.exe" (
        echo [*] Direktori .venv sebelumnya tidak lengkap atau korup, membersihkan...
        rmdir /s /q ".venv"
    )
)
if not exist ".venv\Scripts\python.exe" (
    echo [*] Membuat isolated virtual environment (.venv)...
    !PYTHON_CMD! -m venv .venv
    if !ERRORLEVEL! NEQ 0 (
        echo [ERROR] Gagal membuat virtual environment di folder .venv.
        pause
        exit /b 1
    )
)

:: 3. Aktivasi Environment
call "%~dp0.venv\Scripts\activate.bat"
set "VENV_PY=%~dp0.venv\Scripts\python.exe"

:: 4. Verifikasi dan Install Dependensi
"%VENV_PY%" -c "import deepseek_bridge" >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo [*] Memperbarui pip, setuptools, dan wheel...
    "%VENV_PY%" -m pip install --upgrade pip setuptools wheel
    if !ERRORLEVEL! NEQ 0 (
        echo [WARNING] Pembaruan setuptools gagal atau offline, melanjutkan install...
    )

    echo [*] Menginstal dependensi deepseek-bridge-portable...
    "%VENV_PY%" -m pip install -e .
    if !ERRORLEVEL! NEQ 0 (
        echo.
        echo [ERROR] Gagal menginstal paket deepseek_bridge.
        pause
        exit /b 1
    )

    echo [*] Memasang browser engine Playwright (Chromium)...
    "%VENV_PY%" -m playwright install chromium
)

:: 5. Inisialisasi Template & Pre-Flight Check
echo.
echo [*] Melakukan inisialisasi lingkungan...
"%VENV_PY%" -m deepseek_bridge init
echo [*] Menjalankan audit diagnosa sistem (check)...
"%VENV_PY%" -m deepseek_bridge check
if %ERRORLEVEL% NEQ 0 (
    echo [WARNING] Ada audit check yang perlu perhatian.
)

:: 6. Menu Interaktif
echo.
echo ====================================================================
echo                    PILIH AKSI OPERASIONAL:
echo ====================================================================
echo  [1] Jalankan Server Gateway & Buka Dashboard Web (Default)
echo  [2] Hubungkan / Login Akun DeepSeek (Anti-Burst Window)
echo  [3] Buka Browser Desktop Profil Akun (Interaktif)
echo  [4] Kelola Pool Akun & API Key (CLI Status)
echo  [5] Uji Konektivitas Live Streaming (Test Chat)
echo  [6] Hanya Jalankan Server Gateway (Console Only)
echo  [7] Periksa Pembaruan Sistem (Auto-Update & Changelog)
echo  [8] Keluar
echo ====================================================================
set /p USER_CHOICE="Pilihan Anda [1-8] (Default: 1): "

if "%USER_CHOICE%"=="" set "USER_CHOICE=1"
if "%USER_CHOICE%"=="1" (
    echo.
    echo [*] Menjalankan Server Gateway dan Membuka Dashboard Web...
    "%VENV_PY%" -m deepseek_bridge dashboard
    goto :end
)
if "%USER_CHOICE%"=="2" (
    echo.
    set /p ACC_NAME="Masukkan nama akun (Default: default): "
    if "!ACC_NAME!"=="" set "ACC_NAME=default"
    "%VENV_PY%" -m deepseek_bridge login !ACC_NAME!
    echo.
    echo Tekan sembarang tombol untuk kembali menjalankan server...
    pause >nul
    "%VENV_PY%" -m deepseek_bridge dashboard
    goto :end
)
if "%USER_CHOICE%"=="3" (
    echo.
    set /p ACC_NAME="Masukkan nama akun (Default: default): "
    if "!ACC_NAME!"=="" set "ACC_NAME=default"
    "%VENV_PY%" -m deepseek_bridge browse !ACC_NAME!
    goto :end
)
if "%USER_CHOICE%"=="4" (
    echo.
    echo [*] Memeriksa daftar akun pool...
    "%VENV_PY%" -m deepseek_bridge accounts list
    echo.
    echo [*] Memeriksa daftar API key...
    "%VENV_PY%" -m deepseek_bridge keys list
    echo.
    pause
    goto :end
)
if "%USER_CHOICE%"=="5" (
    echo.
    "%VENV_PY%" -m deepseek_bridge test
    echo.
    pause
    goto :end
)
if "%USER_CHOICE%"=="6" (
    echo.
    echo [*] Menjalankan server gateway console...
    "%VENV_PY%" -m deepseek_bridge serve
    goto :end
)
if "%USER_CHOICE%"=="7" (
    echo.
    "%VENV_PY%" -m deepseek_bridge update
    echo.
    pause
    goto :end
)
if "%USER_CHOICE%"=="8" (
    exit /b 0
)

:end
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [INFO] Proses selesai dengan kode exit %ERRORLEVEL%.
    pause
)
