from __future__ import annotations
import sys
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    import httpx
except ImportError:
    httpx = None


def check_python() -> Tuple[bool, str]:
    ver = sys.version_info
    ver_str = f"v{ver.major}.{ver.minor}.{ver.micro}"
    if ver.major == 3 and ver.minor >= 10:
        return True, f"Python {ver_str} ({sys.maxsize > 2**32 and '64-bit' or '32-bit'})"
    return False, f"Python {ver_str} (Membutuhkan Python >= 3.10)"


def check_nodejs() -> Tuple[bool, str]:
    node_bin = shutil.which("node")
    if not node_bin:
        return False, "Node.js TIDAK TERDETEKSI di PATH (Dibutuhkan Node >= 22 untuk 9router)"
    try:
        res = subprocess.run([node_bin, "-v"], capture_output=True, text=True, timeout=3)
        raw_ver = res.stdout.strip()
        v_num = raw_ver.lstrip("v").split(".")
        major = int(v_num[0]) if v_num and v_num[0].isdigit() else 0
        if major >= 22:
            return True, f"Node.js {raw_ver} (9router WebIDL & zstd compatible)"
        return False, f"Node.js {raw_ver} (PERINGATAN: 9router butuh Node >= 22.0.0; gunakan nvm use 22)"
    except Exception as e:
        return False, f"Gagal mengecek Node.js: {e}"


def check_native_deps() -> List[Tuple[str, bool, str]]:
    results = []

    # 1. wasmtime
    try:
        import wasmtime
        results.append(("PoW Engine (Wasmtime)", True, f"wasmtime v{getattr(wasmtime, '__version__', 'ready')}"))
    except Exception as e:
        results.append(("PoW Engine (Wasmtime)", False, f"Error: {e} (pip install wasmtime)"))

    # 2. curl-cffi
    try:
        import curl_cffi
        results.append(("TLS Fingerprint (curl-cffi)", True, f"curl_cffi v{curl_cffi.__version__}"))
    except Exception as e:
        results.append(("TLS Fingerprint (curl-cffi)", False, f"Error: {e} (pip install curl-cffi)"))

    # 3. playwright
    try:
        import playwright
        results.append(("Browser Engine (Playwright)", True, "Driver terpasang"))
    except Exception as e:
        results.append(("Browser Engine (Playwright)", False, f"Error: {e} (playwright install chromium)"))

    return results


def check_bridge_port(host: str = "127.0.0.1", port: int = 8787) -> Tuple[bool, str]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        is_used = s.connect_ex((host, port)) == 0
    if is_used:
        if httpx:
            try:
                r = httpx.get(f"http://{host}:{port}/healthz", timeout=1.0)
                if r.status_code == 200:
                    return True, f"Port {port} AKTIF melayani DeepSeek Bridge (Daemon Running)"
            except Exception:
                pass
        return False, f"Port {port} SEDANG DIGUNAKAN oleh proses lain (Tutup proses tersebut atau ubah DSB_PORT)"
    return True, f"Port {port} TERSEDIA (Siap digunakan)"


def check_9router(base_url: str = "http://localhost:20128") -> Tuple[bool, str, Dict[str, Any]]:
    if not httpx:
        return False, "httpx library tidak terpasang", {}
    try:
        t0 = time.time()
        r = httpx.get(f"{base_url.rstrip('/')}/v1/models", timeout=3.0)
        latency = round((time.time() - t0) * 1000, 1)
        if r.status_code == 200:
            data = r.json()
            models_list = [m.get("id") for m in data.get("data", []) if isinstance(m, dict)]
            dsk_models = [m for m in models_list if "dsk" in m]
            if dsk_models:
                return True, f"Online ({latency}ms) — Model '{', '.join(dsk_models)}' aktif di 9router", {"latency_ms": latency, "models": dsk_models}
            return True, f"Online ({latency}ms) — 9router aktif tapi node 'dsk' belum diinjeksi (jalankan: deepseek-bridge connect)", {"latency_ms": latency}
        return False, f"HTTP {r.status_code} dari 9router", {}
    except Exception:
        return False, f"Offline ({base_url} tidak merespons)", {}


def run_full_diagnostics() -> None:
    """Tampilkan audit diagnostik visual di terminal."""
    print("=" * 80)
    print("           DEEPSEEK BRIDGE PORTABLE - PRE-FLIGHT SYSTEM AUDIT")
    print("=" * 80)

    # 1. Runtime & Libraries
    print("\n[1] RUNTIME & BINARY DEPENDENCIES:")
    py_ok, py_msg = check_python()
    print(f"    ├─ {'[OK]' if py_ok else '[FAIL]'} {py_msg}")

    node_ok, node_msg = check_nodejs()
    print(f"    ├─ {'[OK]' if node_ok else '[WARN]'} {node_msg}")

    deps = check_native_deps()
    for i, (name, ok, msg) in enumerate(deps):
        print(f"    ├─ {'[OK]' if ok else '[FAIL]'} {name:<30} : {msg}")

    # Port Check
    port_ok, port_msg = check_bridge_port()
    print(f"    └─ {'[OK]' if port_ok else '[WARN]'} {port_msg}")

    # 2. 9router Gateway
    print("\n[2] 9ROUTER GATEWAY STATUS:")
    r9_ok, r9_msg, _ = check_9router()
    print(f"    └─ {'[OK]' if r9_ok else '[WARN]'} {r9_msg}")

    # 3. Hermes Native Profiles Discovery
    print("\n[3] HERMES DYNAMIC PROFILES:")
    try:
        from ..hermes.discovery import get_hermes_root, scan_profiles
        h_root = get_hermes_root()
        if h_root.exists():
            print(f"    ├─ Hermes Root Detected: {h_root}")
            profiles = scan_profiles()
            if profiles:
                print(f"    ├─ Terdeteksi {len(profiles)} Profil Dinamis:")
                for p_name, p in profiles.items():
                    s_status = f"SOUL.md ({len(p.soul_text() or '')}b)" if p.soul_text() else "No SOUL.md"
                    print(f"    │   • {p_name:<18} : {s_status:<15} | Model: {p.model}")
                print("    └─ Status: Terintegrasi Modular")
            else:
                print("    └─ Belum ada profil yang dibuat di Hermes.")
        else:
            print(f"    └─ [INFO] Hermes direktori belum ada ({h_root}). Jalankan hermes sekali untuk inisialisasi.")
    except Exception as e:
        print(f"    └─ Gagal memindai Hermes: {e}")

    # 4. Account Pool
    print("\n[4] DEEPSEEK ACCOUNT POOL:")
    try:
        from ..state.registry import AccountRegistry
        reg = AccountRegistry.load()
        accs = reg.list()
        if accs:
            print(f"    ├─ Total Akun Terdaftar: {len(accs)}")
            for a in accs:
                has_session = a.session.exists() and a.session.stat().st_size > 10
                stat_str = "Sesi Aktif" if has_session else "Belum Login"
                print(f"    │   • {a.name:<10} ({a.email or 'no-email'}) : {stat_str}")
            print("    └─ Siap digunakan untuk round-robin pooling.")
        else:
            print("    └─ [CLEAN SLATE] Belum ada akun. Buka dashboard http://127.0.0.1:8787 untuk Add Account.")
    except Exception as e:
        print(f"    └─ Gagal membaca registry akun: {e}")

    print("\n" + "=" * 80)
