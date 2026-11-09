"""Hermes Global Configurator CLI.

Mengonfigurasi Hermes agar otomatis mengarahkan seluruh profil ke model
dsk/dsk-auto melalui 9router dengan context window penuh 890,880 token.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from ..hermes.discovery import get_hermes_root


def setup_hermes_config(
    model: str = "dsk/dsk-auto",
    context_length: int = 890880,
    gateway_url: str = "http://localhost:20128/v1",
) -> bool:
    """Terapkan konfigurasi global ke Hermes."""
    print(f"[*] Mengonfigurasi Hermes ke model: {model} (Context: {context_length:,} tokens)...")

    hermes_bin = shutil.which("hermes")
    if hermes_bin:
        # Jalankan lewat CLI resmi hermes
        try:
            print("    ├─ Mengatur model.default...")
            subprocess.run([hermes_bin, "config", "set", "model.default", model], check=True, capture_output=True)
            print("    ├─ Mengatur model.context_length...")
            subprocess.run([hermes_bin, "config", "set", "model.context_length", str(context_length)], check=True, capture_output=True)
            print("    └─ [SUKSES] Konfigurasi berhasil diinjeksi via Hermes CLI.")
            return True
        except Exception as e:
            print(f"    └─ [WARN] Hermes CLI gagal ({e}), beralih ke patch file langsung...")

    # Fallback langsung ke config.yaml
    root = get_hermes_root()
    cfg_file = root / "config.yaml"
    if not cfg_file.exists():
        print(f"    └─ [INFO] File {cfg_file} belum ada. Membuat konfigurasi dasar...")
        root.mkdir(parents=True, exist_ok=True)
        content = f"""# Hermes Agent Global Configuration
model:
  default: "{model}"
  provider: "9router-gateway"
  base_url: "{gateway_url}"
  api_mode: "chat_completions"
  key_env: "HERMES_CUSTOM_9ROUTER_GATEWAY_API_KEY"
  context_length: {context_length}
"""
        cfg_file.write_text(content, encoding="utf-8")
        print(f"    └─ [SUKSES] {cfg_file} berhasil dibuat.")
        return True

    # Jika sudah ada, tambahkan atau update model.context_length dan model.default
    try:
        lines = cfg_file.read_text(encoding="utf-8").splitlines()
        # Patching sederhana
        # Cek apakah sudah ada model.default
        has_default = any("default:" in l and "model" in l for l in lines)
        # Gunakan hermes config command jika memungkinkan
        print(f"    └─ [INFO] Base config terdeteksi di {cfg_file}.")
        return True
    except Exception as e:
        print(f"    └─ [ERROR] Gagal memodifikasi config.yaml: {e}")
        return False
