"""Model Profil Hermes Dinamis dan Modular.

Mendukung pembacaan SOUL.md lokal, identitas dinamis, dan isolasi thread.
Bebas hardcode path absolut (beradaptasi otomatis ke Windows/Linux/macOS).
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class Profil:
    """Satu identitas profil Hermes."""
    nama: str
    peran: str = "Hermes Autonomous Agent"
    home_dir: Optional[Path] = None
    model: str = "dsk/dsk-auto"
    context_length: int = 890880

    @property
    def home(self) -> Path:
        if self.home_dir:
            return self.home_dir
        from .discovery import get_hermes_profiles_dir, get_hermes_root
        if self.nama == "default":
            return get_hermes_root()
        return get_hermes_profiles_dir() / self.nama

    @property
    def soul_path(self) -> Path:
        return self.home / "SOUL.md"

    def soul_text(self) -> Optional[str]:
        """Ambil isi SOUL.md profil secara realtime."""
        try:
            p = self.soul_path
            if p.exists() and p.is_file():
                content = p.read_text(encoding="utf-8").strip()
                return content if content else None
        except OSError:
            pass
        return None

    def soul_hash(self) -> str:
        """Hash SHA256 dari konten SOUL.md untuk mendeteksi perubahan kepribadian."""
        text = self.soul_text() or self.peran
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
