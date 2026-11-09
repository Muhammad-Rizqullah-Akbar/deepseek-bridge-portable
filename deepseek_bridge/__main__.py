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
from .cli import diagnostics, connect, hermes, accounts


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

    # status
    sp_stat = sub.add_parser("status", help="Cek status akun dan sesi")
    sp_stat.set_defaults(func=cmd_status)

    # dashboard
    sp_dash = sub.add_parser("dashboard", aliases=["ui", "gui"], help="Buka Web Dashboard GUI di browser default")
    sp_dash.set_defaults(func=cmd_dashboard)

    # accounts
    sp_acc = sub.add_parser("accounts", help="Kelola pool akun DeepSeek")
    acc_sub = sp_acc.add_subparsers(dest="action")
    sp_acc_list = acc_sub.add_parser("list", help="Daftar akun di pool")
    sp_acc_list.set_defaults(func=accounts.cmd_accounts_list)
    sp_acc_add = acc_sub.add_parser("add", help="Tambah akun baru")
    sp_acc_add.add_argument("name", help="Nama akun (e.g. acc1)")
    sp_acc_add.add_argument("--email", default="", help="Email akun")
    sp_acc_add.set_defaults(func=accounts.cmd_accounts_add)
    sp_acc_del = acc_sub.add_parser("del", help="Hapus akun dari pool")
    sp_acc_del.add_argument("name", help="Nama akun")
    sp_acc_del.set_defaults(func=accounts.cmd_accounts_del)
    sp_acc.set_defaults(func=accounts.cmd_accounts_list)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.subcommand:
        # Default behavior: jika tanpa argumen, jalankan serve
        return cmd_serve(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
