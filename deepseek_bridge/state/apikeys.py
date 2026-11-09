"""Manajemen API key untuk DeepSeek Bridge.

Tiap pemakai (agent, profil, orang) mendapat key sendiri dengan kuota
sendiri. Key menentukan siapa boleh memakai bridge, seberapa cepat,
dan akun DeepSeek mana yang boleh dia sentuh.

Key disimpan di berkas JSON di sisi bridge, bukan di-hardcode. Format:

    {
      "keys": [
        {
          "key": "dsb-<32 hex>",
          "name": "agent-dinesh",
          "rpm": 30,
          "max_concurrent": 2,
          "daily_budget": 200,
          "accounts": null,
          "models": null,
          "enabled": true,
          "created_at": "2026-10-21T10:00:00",
          "note": "profil dinesh untuk scraping"
        }
      ]
    }

Field:
    rpm           request per menit untuk key ini. 0 berarti tanpa batas.
    max_concurrent completion bersamaan untuk key ini.
    daily_budget  batas request per hari UTC. 0 berarti tanpa batas.
    accounts      daftar nama akun yang boleh dipakai. null berarti semua.
    models        daftar model yang boleh dipakai. null berarti semua.
    enabled       false mematikan key tanpa menghapusnya.

Berkas key dibuat otomatis saat pertama kali server jalan, dengan satu
key admin bernama "admin" supaya setup awal tidak buntu.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

from ..core import config

KEY_FILE = Path(os.getenv(
    "DSB_KEY_FILE",
    config.ROOT / "keys.json",
))

# Prefix agar key mudah dikenali di log dan mudah di-rotate.
KEY_PREFIX = "dsb-"

_lock = threading.RLock()


@dataclass
class ApiKey:
    """Satu API key beserta kuotanya."""

    key: str
    name: str
    rpm: int = 60
    # Jeda minimum antar request dalam detik. Batas RPM saja hanya menjaga
    # rata-rata per menit, sehingga 20 request masih bisa datang dalam lima
    # detik lalu berhenti. Jeda ini yang membuat jarak antar request tetap
    # terlihat seperti orang yang mengetik dan membaca balasan.
    min_gap_s: float = 3.0
    max_concurrent: int = 2
    daily_budget: int = 0
    accounts: Optional[List[str]] = None
    models: Optional[List[str]] = None
    enabled: bool = True
    created_at: str = ""
    note: str = ""
    # Status runtime, tidak disimpan ke disk.
    used_today: int = field(default=0, compare=False)
    window_start: float = field(default=0.0, compare=False)
    # Ordinal tanggal UTC, dipakai untuk reset kuota harian.
    day_start: int = field(default=0, compare=False)
    in_flight: int = field(default=0, compare=False)
    total_requests: int = field(default=0, compare=False)
    total_rejected: int = field(default=0, compare=False)
    # Penghitung token. API web DeepSeek tidak mengembalikan jumlah token,
    # jadi angkanya estimasi dari panjang karakter. Tetap dicatat karena
    # yang penting untuk audit adalah perbandingan antar key dan tren
    # pemakaian, bukan angka absolut yang sempurna.
    tokens_today: int = field(default=0, compare=False)
    tokens_total: int = field(default=0, compare=False)
    prompt_tokens_total: int = field(default=0, compare=False)
    completion_tokens_total: int = field(default=0, compare=False)
    # Timestamp request yang masih di dalam jendela 60 detik.
    rpm_hits: deque = field(default_factory=deque, compare=False)
    # Waktu monotonic request terakhir yang lolos, dipakai menghitung jeda.
    last_request_at: float = field(default=0.0, compare=False)
    # Kunci khusus jeda. Pemeriksaan sisa jeda dan penundaan harus terjadi
    # di dalam satu wilayah kritis, kalau tidak beberapa request yang
    # datang bersamaan akan sama-sama melihat jeda nol lalu keluar serentak.
    # Memakai asyncio.Lock karena jeda ditahan dengan await; kunci thread
    # akan memblokir event loop dan membuat request lain menggantung.
    gap_async_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock, compare=False, repr=False)

    def to_storage(self) -> dict:
        """Bentuk yang ditulis ke disk, tanpa field runtime."""
        d = asdict(self)
        for k in ("used_today", "window_start", "day_start", "in_flight",
                  "total_requests", "total_rejected", "rpm_hits",
                  "last_request_at", "gap_async_lock",
                  "tokens_today", "tokens_total", "prompt_tokens_total",
                  "completion_tokens_total"):
            d.pop(k, None)
        return d

    @classmethod
    def from_storage(cls, d: dict) -> "ApiKey":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    def allows_account(self, account_name: str) -> bool:
        """True kalau key ini boleh memakai akun tersebut."""
        return self.accounts is None or account_name in self.accounts

    def allows_model(self, model: str) -> bool:
        """True kalau key ini boleh memakai model tersebut."""
        return self.models is None or model in self.models

    def _roll_day(self, now: float) -> None:
        """Reset hitungan harian kalau hari UTC sudah berganti.

        `day_start` menyimpan tanggal UTC dalam bentuk ordinal, bukan
        timestamp. Memakai timestamp monotonic akan membuat perbandingan
        tanggal gagal dan hitungan terreset setiap panggilan.
        """
        import datetime
        today = datetime.datetime.now(datetime.timezone.utc).toordinal()
        if self.day_start != today:
            self.day_start = today
            self.used_today = 0
            self.tokens_today = 0

    def quota_remaining(self) -> Optional[int]:
        """Sisa kuota harian, atau None kalau tanpa batas."""
        if self.daily_budget <= 0:
            return None
        self._roll_day(time.monotonic())
        return max(0, self.daily_budget - self.used_today)

    def register_request(self, prompt_tokens: int = 0,
                         completion_tokens: int = 0) -> None:
        """Catat satu request yang lolos beserta perkiraan tokennya."""
        now = time.monotonic()
        self._roll_day(now)
        self.used_today += 1
        self.total_requests += 1
        total = int(prompt_tokens) + int(completion_tokens)
        self.tokens_today += total
        self.tokens_total += total
        self.prompt_tokens_total += int(prompt_tokens)
        self.completion_tokens_total += int(completion_tokens)

    # --- batas RPM ----------------------------------------------------------

    def allow_rpm(self) -> bool:
        """True kalau masih dalam kuota request per menit.

        Sliding window: hit yang lebih tua dari 60 detik dibuang, lalu
        dihitung apakah slot masih tersedia. rpm 0 berarti tanpa batas.
        """
        if self.rpm <= 0:
            return True
        now = time.monotonic()
        with _lock:
            while self.rpm_hits and now - self.rpm_hits[0] > 60.0:
                self.rpm_hits.popleft()
            if len(self.rpm_hits) >= self.rpm:
                return False
            self.rpm_hits.append(now)
            return True

    def retry_after_rpm(self) -> float:
        """Detik yang harus ditunggu sampai slot RPM berikutnya bebas."""
        if self.rpm <= 0 or not self.rpm_hits:
            return 0.0
        now = time.monotonic()
        oldest = self.rpm_hits[0]
        return max(0.0, 60.0 - (now - oldest))

    def gap_remaining(self) -> float:
        """Sisa jeda minimum sebelum request berikutnya boleh jalan.

        Dihitung dari request terakhir yang lolos, bukan dari yang ditolak,
        supaya penolakan tidak memperpanjang waktu tunggu. Nilai 0 berarti
        boleh jalan sekarang.
        """
        if self.min_gap_s <= 0 or self.last_request_at <= 0.0:
            return 0.0
        now = time.monotonic()
        return max(0.0, self.min_gap_s - (now - self.last_request_at))

    def mark_request_sent(self) -> None:
        """Tandai waktu request dikirim, jadi jeda dihitung dari sini."""
        self.last_request_at = time.monotonic()

    def effective_rpm(self) -> int:
        """RPM yang benar-benar bisa dicapai dengan jeda minimum.

        Batas rpm yang tertulis hanya plafon. Laju nyata dibatasi oleh
        min_gap_s, karena satu request butuh minimal jeda itu sebelum
        request berikutnya. Nilai 0 berarti tanpa batas.
        """
        if self.rpm <= 0:
            return 0
        if self.min_gap_s <= 0:
            return self.rpm
        return min(self.rpm, int(60.0 / self.min_gap_s))


class KeyStore:
    """Penyimpanan dan pencarian API key, aman untuk multi-thread."""

    def __init__(self, path: Path = KEY_FILE):
        self.path = path
        self._keys: Dict[str, ApiKey] = {}
        self._last_mtime: float = 0.0
        self._load()

    # --- persistensi --------------------------------------------------------

    def _maybe_reload(self) -> None:
        """Reload dari disk jika file diubah oleh proses lain (misal CLI)."""
        try:
            if not self.path.exists():
                return
            mtime = self.path.stat().st_mtime
            if mtime != self._last_mtime:
                self._load()
        except Exception:
            pass

    def _load(self) -> None:
        if not self.path.exists():
            self._bootstrap()
            return
        try:
            self._last_mtime = self.path.stat().st_mtime
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            new_keys: Dict[str, ApiKey] = {}
            for entry in raw.get("keys", []):
                k = ApiKey.from_storage(entry)
                if k.key:
                    if k.key in self._keys:
                        existing = self._keys[k.key]
                        for attr in ("used_today", "window_start", "day_start", "in_flight",
                                     "total_requests", "total_rejected", "rpm_hits",
                                     "last_request_at", "gap_async_lock",
                                     "tokens_today", "tokens_total", "prompt_tokens_total",
                                     "completion_tokens_total"):
                            if hasattr(existing, attr):
                                setattr(k, attr, getattr(existing, attr))
                    new_keys[k.key] = k
            self._keys = new_keys
        except Exception:
            # Berkas rusak: jangan bikin server mati, buat ulang dari nol.
            if not self._keys:
                self._keys = {}
                self._bootstrap()

    def _save(self) -> None:
        with _lock:
            payload = {"keys": [k.to_storage() for k in self._keys.values()]}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(self.path)
            try:
                self._last_mtime = self.path.stat().st_mtime
            except Exception:
                pass

    def _bootstrap(self) -> None:
        """Buat key admin pertama kali supaya bridge langsung bisa dipakai."""
        token = KEY_PREFIX + secrets.token_hex(16)
        admin = ApiKey(
            key=token,
            name="admin",
            rpm=0,
            max_concurrent=0,
            daily_budget=0,
            note="key awal, ganti atau tambahkan key lain lewat admin API",
            created_at=_now_iso(),
        )
        self._keys[token] = admin
        self._save()

    # --- akses --------------------------------------------------------------

    def get(self, key: str) -> Optional[ApiKey]:
        with _lock:
            self._maybe_reload()
            return self._keys.get(key)

    def all(self) -> List[ApiKey]:
        with _lock:
            self._maybe_reload()
            return list(self._keys.values())

    # --- manajemen ----------------------------------------------------------

    def create(self, name: str, rpm: int = 60, min_gap_s: float = 6.0,
               max_concurrent: int = 2,
               daily_budget: int = 0, accounts: Optional[List[str]] = None,
               models: Optional[List[str]] = None,
               note: str = "") -> ApiKey:
        """Buat key baru. Token dihasilkan acak dan hanya ditampilkan sekali."""
        with _lock:
            token = KEY_PREFIX + secrets.token_hex(16)
            k = ApiKey(
                key=token, name=name, rpm=rpm, min_gap_s=min_gap_s,
                max_concurrent=max_concurrent,
                daily_budget=daily_budget, accounts=accounts, models=models,
                note=note, created_at=_now_iso(),
            )
            self._keys[token] = k
            self._save()
            return k

    def delete(self, key: str) -> bool:
        with _lock:
            if key in self._keys:
                del self._keys[key]
                self._save()
                return True
            return False

    def set_enabled(self, key: str, enabled: bool) -> bool:
        with _lock:
            k = self._keys.get(key)
            if not k:
                return False
            k.enabled = enabled
            self._save()
            return True

    def update(self, key: str, **fields) -> bool:
        with _lock:
            k = self._keys.get(key)
            if not k:
                return False
            for name, value in fields.items():
                if name in ApiKey.__dataclass_fields__ and value is not None:
                    setattr(k, name, value)
            self._save()
            return True


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds")


# Satu instance dipakai bersama seluruh aplikasi.
_store: Optional[KeyStore] = None
_store_lock = threading.Lock()


def get_store() -> KeyStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = KeyStore()
    return _store
