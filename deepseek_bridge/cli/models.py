"""CLI manajemen model untuk DeepSeek Bridge."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..core import config


def _get_models_file() -> Path:
    p = getattr(config, "MODELS_FILE", config.ROOT / "session" / "models.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_models_data() -> dict:
    f = _get_models_file()
    if not f.exists():
        data = {
            "models": dict(config.MODEL_MAP),
            "published": list(config.PUBLISHED_MODELS),
            "capabilities": dict(config.MODEL_CAPABILITIES),
        }
        save_models_data(data)
        return data
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data
    except Exception:
        return {
            "models": dict(config.MODEL_MAP),
            "published": list(config.PUBLISHED_MODELS),
            "capabilities": dict(config.MODEL_CAPABILITIES),
        }


def save_models_data(data: dict) -> None:
    f = _get_models_file()
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
    # Update runtime config
    if "models" in data:
        config.MODEL_MAP.clear()
        config.MODEL_MAP.update(data["models"])
    if "published" in data:
        config.PUBLISHED_MODELS = tuple(data["published"])
    if "capabilities" in data:
        config.MODEL_CAPABILITIES.clear()
        config.MODEL_CAPABILITIES.update(data["capabilities"])


def cmd_models(args) -> int:
    action = getattr(args, "action", "list") or "list"
    if action == "list":
        return cmd_models_list(args)
    elif action == "edit":
        return cmd_models_edit(args)
    elif action == "add":
        return cmd_models_add(args)
    elif action in ("del", "delete", "remove"):
        return cmd_models_del(args)
    elif action == "publish":
        return cmd_models_publish(args)
    elif action == "unpublish":
        return cmd_models_unpublish(args)
    else:
        return cmd_models_list(args)


def cmd_models_list(args=None) -> int:
    data = load_models_data()
    models = data.get("models", {})
    published = set(data.get("published", []))
    caps = data.get("capabilities", {})

    print(f"[bridge] File konfigurasi: {_get_models_file()}")
    print(f"[bridge] {len(models)} model/alias terdaftar ({len(published)} dipublikasikan ke 9router):\n")

    # Header
    print(f"  {'Nama Model':<22} {'Target':<10} {'Dipublikasi':<14} {'Thinking':<10} {'Search':<8} {'Context':<10}")
    print(f"  {'-'*22} {'-'*10} {'-'*14} {'-'*10} {'-'*8} {'-'*10}")

    for name, target in models.items():
        is_pub = "[Ya]" if name in published else "[Tidak]"
        c = caps.get(name, caps.get("dsk-auto", {}))
        think = "Ya" if c.get("thinking") else "Tidak"
        srch = "Ya" if c.get("search") else "Tidak"
        ctx = f"{config.CONTEXT_LENGTH:,}" if name in published else "-"
        print(f"  {name:<22} {target:<10} {is_pub:<14} {think:<10} {srch:<8} {ctx:<10}")

    print("\nPerintah:")
    print("  Edit model (interaktif) : dsb models edit")
    print("  Tambah alias model baru : dsb models add <nama> [--target default/expert] [--publish]")
    print("  Publikasikan ke 9router : dsb models publish <nama>")
    print("  Batal publikasi model   : dsb models unpublish <nama>")
    print("  Hapus model/alias       : dsb models del <nama>")
    return 0


def _interactive_model_select(models: list, published: set) -> Optional[int]:
    """Menampilkan menu pemilihan model interaktif (bisa arrow key atau input nomor)."""
    has_msvcrt = False
    try:
        import msvcrt
        has_msvcrt = True
    except ImportError:
        pass

    if not sys.stdin.isatty() or not has_msvcrt:
        print("\n=== PILIH MODEL YANG INGIN DIEDIT ===")
        for idx, (m_name, m_tgt) in enumerate(models, 1):
            is_pub = "Ya" if m_name in published else "Tidak"
            print(f"  [{idx:2d}] {m_name:<22} (Target: {m_tgt}, Publikasi: {is_pub})")
        print("  [ 0] Batal / Keluar")
        try:
            choice = input(f"\nPilih nomor [1-{len(models)}, 0 untuk batal]: ").strip()
            if not choice or choice == "0":
                return None
            val = int(choice)
            if 1 <= val <= len(models):
                return val - 1
            print("[bridge] Pilihan di luar jangkauan.")
            return None
        except (ValueError, KeyboardInterrupt, EOFError):
            return None

    curr = 0
    total = len(models)

    def draw():
        lines = []
        lines.append("=== PILIH MODEL YANG INGIN DIEDIT ===")
        lines.append("Gunakan tombol panah [↑ / ↓] atau ketik nomor, lalu tekan [Enter]:\n")
        for idx, (m_name, m_tgt) in enumerate(models):
            is_pub = "Ya" if m_name in published else "Tidak"
            pointer = " > " if idx == curr else "   "
            lines.append(f"{pointer}[{idx+1:2d}] {m_name:<22} (Target: {m_tgt}, Publikasi: {is_pub})")
        lines.append("       [ 0] Batal / Keluar")
        print("\n".join(lines))

    os.system("cls" if os.name == "nt" else "clear")
    draw()

    while True:
        try:
            ch = msvcrt.getwch()
        except (KeyboardInterrupt, EOFError):
            return None

        if ch in ("\x00", "\xe0"):
            try:
                ch2 = msvcrt.getwch()
            except Exception:
                continue
            if ch2 == "H":  # Up Arrow
                curr = (curr - 1) % total
                os.system("cls" if os.name == "nt" else "clear")
                draw()
            elif ch2 == "P":  # Down Arrow
                curr = (curr + 1) % total
                os.system("cls" if os.name == "nt" else "clear")
                draw()
        elif ch in ("\r", "\n"):
            return curr
        elif ch == "0" or ch.lower() == "q":
            return None
        elif ch.isdigit():
            val = int(ch)
            if 1 <= val <= total:
                curr = val - 1
                os.system("cls" if os.name == "nt" else "clear")
                draw()
        elif ch == "\x03":  # Ctrl+C
            return None


def cmd_models_edit(args) -> int:
    data = load_models_data()
    models = list(data.get("models", {}).items())
    published = set(data.get("published", []))
    caps = data.get("capabilities", {})

    if not models:
        print("[bridge] Belum ada model yang terdaftar.")
        return 1

    target_name = getattr(args, "name", "").strip() if getattr(args, "name", None) else ""
    new_name_arg = getattr(args, "new_name", "").strip() if getattr(args, "new_name", None) else ""

    selected_index = -1
    if target_name:
        for idx, (m_name, _) in enumerate(models):
            if m_name.lower() == target_name.lower():
                selected_index = idx
                break
        if selected_index == -1:
            print(f"[bridge] Model '{target_name}' tidak ditemukan di daftar.")
            return 1
    else:
        selected_index = _interactive_model_select(models, published)
        if selected_index is None or selected_index < 0:
            print("\n[bridge] Pengeditan model dibatalkan.")
            return 0

    old_name, old_target = models[selected_index]
    is_pub_old = old_name in published

    print(f"\nModel terpilih: '{old_name}'")
    print(f"Target saat ini: '{old_target}' | Dipublikasi ke 9router: {'Ya' if is_pub_old else 'Tidak'}")
    print("-" * 60)

    # 1. Masukkan nama baru
    if new_name_arg:
        new_name = new_name_arg
    else:
        try:
            val = input(f"1. Masukkan nama model baru [Enter untuk tetap '{old_name}']: ").strip()
            new_name = val if val else old_name
        except (KeyboardInterrupt, EOFError):
            print("\n[bridge] Dibatalkan.")
            return 0

    # 2. Masukkan target wire
    target_arg = getattr(args, "target", "").strip() if getattr(args, "target", None) else ""
    if target_arg:
        new_target = target_arg
    else:
        try:
            t_prompt = f"2. Target internal DeepSeek wire [1: default (Instant), 2: expert (Pro)] [Enter: {old_target}]: "
            t_val = input(t_prompt).strip()
            if t_val == "1":
                new_target = "default"
            elif t_val == "2":
                new_target = "expert"
            elif t_val in ("default", "expert"):
                new_target = t_val
            else:
                new_target = old_target
        except (KeyboardInterrupt, EOFError):
            print("\n[bridge] Dibatalkan.")
            return 0

    # 3. Masukkan status publikasi
    if getattr(args, "publish", False):
        new_pub = True
    else:
        try:
            default_pub_char = "y" if is_pub_old else "n"
            p_prompt = f"3. Publikasikan ke 9router di /v1/models? [y/n] [Enter: {default_pub_char}]: "
            p_val = input(p_prompt).strip().lower()
            if p_val == "y":
                new_pub = True
            elif p_val == "n":
                new_pub = False
            else:
                new_pub = is_pub_old
        except (KeyboardInterrupt, EOFError):
            print("\n[bridge] Dibatalkan.")
            return 0

    # Terapkan perubahan pada dictionary models
    new_models_dict = {}
    for m_name, m_tgt in models:
        if m_name == old_name:
            new_models_dict[new_name] = new_target
        else:
            new_models_dict[m_name] = m_tgt
    data["models"] = new_models_dict

    # Update published list
    pub_list = data.setdefault("published", [])
    if old_name in pub_list:
        pub_list.remove(old_name)
    if new_pub and new_name not in pub_list:
        pub_list.append(new_name)

    # Update capabilities
    cap_dict = data.setdefault("capabilities", {})
    old_cap = cap_dict.pop(old_name, None)
    if new_pub:
        cap_dict[new_name] = old_cap or dict(config.MODEL_CAPABILITIES.get("dsk-auto", {
            "vision": True,
            "reasoning": True,
            "thinking": True,
            "tools": True,
            "search": True,
            "streaming": True,
        }))

    save_models_data(data)

    print("\n========================================================")
    print(f"[bridge] Sukses! Model '{old_name}' berhasil diperbarui.")
    print(f"  Nama Model   : {new_name}")
    print(f"  Target Wire  : {new_target}")
    print(f"  Dipublikasi  : {'Ya (terbaca di 9router /v1/models)' if new_pub else 'Tidak (alias internal)'}")
    print(f"  File Disimpan : {_get_models_file()}")
    print("========================================================")
    return 0


def cmd_models_add(args) -> int:
    data = load_models_data()
    name = args.name.strip()
    target = getattr(args, "target", "default") or "default"
    target = target.strip()
    if not name:
        print("[bridge] Error: Nama model tidak boleh kosong.")
        return 1

    data.setdefault("models", {})[name] = target
    if getattr(args, "publish", False):
        pub_list = data.setdefault("published", [])
        if name not in pub_list:
            pub_list.append(name)
        data.setdefault("capabilities", {})[name] = dict(config.MODEL_CAPABILITIES.get("dsk-auto", {
            "vision": True,
            "reasoning": True,
            "thinking": True,
            "tools": True,
            "search": True,
            "streaming": True,
        }))
    save_models_data(data)
    pub_str = " (dipublikasikan ke 9router)" if getattr(args, "publish", False) else " (alias internal)"
    print(f"[bridge] Model '{name}' -> target '{target}' berhasil ditambahkan{pub_str}.")
    return 0


def cmd_models_del(args) -> int:
    data = load_models_data()
    name = args.name.strip()
    if name not in data.get("models", {}):
        print(f"[bridge] Error: Model '{name}' tidak ditemukan.")
        return 1

    del data["models"][name]
    if name in data.get("published", []):
        data["published"].remove(name)
    data.get("capabilities", {}).pop(name, None)
    save_models_data(data)
    print(f"[bridge] Model '{name}' berhasil dihapus.")
    return 0


def cmd_models_publish(args) -> int:
    data = load_models_data()
    name = args.name.strip()
    if name not in data.get("models", {}):
        print(f"[bridge] Error: Model '{name}' belum terdaftar. Tambahkan dulu dengan 'dsb models add {name}'.")
        return 1

    pub_list = data.setdefault("published", [])
    if name not in pub_list:
        pub_list.append(name)
    data.setdefault("capabilities", {})[name] = dict(config.MODEL_CAPABILITIES.get("dsk-auto", {
        "vision": True,
        "reasoning": True,
        "thinking": True,
        "tools": True,
        "search": True,
        "streaming": True,
    }))
    save_models_data(data)
    print(f"[bridge] Model '{name}' sekarang dipublikasikan ke /v1/models (dikenali 9router).")
    return 0


def cmd_models_unpublish(args) -> int:
    data = load_models_data()
    name = args.name.strip()
    if name in data.get("published", []):
        data["published"].remove(name)
        save_models_data(data)
        print(f"[bridge] Model '{name}' dihapus dari daftar publikasi (tetap aktif sebagai alias).")
    else:
        print(f"[bridge] Model '{name}' memang tidak dipublikasikan.")
    return 0
