"""CLI manajemen API Key untuk DeepSeek Bridge."""

from __future__ import annotations

import sys
from ..state.apikeys import get_store, ApiKey


def cmd_keys(args) -> int:
    action = getattr(args, "action", "list") or "list"
    if action == "list":
        return cmd_keys_list(args)
    elif action in ("create", "add", "gen", "new"):
        return cmd_keys_create(args)
    elif action in ("del", "delete", "remove"):
        return cmd_keys_del(args)
    elif action == "enable":
        return cmd_keys_enable(args)
    elif action == "disable":
        return cmd_keys_disable(args)
    else:
        return cmd_keys_list(args)


def cmd_keys_list(args=None) -> int:
    store = get_store()
    keys = store.all()
    print(f"[bridge] File API Key: {store.path}")
    print(f"[bridge] {len(keys)} API Key terdaftar:\n")

    if not keys:
        print("  (Belum ada API key. Buat dengan 'dsb keys create <nama>')")
        return 0

    print(f"  {'Nama':<15} {'Key Token':<38} {'RPM':<9} {'Jeda':<6} {'Conc':<6} {'Kuota':<11} {'Akun':<12} {'Status':<8}")
    print(f"  {'-'*15} {'-'*38} {'-'*9} {'-'*6} {'-'*6} {'-'*11} {'-'*12} {'-'*8}")

    for k in keys:
        stat = "aktif" if k.enabled else "mati"
        quota = f"{k.daily_budget}/hari" if k.daily_budget > 0 else "unlimited"
        accs = ", ".join(k.accounts) if k.accounts else "semua"
        gap = f"{k.min_gap_s:.1f}s" if k.min_gap_s else "-"
        rpm_str = str(k.rpm) if k.rpm > 0 else "unlimited"
        print(f"  {k.name:<15} {k.key:<38} {rpm_str:<9} {gap:<6} {k.max_concurrent:<6} {quota:<11} {accs:<12} {stat:<8}")

    print("\nPerintah:")
    print("  Buat key baru     : dsb keys create <nama> [--rpm 60] [--gap 1.0] [--accounts acc2,acc3]")
    print("  Hapus key         : dsb keys del <nama_atau_key>")
    print("  Matikan / Hidupkan: dsb keys disable <nama_atau_key> / dsb keys enable <nama_atau_key>")
    return 0


def cmd_keys_create(args) -> int:
    store = get_store()
    name = args.name.strip()
    if not name:
        print("[bridge] Error: Nama key tidak boleh kosong.")
        return 1

    # Cek apakah nama sudah ada
    for existing in store.all():
        if existing.name.lower() == name.lower():
            print(f"[bridge] Peringatan: Key dengan nama '{name}' sudah ada ({existing.key}).")

    accounts = [a.strip() for a in args.accounts.split(",") if a.strip()] if getattr(args, "accounts", None) else None
    models = [m.strip() for m in args.models.split(",") if m.strip()] if getattr(args, "models", None) else None

    key = store.create(
        name=name,
        rpm=getattr(args, "rpm", 60),
        min_gap_s=getattr(args, "gap", 1.0),
        max_concurrent=getattr(args, "conc", 2),
        daily_budget=getattr(args, "budget", 0),
        accounts=accounts,
        models=models,
        note=getattr(args, "note", ""),
    )

    print("==================================================================")
    print(f"[bridge] API Key baru berhasil dibuat untuk '{name}'!")
    print(f"  Token Key    : {key.key}")
    print(f"  RPM          : {key.rpm} (jeda min {key.min_gap_s}s)")
    print(f"  Max Conc     : {key.max_concurrent}")
    print(f"  Kuota Harian : {'Tanpa batas' if key.daily_budget == 0 else f'{key.daily_budget} request'}")
    print(f"  Akun Pool    : {', '.join(key.accounts) if key.accounts else 'Semua Akun'}")
    print("==================================================================")
    print("Silakan copy Token Key di atas untuk dipasang di Hermes atau 9router.")
    return 0


def cmd_keys_del(args) -> int:
    store = get_store()
    target = args.target.strip()
    found_key = None
    for k in store.all():
        if k.key == target or k.name.lower() == target.lower():
            found_key = k.key
            break

    if not found_key:
        print(f"[bridge] Error: API key '{target}' tidak ditemukan.")
        return 1

    if store.delete(found_key):
        print(f"[bridge] API key '{target}' ({found_key}) berhasil dihapus.")
        return 0
    else:
        print(f"[bridge] Gagal menghapus API key '{target}'.")
        return 1


def cmd_keys_enable(args) -> int:
    store = get_store()
    target = args.target.strip()
    found_key = None
    for k in store.all():
        if k.key == target or k.name.lower() == target.lower():
            found_key = k.key
            break

    if not found_key:
        print(f"[bridge] Error: API key '{target}' tidak ditemukan.")
        return 1

    store.set_enabled(found_key, True)
    print(f"[bridge] API key '{target}' ({found_key}) telah diaktifkan.")
    return 0


def cmd_keys_disable(args) -> int:
    store = get_store()
    target = args.target.strip()
    found_key = None
    for k in store.all():
        if k.key == target or k.name.lower() == target.lower():
            found_key = k.key
            break

    if not found_key:
        print(f"[bridge] Error: API key '{target}' tidak ditemukan.")
        return 1

    store.set_enabled(found_key, False)
    print(f"[bridge] API key '{target}' ({found_key}) telah dinonaktifkan.")
    return 0
