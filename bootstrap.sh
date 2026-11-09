#!/usr/bin/env bash
set -e

# 0. Kunci direktori kerja ke folder script berada
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "===================================================================="
echo "       DEEPSEEK BRIDGE PORTABLE - 1-CLICK BOOTSTRAP (UNIX)"
echo "===================================================================="

# 1. Verifikasi Python
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] Python 3 tidak ditemukan. Silakan pasang Python >= 3.10."
    exit 1
fi

# 2. Setup Virtual Environment
if [ ! -d ".venv" ]; then
    echo "[*] Membuat virtual environment (.venv)..."
    python3 -m venv .venv
fi

# 3. Aktivasi Environment
source .venv/bin/activate

# 4. Install dependensi jika package belum terpasang
if ! python3 -c "import deepseek_bridge" &>/dev/null; then
    echo "[*] Memperbarui pip, setuptools, dan wheel..."
    pip install --upgrade pip setuptools wheel
    echo "[*] Menginstal dependensi package deepseek_bridge..."
    pip install -e .
    echo "[*] Memasang browser engine Playwright (Chromium)..."
    python3 -m playwright install chromium
fi

# 5. Inisialisasi Environment & Pre-Flight Check
python3 -m deepseek_bridge init
python3 -m deepseek_bridge check

echo ""
echo "===================================================================="
echo "Inisialisasi selesai. Menjalankan server Gateway..."
echo "===================================================================="

python3 -m deepseek_bridge serve
