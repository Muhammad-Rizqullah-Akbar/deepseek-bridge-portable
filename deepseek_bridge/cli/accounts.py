"""CLI Handler untuk Manajemen Akun DeepSeek."""
from __future__ import annotations

import argparse
from typing import Optional

from ..state.registry import Account, AccountRegistry


def cmd_accounts_list(args=None) -> int:
    reg = AccountRegistry.load()
    accs = reg.list()
    if not accs:
        print("[*] Belum ada akun di pool. Tambahkan akun lewat Dashboard (http://127.0.0.1:8787) atau CLI.")
        return 0
    print("=" * 60)
    print(f"{'Nama':<12} {'Email':<25} {'Sesi':<10} {'Prioritas'}")
    print("-" * 60)
    for a in accs:
        has_sess = "Ada" if (a.session.exists() and a.session.stat().st_size > 10) else "Kosong"
        print(f"{a.name:<12} {a.email or '-':<25} {has_sess:<10} {a.priority}")
    print("=" * 60)
    return 0


def cmd_accounts_add(args) -> int:
    name = args.name.strip().lower()
    email = getattr(args, "email", "") or ""
    reg = AccountRegistry.load()
    if reg.get(name):
        print(f"[!] Akun '{name}' sudah ada di registry.")
        return 1
    acc = Account(name=name, email=email)
    reg.add(acc)
    print(f"[V] Akun '{name}' berhasil didaftarkan ke data/session/{name}.")
    print(f"    Jalankan 'deepseek-bridge login {name}' untuk menangkap token sesi.")
    return 0


def cmd_accounts_del(args) -> int:
    name = args.name.strip().lower()
    reg = AccountRegistry.load()
    acc = reg.remove(name)
    if acc:
        print(f"[V] Akun '{name}' berhasil dihapus dari pool.")
        return 0
    print(f"[!] Akun '{name}' tidak ditemukan di registry.")
    return 1
