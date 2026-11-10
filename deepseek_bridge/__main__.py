"""Entry Point CLI DeepSeek Bridge Portable.

Perintah Utama:
    deepseek-bridge check        Auditor diagnosa runtime & dependensi
    deepseek-bridge init         Inisialisasi lingkungan & template data
    deepseek-bridge serve        Jalankan OpenAI-compatible gateway (default)
    deepseek-bridge connect      Auto-injeksi node & connection ke 9router
    deepseek-bridge setup-hermes Konfigurasi global Hermes ke dsk/dsk-auto (890k context)
    deepseek-bridge accounts     Kelola pool akun DeepSeek
    deepseek-bridge keys         Kelola API keys internal
    deepseek-bridge login        Login manual browser (Playwright capture)
    deepseek-bridge status       Cek masa aktif token & sesi tersimpan
    deepseek-bridge test         Kirim satu prompt uji ke DeepSeek
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import uvicorn

from .core import config
from .cli import diagnostics, connect, hermes, accounts, keys, models, logs, updater


def cmd_check(args) -> int:
    """Jalankan pre-flight system auditor."""
    diagnostics.run_full_diagnostics()
    return 0


def cmd_init(args) -> int:
    """Inisialisasi direktori lokal dan file konfigurasi."""
    print("=" * 80)
    print("      DEEPSEEK BRIDGE PORTABLE - INITIALIZING ENVIRONMENT")
    print("=" * 80)
    root = config.ROOT
    data_dir = root / "data"

    # 1. Direktori Runtime
    for sub in ["session", "state", "wasm"]:
        p = data_dir / sub
        p.mkdir(parents=True, exist_ok=True)
        print(f"[*] Direktori data: {p.relative_to(root)} [OK]")

    # 2. Template .env
    env_file = root / ".env"
    example_env = root / ".env.example"
    if not env_file.exists() and example_env.exists():
        shutil.copy2(example_env, env_file)
        print("[*] Membuat file .env dari template .env.example [OK]")
    else:
        print("[*] File .env sudah ada [OK]")

    # 3. Template accounts.json
    acc_file = data_dir / "session" / "accounts.json"
    if not acc_file.exists():
        acc_file.write_text('{\n  "accounts": []\n}\n', encoding="utf-8")
        print("[*] Inisialisasi data/session/accounts.json [OK]")

    # 4. Playwright Browser
    print("[*] Memeriksa browser Playwright Chromium...")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                b = p.chromium.launch(headless=True)
                b.close()
                print("    └─ Playwright Chromium siap [OK]")
            except Exception:
                print("    └─ Mengunduh Chromium headless...")
                subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
                print("    └─ Download Chromium selesai [OK]")
    except Exception as e:
        print(f"    └─ [WARN] Gagal menginisialisasi browser: {e}")

    print("\n[SUCCESS] Inisialisasi selesai. Jalankan 'deepseek-bridge check' untuk verifikasi.")
    print("=" * 80)
    return 0


def cmd_connect(args) -> int:
    """Auto-inject node ke 9router."""
    return connect.cmd_connect_generate(args)


def cmd_setup_hermes(args) -> int:
    """Konfigurasi global Hermes ke model dsk/dsk-auto."""
    ok = hermes.setup_hermes_config(
        model=getattr(args, "model", "dsk/dsk-auto"),
        context_length=getattr(args, "context_length", 890880),
    )
    return 0 if ok else 1


def cmd_serve(args) -> int:
    """Jalankan server OpenAI-compatible gateway dengan dashboard pre-flight visual."""
    from .hermes.discovery import get_hermes_root, scan_profiles
    from .state.registry import AccountRegistry

    h_root = get_hermes_root()
    profiles = scan_profiles()
    reg = AccountRegistry.load()
    acc_count = len(reg.list())

    print("=" * 80)
    print("             DEEPSEEK BRIDGE - PORTABLE ENTERPRISE GATEWAY")
    print("=" * 80)
    print(f"[Core]    Host: http://{config.HOST}:{config.PORT}/v1 | Health: /healthz")
    print(f"[Models]  Published: {', '.join(getattr(config, 'PUBLISHED_MODELS', ['dsk-auto', 'dsk']))}")
    print(f"[Pool]    Akun Terdaftar: {acc_count} akun (Dynamic Round-Robin)")
    print(f"[Hermes]  Detected at: {h_root} ({len(profiles)} Profil Dinamis Terhubung)")
    print("-" * 80)
    print("Dashboard Web: http://127.0.0.1:8787/")
    print("Tekan Ctrl+C untuk menghentikan server.")
    print("=" * 80 + "\n")

    host = "127.0.0.1" if config.HOST in ("127.0.0.1", "localhost") else "0.0.0.0"

    from .cli.diagnostics import is_port_in_use, is_bridge_running
    if is_port_in_use(host, config.PORT):
        if is_bridge_running(host, config.PORT):
            print(f"[!] INFO: DeepSeek Bridge SUDAH AKTIF di latar belakang pada http://127.0.0.1:{config.PORT}/ (Daemon Running).")
            print(f"[*] Hermes dan 9router sudah terhubung dan siap digunakan secara langsung.")
            print(f"[*] Dashboard Web: http://127.0.0.1:{config.PORT}/")
            print(f"[*] Anda tidak perlu menjalankan server baru lagi di terminal ini.\n")
            return 0
        else:
            print(f"[!] PERINGATAN: Port {config.PORT} sedang digunakan oleh proses lain.")
            print(f"[*] Tutup proses yang memakai port {config.PORT} atau gunakan DSB_PORT=<port> baru.\n")
            return 1

    uvicorn.run(
        "deepseek_bridge.core.server:app",
        host=host,
        port=config.PORT,
        reload=getattr(args, "reload", False),
        log_level="info",
    )
    return 0


def cmd_login(args) -> int:
    from .browser import auth
    return auth.interactive_login(getattr(args, "account", "default"))


def cmd_status(args) -> int:
    from .state.registry import AccountRegistry
    reg = AccountRegistry.load()
    accs = reg.list()
    print(f"Total akun di registry: {len(accs)}")
    for a in accs:
        print(f" - {a.name}: {a.email or 'no email'} (Session: {'ada' if a.session.exists() else 'kosong'})")
    return 0


def cmd_dashboard(args) -> int:
    """Buka Web Dashboard GUI di browser default."""
    import time
    import threading
    import webbrowser
    from .cli.diagnostics import check_bridge_port

    host = getattr(config, "HOST", "127.0.0.1")
    if host in ("::", "0.0.0.0"):
        host = "127.0.0.1"
    port = getattr(config, "PORT", 8787)
    url = f"http://{host}:{port}/"

    is_running, _ = check_bridge_port(host, port)
    if is_running:
        print(f"[*] Gateway aktif. Membuka Dashboard Web di browser: {url}")
        webbrowser.open(url)
        return 0

    print(f"[*] Gateway belum aktif di {url}. Menjalankan server dan membuka Dashboard...")
    def _open_delayed():
        time.sleep(1.2)
        webbrowser.open(url)
    threading.Thread(target=_open_delayed, daemon=True).start()
    return cmd_serve(args)


def cmd_browse(args) -> int:
    """Buka browser persistent profile DeepSeek secara interaktif untuk satu akun."""
    from .browser import browse
    account = getattr(args, "account", "default") or "default"
    return browse.run_browse(account)


def cmd_test(args) -> int:
    """Kirim prompt chat uji ke DeepSeek Bridge untuk memverifikasi fungsionalitas streaming."""
    import json
    import time
    import requests
    host = getattr(args, "host", "127.0.0.1")
    port = getattr(args, "port", getattr(config, "PORT", 8787))
    model = getattr(args, "model", "dsk-auto")
    prompt = getattr(args, "prompt", "Halo DeepSeek, tes konektivitas satu kalimat.")
    key = getattr(args, "key", "test")

    url = f"http://{host}:{port}/v1/chat/completions"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
    }

    print("=" * 80)
    print("           DEEPSEEK BRIDGE - LIVE STREAMING TEST")
    print("=" * 80)
    print(f"[*] Target Endpoint : {url}")
    print(f"[*] Model           : {model}")
    print(f"[*] Prompt          : {prompt}")
    print("-" * 80)

    try:
        t0 = time.time()
        r = requests.post(url, json=payload, headers=headers, stream=True, timeout=60)
        if r.status_code != 200:
            print(f"[ERROR] HTTP {r.status_code}: {r.text}")
            return 1

        print("[*] Stream Output:")
        first_chunk = True
        ttft = 0.0
        for line in r.iter_lines(decode_unicode=True):
            if not line:
                continue
            if line.startswith("data: "):
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    reasoning = delta.get("reasoning_content", "")
                    if first_chunk:
                        ttft = time.time() - t0
                        first_chunk = False
                    if reasoning:
                        sys.stdout.write(reasoning)
                        sys.stdout.flush()
                    if content:
                        sys.stdout.write(content)
                        sys.stdout.flush()
                except Exception:
                    pass
        print(f"\n\n[SUCCESS] Selesai dalam {time.time() - t0:.2f}s (TTFT: {ttft:.2f}s)")
        print("=" * 80)
        return 0
    except requests.exceptions.ConnectionError:
        print(f"[!] Server gateway belum aktif di {url}.")
        print("[*] Jalankan 'deepseek-bridge serve' terlebih dahulu di terminal lain.")
        return 1
    except Exception as e:
        print(f"[ERROR] Tes gagal: {e}")
        return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="deepseek-bridge",
        description="Portable, Zero-Cost OpenAI-Compatible Gateway for DeepSeek Latest (Chat & Reasoner) & Hermes",
    )
    sub = p.add_subparsers(dest="subcommand")

    # serve
    sp_serve = sub.add_parser("serve", help="Jalankan OpenAI-compatible gateway")
    sp_serve.add_argument("--reload", action="store_true", help="Auto-reload on changes")
    sp_serve.set_defaults(func=cmd_serve)

    # check
    sp_check = sub.add_parser("check", help="Audit diagnosa runtime & dependensi")
    sp_check.set_defaults(func=cmd_check)

    # init
    sp_init = sub.add_parser("init", help="Inisialisasi template data & dependensi browser")
    sp_init.set_defaults(func=cmd_init)

    # connect
    sp_conn = sub.add_parser("connect", help="Injeksi otomatis node & connection ke 9router")
    sp_conn.add_argument("name", nargs="?", default="xenonite", help="Nama API key bridge")
    sp_conn.add_argument("--router-url", default="http://localhost:20128", help="URL 9router")
    sp_conn.set_defaults(func=cmd_connect)

    # setup-hermes
    sp_h = sub.add_parser("setup-hermes", help="Konfigurasi global Hermes ke dsk/dsk-auto (890k context)")
    sp_h.add_argument("--model", default="dsk/dsk-auto", help="Model default")
    sp_h.add_argument("--context-length", type=int, default=890880, help="Context length")
    sp_h.set_defaults(func=cmd_setup_hermes)

    # login
    sp_login = sub.add_parser("login", help="Login manual akun via Playwright window")
    sp_login.add_argument("account", nargs="?", default="default", help="Nama akun")
    sp_login.set_defaults(func=cmd_login)

    # browse / open
    sp_browse = sub.add_parser("browse", aliases=["open"], help="Buka browser desktop interaktif untuk satu akun")
    sp_browse.add_argument("account", nargs="?", default="default", help="Nama akun target (default: default)")
    sp_browse.set_defaults(func=cmd_browse)

    # status
    sp_stat = sub.add_parser("status", help="Cek status akun dan sesi")
    sp_stat.set_defaults(func=cmd_status)

    # dashboard
    sp_dash = sub.add_parser("dashboard", aliases=["ui", "gui"], help="Buka Web Dashboard GUI di browser default")
    sp_dash.set_defaults(func=cmd_dashboard)

    # accounts
    sp_acc = sub.add_parser("accounts", aliases=["acc"], help="Kelola pool akun DeepSeek")
    acc_sub = sp_acc.add_subparsers(dest="action")
    sp_acc_list = acc_sub.add_parser("list", help="Daftar akun di pool")
    sp_acc_list.set_defaults(func=accounts.cmd_accounts_list)
    sp_acc_add = acc_sub.add_parser("add", help="Tambah akun baru")
    sp_acc_add.add_argument("name", help="Nama akun (e.g. acc1)")
    sp_acc_add.add_argument("--email", default="", help="Email akun")
    sp_acc_add.set_defaults(func=accounts.cmd_accounts_add)
    sp_acc_del = acc_sub.add_parser("del", aliases=["delete", "remove"], help="Hapus akun dari pool")
    sp_acc_del.add_argument("name", help="Nama akun")
    sp_acc_del.set_defaults(func=accounts.cmd_accounts_del)
    sp_acc.set_defaults(func=accounts.cmd_accounts_list)

    # keys
    sp_keys = sub.add_parser("keys", help="Kelola API keys gateway")
    k_sub = sp_keys.add_subparsers(dest="action")
    sp_k_list = k_sub.add_parser("list", help="Daftar semua API key")
    sp_k_list.set_defaults(func=keys.cmd_keys_list)
    sp_k_create = k_sub.add_parser("create", aliases=["add", "gen", "new"], help="Buat API key baru")
    sp_k_create.add_argument("name", help="Nama API key")
    sp_k_create.add_argument("--rpm", type=int, default=60, help="Batas request per menit (0 = tanpa batas)")
    sp_k_create.add_argument("--gap", type=float, default=1.0, help="Jeda minimum antar request (detik)")
    sp_k_create.add_argument("--conc", type=int, default=2, help="Maksimum konkurensi bersamaan")
    sp_k_create.add_argument("--budget", type=int, default=0, help="Kuota harian (0 = tanpa batas)")
    sp_k_create.add_argument("--accounts", help="Filter akun tertentu (koma: acc1,acc2)")
    sp_k_create.add_argument("--models", help="Filter model tertentu (koma: dsk-auto,dsk)")
    sp_k_create.add_argument("--note", default="", help="Catatan untuk key ini")
    sp_k_create.set_defaults(func=keys.cmd_keys_create)
    sp_k_del = k_sub.add_parser("del", aliases=["delete", "remove"], help="Hapus API key")
    sp_k_del.add_argument("target", help="Nama atau token key yang akan dihapus")
    sp_k_del.set_defaults(func=keys.cmd_keys_del)
    sp_k_en = k_sub.add_parser("enable", help="Aktifkan API key")
    sp_k_en.add_argument("target", help="Nama atau token key")
    sp_k_en.set_defaults(func=keys.cmd_keys_enable)
    sp_k_dis = k_sub.add_parser("disable", help="Nonaktifkan API key")
    sp_k_dis.add_argument("target", help="Nama atau token key")
    sp_k_dis.set_defaults(func=keys.cmd_keys_disable)
    sp_keys.set_defaults(func=keys.cmd_keys)

    # models
    sp_models = sub.add_parser("models", help="Kelola alias dan publikasi model")
    m_sub = sp_models.add_subparsers(dest="action")
    sp_m_list = m_sub.add_parser("list", help="Daftar model dan alias terdaftar")
    sp_m_list.set_defaults(func=models.cmd_models_list)
    sp_m_edit = m_sub.add_parser("edit", help="Edit model secara interaktif atau via argumen")
    sp_m_edit.add_argument("--name", help="Nama model yang akan diedit")
    sp_m_edit.add_argument("--new-name", help="Nama baru")
    sp_m_edit.add_argument("--target", help="Target wire internal (default / expert)")
    sp_m_edit.add_argument("--publish", action="store_true", help="Publikasikan ke /v1/models")
    sp_m_edit.set_defaults(func=models.cmd_models_edit)
    sp_m_add = m_sub.add_parser("add", help="Tambah alias model baru")
    sp_m_add.add_argument("name", help="Nama alias model")
    sp_m_add.add_argument("--target", default="default", help="Target wire internal (default / expert)")
    sp_m_add.add_argument("--publish", action="store_true", help="Publikasikan ke /v1/models")
    sp_m_add.set_defaults(func=models.cmd_models_add)
    sp_m_del = m_sub.add_parser("del", aliases=["delete", "remove"], help="Hapus alias model")
    sp_m_del.add_argument("name", help="Nama alias model")
    sp_m_del.set_defaults(func=models.cmd_models_del)
    sp_m_pub = m_sub.add_parser("publish", help="Publikasikan model ke /v1/models")
    sp_m_pub.add_argument("name", help="Nama model")
    sp_m_pub.set_defaults(func=models.cmd_models_publish)
    sp_m_unpub = m_sub.add_parser("unpublish", help="Batal publikasikan model")
    sp_m_unpub.add_argument("name", help="Nama model")
    sp_m_unpub.set_defaults(func=models.cmd_models_unpublish)
    sp_models.set_defaults(func=models.cmd_models)

    # log / logs
    sp_log = sub.add_parser("log", aliases=["logs"], help="Pantau telemetry dan request log SQLite")
    sp_log.add_argument("--key", help="Filter berdasarkan nama API key")
    sp_log.add_argument("--account", help="Filter berdasarkan nama akun")
    sp_log.add_argument("--status", help="Filter berdasarkan status (ok, throttled, error)")
    sp_log.add_argument("--session", help="Filter berdasarkan ID thread sesi")
    sp_log.add_argument("--limit", type=int, default=20, help="Jumlah entri terakhir (default: 20)")
    sp_log.add_argument("-f", "--follow", action="store_true", help="Tail log realtime terus menerus")
    sp_log.add_argument("--json", action="store_true", help="Output format raw JSON")
    sp_log.set_defaults(func=logs.cmd_log)

    # test
    sp_test = sub.add_parser("test", help="Kirim prompt uji streaming ke bridge")
    sp_test.add_argument("prompt", nargs="?", default="Halo DeepSeek, tes konektivitas satu kalimat.", help="Prompt pengujian")
    sp_test.add_argument("--model", default="dsk-auto", help="Model target (default: dsk-auto)")
    sp_test.add_argument("--key", default="test", help="API key authorization")
    sp_test.add_argument("--host", default="127.0.0.1", help="Host server")
    sp_test.add_argument("--port", type=int, default=8787, help="Port server")
    sp_test.set_defaults(func=cmd_test)

    # update / upgrade
    sp_up = sub.add_parser("update", aliases=["upgrade"], help="Periksa dan terapkan pembaruan sistem dari GitHub")
    sp_up.add_argument("--check", action="store_true", help="Hanya periksa pembaruan tanpa menerapkan")
    sp_up.add_argument("-y", "--yes", action="store_true", help="Otomatis terapkan pembaruan tanpa konfirmasi interaktif")
    sp_up.set_defaults(func=updater.cmd_update)

    # version
    sp_ver = sub.add_parser("version", help="Tampilkan versi aktif, commit hash, dan status repositori")
    sp_ver.set_defaults(func=updater.cmd_version)

    return p


def main(argv: list[str] | None = None) -> int:
    try:
        if config.ROOT.exists():
            os.chdir(config.ROOT)
    except Exception:
        pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.subcommand:
        # Default behavior: jika tanpa argumen, jalankan serve
        return cmd_serve(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
