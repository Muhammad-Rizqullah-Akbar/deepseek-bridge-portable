"""Registry Akun Portabel: Satu identitas DeepSeek, satu profil browser relatif.

Semua path disimpan relatif terhadap project root agar aman saat dipindahkan
lintas folder, disk, atau sistem operasi.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

from ..core import config

REGISTRY_FILE = Path(os.getenv(
    "DSB_ACCOUNT_REGISTRY",
    getattr(config, "SESSION_BASE", config.ROOT / "session") / "accounts.json",
))


class AccountError(RuntimeError):
    """Masalah pada definisi akun, misalnya path yang bertabrakan."""


@dataclass
class Account:
    """Satu identitas DeepSeek beserta tempat penyimpanan sesinya."""

    name: str
    email: str = ""
    profile_dir: str = ""
    session_file: str = ""
    note: str = ""
    priority: int = 50

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise AccountError("Nama akun wajib diisi.")
        if any(c in self.name for c in '<>:"/\\|?*'):
            raise AccountError(
                f"Nama akun '{self.name}' memuat karakter yang tidak boleh "
                "dipakai pada nama direktori.")

        if not self.profile_dir or not self.session_file:
            sf, pd = config.account_paths(self.name)
            if not self.profile_dir:
                try:
                    self.profile_dir = str(pd.relative_to(config.ROOT))
                except ValueError:
                    self.profile_dir = str(pd)
            if not self.session_file:
                try:
                    self.session_file = str(sf.relative_to(config.ROOT))
                except ValueError:
                    self.session_file = str(sf)

    @property
    def profile(self) -> Path:
        p = Path(self.profile_dir)
        if not p.is_absolute():
            return config.ROOT / p
        return p

    @property
    def session(self) -> Path:
        p = Path(self.session_file)
        if not p.is_absolute():
            return config.ROOT / p
        return p

    def to_dict(self) -> dict:
        d = asdict(self)
        try:
            p_prof = Path(self.profile_dir)
            if p_prof.is_absolute():
                d["profile_dir"] = str(p_prof.relative_to(config.ROOT))
        except ValueError:
            pass
        try:
            p_sess = Path(self.session_file)
            if p_sess.is_absolute():
                d["session_file"] = str(p_sess.relative_to(config.ROOT))
        except ValueError:
            pass
        return d


class AccountRegistry:
    """Kumpulan akun dengan jaminan path relatif dan bebas tabrakan."""

    def __init__(self, accounts: Optional[List[Account]] = None,
                 path: Path = REGISTRY_FILE) -> None:
        self.path = path
        self.accounts: Dict[str, Account] = {}
        for acc in (accounts or []):
            self.add(acc, save=False)

    @classmethod
    def load(cls, path: Path = REGISTRY_FILE) -> "AccountRegistry":
        reg = cls(path=path)
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception as e:
                raise AccountError(
                    f"Registry akun tidak bisa dibaca ({path}): {e}") from e
            for entry in raw.get("accounts", []):
                reg.add(Account(**entry), save=False)
            return reg

        for name in config.ACCOUNT_NAMES:
            if not name or name == "default":
                continue
            sf, pd = config.account_paths(name)
            reg.add(Account(name=name, profile_dir=str(pd),
                            session_file=str(sf)), save=False)
        return reg

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "accounts": [a.to_dict() for a in self.accounts.values()],
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(self.path)

    def add(self, account: Account, save: bool = True) -> None:
        name_key = account.name.strip().lower()
        self.accounts[name_key] = account
        if save:
            self.save()

    def remove(self, name: str, save: bool = True) -> Optional[Account]:
        name_key = name.strip().lower()
        acc = self.accounts.pop(name_key, None)
        if acc and save:
            self.save()
        return acc

    def get(self, name: str) -> Optional[Account]:
        return self.accounts.get(name.strip().lower())

    def list(self) -> List[Account]:
        return list(self.accounts.values())

    def entries(self) -> List[dict]:
        """Kembalikan daftar akun dalam format dictionary untuk sinkronisasi pool."""
        return [a.to_dict() for a in self.accounts.values()]

    def update(self, name: str, **kwargs) -> Account:
        """Perbarui metadata akun (email, note, priority) lalu simpan."""
        acc = self.get(name)
        if not acc:
            raise AccountError(f"Akun '{name}' tidak ditemukan di registry.")
        for k, v in kwargs.items():
            if hasattr(acc, k):
                setattr(acc, k, v)
        self.save()
        return acc

    def collisions(self) -> List[str]:
        """Deteksi apakah ada dua akun yang berbagi direktori profil atau berkas sesi."""
        issues = []
        profiles: Dict[str, str] = {}
        sessions: Dict[str, str] = {}
        for acc in self.accounts.values():
            p_str = str(acc.profile.resolve()).lower()
            s_str = str(acc.session.resolve()).lower()
            if p_str in profiles:
                issues.append(f"Profil browser '{acc.profile_dir}' dipakai ganda oleh akun '{profiles[p_str]}' dan '{acc.name}'")
            else:
                profiles[p_str] = acc.name
            if s_str in sessions:
                issues.append(f"Berkas sesi '{acc.session_file}' dipakai ganda oleh akun '{sessions[s_str]}' dan '{acc.name}'")
            else:
                sessions[s_str] = acc.name
        return issues
