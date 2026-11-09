"""Discovery Engine untuk Profil Hermes.

Mendeteksi instalasi Hermes dan seluruh profil lokal secara otomatis.
Tidak ada batasan profil statis: profil apa pun yang dibuat di Hermes
akan langsung terdeteksi saat runtime.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

from .profile import Profil


def get_hermes_root() -> Path:
    """Cari root instalasi Hermes secara OS-agnostic."""
    custom = os.getenv("HERMES_HOME", "").strip()
    if custom:
        p = Path(custom)
        if p.exists():
            return p

    # Windows default: %LOCALAPPDATA%\hermes
    local_app_data = os.getenv("LOCALAPPDATA", "")
    if local_app_data:
        cand = Path(local_app_data) / "hermes"
        if cand.exists():
            return cand

    # Unix / fallback: ~/.hermes
    home_cand = Path.home() / ".hermes"
    if home_cand.exists():
        return home_cand

    # Default fallback
    if local_app_data:
        return Path(local_app_data) / "hermes"
    return home_cand


def get_hermes_profiles_dir() -> Path:
    """Direktori sub-profil Hermes."""
    return get_hermes_root() / "profiles"


# Persona bawaan untuk profil Xenonite (fallback deskripsi jika profile.yaml kosong)
KNOWN_DESCRIPTIONS = {
    "mr-robot": "Chief Technical Architect & Kinetic Will (Offensive Orchestration)",
    "elliot-alderson": "Zero-Trust Forensic Cryptographer & Reconnaissance Daemon",
    "darlene-alderson": "Tactical Exploitation, Malware Engineering & Social Vector Lead",
    "lisbeth-salander": "Lead Strategic Mastermind & Intelligence Validation",
    "benjamin-eng": "Reverse-Engineering Specialist & Protocol Decompilation",
    "aaron-swartz": "Research Integrity & Open Architecture Advocate",
    "gilfoyle": "Backend Infrastructure & Database Resilience Lead",
    "dinesh": "Full-Stack Security & Application Hardening",
}


def scan_profiles() -> Dict[str, Profil]:
    """Pindai direktori Hermes dan kembalikan seluruh profil aktif."""
    registry: Dict[str, Profil] = {}
    root = get_hermes_root()

    # 1. Profil Default (Root)
    if root.exists():
        default_soul = root / "SOUL.md"
        registry["default"] = Profil(
            nama="default",
            peran="Hermes Primary Default Profile",
            home_dir=root,
        )

    # 2. Sub-Profil di folder profiles/
    profiles_dir = get_hermes_profiles_dir()
    if profiles_dir.exists() and profiles_dir.is_dir():
        for item in profiles_dir.iterdir():
            if not item.is_dir() or item.name.startswith("."):
                continue

            name = item.name
            desc = KNOWN_DESCRIPTIONS.get(name, "Dynamic Hermes Agent Profile")

            # Cek apakah ada deskripsi di profile.yaml
            profile_yaml = item / "profile.yaml"
            if profile_yaml.exists():
                try:
                    for line in profile_yaml.read_text(encoding="utf-8").splitlines():
                        if line.startswith("description:"):
                            desc = line.split(":", 1)[1].strip().strip('"\'')
                            break
                except Exception:
                    pass

            registry[name] = Profil(
                nama=name,
                peran=desc,
                home_dir=item,
            )

    return registry


def get_profil(nama: str) -> Optional[Profil]:
    """Ambil profil spesifik berdasarkan nama."""
    if not nama:
        return None
    kunci = str(nama).strip().lower()
    profiles = scan_profiles()
    if kunci in profiles:
        return profiles[kunci]

    # Fallback dinamis jika profil dibuat on-the-fly
    cand_dir = get_hermes_profiles_dir() / kunci
    if cand_dir.exists():
        return Profil(nama=kunci, peran="Dynamic Profile", home_dir=cand_dir)

    return None


def semua_profil() -> Dict[str, Profil]:
    """Alias kompatibilitas: seluruh profil yang terdeteksi."""
    return scan_profiles()


def nama_profil_valid(nama: str) -> bool:
    """Cek apakah nama profil terdaftar di Hermes."""
    return get_profil(nama) is not None
