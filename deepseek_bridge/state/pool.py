"""Manajemen pool akun DeepSeek.

Setiap akun punya batas sendiri yang terukur lewat pengujian. Pool ini
mendistribusikan request ke beberapa akun secara serial per akun, sehingga
throughput total bertambah sebanding dengan jumlah akun.

Hasil pengukuran nyata (satu mesin, satu IP):

    Mode Instant (default): 10 request burst, lalu "Messages too frequent".
    Mode Expert:           19 request burst, lalu throttle.
    Pendinginan:           sekitar 17 detik setelah throttle.
    Paralel 2 per akun:    stabil. 3 ke atas mulai ditolak.
    Cakupan throttle:      PER AKUN, bukan per IP. Terbukti: burst lewat Tor
                           dengan IP berbeda tetap kena throttle di request
                           ke-10, angka yang identik.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from ..core import config


@dataclass
class AccountState:
    """Status satu akun DeepSeek di dalam pool."""

    name: str
    session_file: Path
    profile_dir: Path
    # Prioritas rotasi: angka lebih kecil = prioritas lebih awal.
    priority: int = 50
    # Jumlah request yang sudah dikirim pada jendela saat ini.
    sent_in_window: int = 0
    # Waktu request terakhir, untuk menghitung jeda antar request.
    last_request_at: float = 0.0
    # Kalau akun kena throttle, catat kapan ia boleh dicoba lagi.
    cooldown_until: float = 0.0
    # Status mute dari DeepSeek. Mute bertahan berhari-hari, bukan detik.
    muted: bool = False
    mute_until: Optional[float] = None
    # Statistik kumulatif.
    total_ok: int = 0
    total_throttled: int = 0
    # Jumlah request aktif yang sedang berjalan di akun ini.
    in_flight: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        sf = Path(self.session_file)
        if not sf.is_absolute():
            sf = config.ROOT / sf
        self.session_file = sf
        pd = Path(self.profile_dir)
        if not pd.is_absolute():
            pd = config.ROOT / pd
        self.profile_dir = pd

    @property
    def key(self) -> str:
        return self.name

    def acquire_in_flight(self) -> None:
        with self.lock:
            self.in_flight += 1

    def release_in_flight(self) -> None:
        with self.lock:
            self.in_flight = max(0, self.in_flight - 1)

    def is_cooling(self, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.monotonic()
        return now < self.cooldown_until

    def cooldown_remaining(self, now: Optional[float] = None) -> float:
        now = now if now is not None else time.monotonic()
        return max(0.0, self.cooldown_until - now)

    def mark_throttled(self, cooldown: float, now: Optional[float] = None) -> None:
        now = now if now is not None else time.monotonic()
        self.cooldown_until = now + cooldown
        self.total_throttled += 1

    def mark_ok(self, now: Optional[float] = None) -> None:
        now = now if now is not None else time.monotonic()
        self.last_request_at = now
        self.total_ok += 1
        self.sent_in_window += 1


class AccountPool:
    """Pool akun dengan pemilihan berbasis beban dan status pendinginan.

    Strategi pemilihan, urut prioritas:

    1. Akun yang tidak sedang pendinginan dan paling jarang dipakai.
    2. Kalau semua pendinginan, ambil yang pendinginannya paling cepat selesai
       dan tunggu sampai waktunya tiba.

    Dengan begitu satu akun yang kena throttle tidak menjatuhkan keseluruhan
    pool, dan request otomatis dialihkan ke akun lain yang masih segar.
    """

    def __init__(
        self,
        accounts: Optional[List[AccountState]] = None,
        # Jeda minimum antar request pada satu akun, detik.
        min_gap_seconds: float = 1.0,
        # Lama pendinginan setelah throttle, detik.
        cooldown_seconds: float = 20.0,
        # Berkas untuk menyimpan status mute. None mematikan persistensi.
        state_file: Optional[Path] = None,
    ):
        accounts = accounts or []
        self.accounts: Dict[str, AccountState] = {a.name: a for a in accounts}
        self.min_gap_seconds = min_gap_seconds
        self.cooldown_seconds = cooldown_seconds
        self.state_file = state_file
        self._pick_lock = threading.Lock()

    @classmethod
    def from_config(cls, entries: List[dict], **kwargs) -> "AccountPool":
        """Bangun pool dari daftar config akun.

        Setiap entri berbentuk:
            {"name": "akun1",
             "session_file": "session/akun1/session.json",
             "profile_dir": "session/akun1/profile"}
        """
        accounts = [
            AccountState(
                name=e["name"],
                session_file=Path(e["session_file"]),
                profile_dir=Path(e["profile_dir"]),
                priority=int(e.get("priority", 50)),
            )
            for e in entries
        ]
        return cls(accounts, **kwargs)

    def acquire(self, timeout: float = 300.0,
                only: Optional[List[str]] = None) -> Optional[AccountState]:
        """Ambil akun yang siap dipakai. None kalau timeout.

        `only` membatasi pemilihan ke sekumpulan nama akun. Dipakai oleh
        router saat sebuah API key hanya boleh menyentuh akun tertentu.
        """
        deadline = time.monotonic() + timeout
        while True:
            now = time.monotonic()
            chosen = self._pick(now, only=only)
            if chosen is not None:
                return chosen
            if now >= deadline:
                return None
            time.sleep(0.01)

    def _pick(self, now: float,
              only: Optional[List[str]] = None) -> Optional[AccountState]:
        """Pilih akun terbaik, atau None kalau belum ada yang siap.

        `only` membatasi pemilihan ke sekumpulan nama akun. Dipakai oleh
        router saat sebuah API key hanya boleh menyentuh akun tertentu.

        Cabang "semua pendinginan" wajib memakai filter yang sama dengan
        cabang "ready". Kalau tidak, akun di luar `only` bisa terpilih
        sebagai kandidat pendinginan, dan karena tidak ada yang lolos
        filter, fungsi mengembalikan None padahal masih ada akun sehat.
        """
        with self._pick_lock:
            ready = []
            for acc in self.accounts.values():
                if only is not None and acc.name not in only:
                    continue
                if acc.muted and not acc.is_cooling(now):
                    # Mute sudah seharusnya berakhir, bersihkan tandanya.
                    acc.muted = False
                    acc.mute_until = None
                if acc.is_cooling(now):
                    continue
                try:
                    if not acc.session_file.exists() or acc.session_file.stat().st_size < 30:
                        continue
                except OSError:
                    continue
                # Jangan alokasikan ke akun jika request in-flight melebihi batas per akun
                max_conc = getattr(config, "MAX_CONCURRENT_COMPLETIONS", 1)
                if acc.in_flight >= max_conc:
                    continue
                # Pastikan jeda minimum antar request pada akun ini terpenuhi
                if acc.last_request_at and (now - acc.last_request_at) < self.min_gap_seconds:
                    continue
                ready.append(acc)

            if ready:
                # Prioritas utama:
                # 1. in_flight == 0 (akun kosong selalu dipilih lebih dulu untuk request berbeda)
                # 2. sent_in_window terendah (beban terdistribusi merata antar akun kosong)
                # 3. last_request_at paling lama (paling segar/paling lama istirahat)
                # 4. priority statis sebagai penentu akhir
                ready.sort(key=lambda a: (a.in_flight, a.sent_in_window, a.last_request_at, a.priority))
                return ready[0]

            # Semua pendinginan. Cari yang paling cepat selesai, dengan
            # filter yang sama seperti cabang di atas.
            cooling = [
                a for a in self.accounts.values()
                if (only is None or a.name in only) and a.is_cooling(now)
            ]
            if not cooling:
                return None
            soonest = min(cooling, key=lambda a: (a.priority, a.cooldown_until))
            wait = soonest.cooldown_remaining(now)
            if wait <= 0:
                return soonest
            # Tunggu sampai pendinginan selesai, lalu pilih lagi.
            time.sleep(min(wait, 0.05))
            return None

    def report(self, account: AccountState, throttled: bool) -> None:
        """Laporkan hasil request untuk memperbarui status akun."""
        with account.lock:
            if throttled:
                account.mark_throttled(self.cooldown_seconds)
                account.sent_in_window = 0
            else:
                account.mark_ok()

    def mark_throttled(self, account: AccountState, cooldown_seconds: float = 60.0) -> None:
        """Keluarkan akun dari rotasi untuk jangka waktu cooldown_seconds."""
        with account.lock:
            account.mark_throttled(cooldown_seconds)
            account.sent_in_window = 0

    def mark_challenge(self, account: AccountState, cooldown_seconds: Optional[float] = None) -> None:
        """Keluarkan akun dari rotasi sementara karena tantangan CAPTCHA/WAF (code 40303)."""
        cd = cooldown_seconds if cooldown_seconds is not None else getattr(config, "CHALLENGE_COOLDOWN_SECONDS", 60.0)
        with account.lock:
            account.mark_throttled(cd)
            account.sent_in_window = 0

    def clear_cooldown(self, account_name: Optional[str] = None) -> None:
        """Bersihkan status pendinginan cooldown untuk satu akun atau semua akun."""
        with self._pick_lock:
            for acc in self.accounts.values():
                if account_name is None or acc.name == account_name:
                    with acc.lock:
                        acc.cooldown_until = 0.0
                        acc.sent_in_window = 0

    def mark_muted(self, account: AccountState,
                   mute_until: Optional[float] = None) -> None:
        """Keluarkan akun dari rotasi karena di-mute DeepSeek.

        Mute bertahan berhari-hari, jauh lebih lama daripada cooldown biasa.
        Kalau `mute_until` diketahui, pendinginan dihapus tepat pada waktu itu.
        Status disimpan ke disk supaya restart bridge tidak menguji ulang
        akun yang sudah diketahui di-mute.

        `_persist` dipanggil setelah lock dilepas. Memanggilnya di dalam
        blok `with` akan deadlock, karena `_persist` juga mengambil lock
        yang sama dan `threading.Lock` tidak reentrant.
        """
        with account.lock:
            account.muted = True
            account.mute_until = mute_until
            account.total_throttled += 1
            account.sent_in_window = 0
            if mute_until:
                # cooldown memakai monotonic clock, jadi konversi selisihnya.
                delta = float(mute_until) - time.time()
                account.cooldown_until = time.monotonic() + max(0.0, delta)
        self._persist()

    def load_persisted(self, path: Path) -> None:
        """Muat status mute tersimpan dari disk.

        Status hanya dihormati kalau mute-nya belum berakhir. Mute yang
        sudah lewat dibuang, supaya akun otomatis kembali ke rotasi tanpa
        perlu direset manual.
        """
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        now = time.time()
        for name, entry in (data.get("accounts") or {}).items():
            acc = self.accounts.get(name)
            if acc is None:
                continue
            until = entry.get("mute_until")
            if not until or float(until) <= now:
                continue
            # muat tanpa menulis ulang berkas yang baru saja dibaca.
            with acc.lock:
                acc.muted = True
                acc.mute_until = float(until)
                delta = float(until) - now
                acc.cooldown_until = time.monotonic() + max(0.0, delta)

    def _persist(self) -> None:
        """Tulis status mute ke disk, ditulis atomik lewat berkas sementara."""
        if self.state_file is None:
            return
        payload = {"accounts": {}}
        for acc in self.accounts.values():
            with acc.lock:
                if acc.muted and acc.mute_until:
                    payload["accounts"][acc.name] = {
                        "mute_until": acc.mute_until,
                    }
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(self.state_file)
        except Exception:
            # Gagal menulis status bukan alasan untuk menjatuhkan request.
            pass

    def reset_windows(self, window_seconds: float = 60.0) -> None:
        """Reset hitungan jendela. Dipanggil berkala agar pemilihan adil."""
        for acc in self.accounts.values():
            with acc.lock:
                if acc.last_request_at and \
                        time.monotonic() - acc.last_request_at > window_seconds:
                    acc.sent_in_window = 0

    def stats(self) -> List[dict]:
        """Ringkasan status semua akun, untuk endpoint monitoring."""
        now = time.monotonic()
        out = []
        for acc in self.accounts.values():
            out.append({
                "name": acc.name,
                "total_ok": acc.total_ok,
                "total_throttled": acc.total_throttled,
                "sent_in_window": acc.sent_in_window,
                "cooling": acc.is_cooling(now),
                "muted": acc.muted,
                "mute_until": acc.mute_until,
                "cooldown_remaining": round(acc.cooldown_remaining(now), 1),
                "last_request_ago": (
                    round(now - acc.last_request_at, 1)
                    if acc.last_request_at else None
                ),
            })
        return out
