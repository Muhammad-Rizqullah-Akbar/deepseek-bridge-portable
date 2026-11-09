"""Autentikasi: login manual sekali, capture sesi, lalu pakai ulang.

Browser hanya dipakai untuk membangun sesi signed-in, yaitu menangani form
login dan human-check. Browser tidak dipakai untuk chatting. Setelah itu
bearer token diambil dari `localStorage.userToken` bersama cookie sesi, lalu
diserahkan ke klien HTTP murni di `client.py`.

Profil Chromium persisten membuat human-check jadi urusan sekali saja: setelah
login, run berikutnya menangkap token secara headless tanpa window.

    python -m deepseek_bridge.auth     # login manual, buka window sekali
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

from playwright.sync_api import sync_playwright

from .. import config

log = logging.getLogger("deepseek_bridge.auth")

LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--new-window",
    "--start-maximized",
    "--no-sandbox",
    "--disable-infobars",
    "--no-first-run",
    "--no-default-browser-check",
    "--password-store=basic",
]
IGNORE_DEFAULT_ARGS = ["--enable-automation"]


def _ensure_interactive_desktop() -> None:
    """Pastikan thread dan proses terikat ke desktop interaktif pengguna (WinSta0\\Default)."""
    if sys.platform == "win32":
        try:
            user32 = ctypes.windll.user32
            h_desk = user32.OpenDesktopW("Default", 0, False, 0x01FF)
            if h_desk:
                user32.SetThreadDesktop(h_desk)
        except Exception as e:
            log.debug("ensure_interactive_desktop: %s", e)


def _bring_window_to_front() -> None:
    """Cari window Chrome milik profil ini dan bawa ke foreground."""
    if sys.platform != "win32":
        return
    try:
        user32 = ctypes.windll.user32
        try:
            user32.AllowSetForegroundWindow(-1)
        except Exception:
            pass

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def enum_cb(hwnd, lparam):
            if user32.IsWindowVisible(hwnd):
                cls_buff = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, cls_buff, 256)
                cls_name = cls_buff.value
                if "Chrome_WidgetWin" in cls_name:
                    length = user32.GetWindowTextLengthW(hwnd)
                    buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buff, length + 1)
                    title = buff.value
                    if not title or any(k in title.lower() for k in ["deepseek", "chrome", "blank", "tab"]):
                        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                        user32.SetForegroundWindow(hwnd)
            return True

        user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
    except Exception as e:
        log.debug("bring_to_front: %s", e)


def _clean_stale_profile_locks(profile_dir: Path) -> None:
    """Bersihkan file lock Chrome usang dan matikan proses browser yatim-piatu yang menahan profil."""
    try:
        import psutil
        prof_str = str(profile_dir.resolve()).lower()
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (p.info.get("name") or "").lower()
                if "chrome" in name:
                    cmdline = " ".join(p.info.get("cmdline") or []).lower()
                    if prof_str in cmdline:
                        log.warning("Menemukan proses Chrome yatim-piatu (PID %d) pada profil %s, mematikan...", p.info["pid"], profile_dir.name)
                        p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except Exception as e:
        log.debug("cleanup process error: %s", e)

    time.sleep(0.3)

    for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"):
        lock_p = profile_dir / lock_name
        if lock_p.exists():
            try:
                lock_p.unlink(missing_ok=True)
            except Exception:
                pass
    lock_def = profile_dir / "Default" / "LOCK"
    if lock_def.exists():
        try:
            lock_def.unlink(missing_ok=True)
        except Exception:
            pass


class LoginRequired(RuntimeError):
    """Tidak ada sesi yang bisa dipakai dan login interaktif tidak diizinkan."""

    DEFAULT = (
        "Tidak ada sesi DeepSeek. Login dulu dengan menjalankan:\n"
        "    python -m deepseek_bridge.auth\n"
        "Window browser akan terbuka sekali untuk login manual; setelah itu\n"
        "server memakai sesi tersimpan secara otomatis."
    )

    def __init__(self, message: str = DEFAULT):
        super().__init__(message)


@dataclass
class Session:
    """Sesi DeepSeek yang sudah tertangkap."""

    token: str
    cookies: Dict[str, str]
    user_agent: str
    captured_at: float
    # Device id yang dikirim website di header x-device-id. Opsional supaya
    # berkas sesi lama tetap bisa dimuat tanpa diubah. Kalau kosong, klien
    # memakai nilai turunan yang stabil per sesi, bukan UUID acak tiap request,
    # karena device id yang berubah tiap request justru lebih mudah dikenali
    # daripada device id yang tidak dikirim.
    device_id: str = ""
    # Zona waktu klien dalam detik, dikirim website di
    # x-client-timezone-offset. Diisi dari mesin lokal kalau tidak ada.
    timezone_offset: int = 0

    @property
    def age(self) -> float:
        return time.time() - self.captured_at

    def save(self, path: Path = config.SESSION_FILE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path = config.SESSION_FILE) -> Optional["Session"]:
        if not path.exists():
            return None
        try:
            return cls(**json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return None


# Membaca bearer token yang disimpan web app setelah login. Bentuknya:
#   localStorage.userToken = {"value":"<TOKEN>","__version":"0"}
_READ_TOKEN_JS = """
() => {
  try {
    const raw = window.localStorage.getItem('userToken');
    if (!raw) return null;
    const o = JSON.parse(raw);
    return (o && o.value) ? o.value : null;
  } catch (e) { return null; }
}
"""


def _safe_evaluate(page, js: str):
    """Jalankan page.evaluate, toleran terhadap semua error navigasi/konteks sesaat."""
    try:
        return page.evaluate(js)
    except Exception:
        return None


def _launch(p, headless: bool, profile_dir: Optional[Path] = None):
    """Buka persistent context dengan proteksi anti-deteksi dan pembersihan lock."""
    profile_dir = profile_dir or config.PROFILE_DIR
    config.SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    _clean_stale_profile_locks(profile_dir)

    chrome_error: Optional[Exception] = None
    try:
        return p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=headless,
            channel="chrome",
            args=LAUNCH_ARGS,
            ignore_default_args=IGNORE_DEFAULT_ARGS,
        )
    except Exception as e:
        msg = str(e).lower()
        if "lock" in msg or "singleton" in msg or "profile in use" in msg:
            raise
        chrome_error = e

    try:
        return p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=headless,
            args=LAUNCH_ARGS,
            ignore_default_args=IGNORE_DEFAULT_ARGS,
        )
    except Exception:
        raise chrome_error


def _capture_from_context(context, page) -> Optional[Session]:
    """Ambil token, cookie, user agent, dan device id dari halaman yang sudah login."""
    try:
        token = _safe_evaluate(page, _READ_TOKEN_JS)
        if not token or str(token).strip().lower() in ("null", "none", "undefined", ""):
            return None
        cookies = {c["name"]: c["value"] for c in context.cookies()}
        ua = _safe_evaluate(page, "() => navigator.userAgent") or ""
        dev_id = _safe_evaluate(page, "() => window.localStorage.getItem('deviceId') || window.localStorage.getItem('device_id') || ''") or ""
        return Session(token=token, cookies=cookies, user_agent=ua, captured_at=time.time(), device_id=dev_id)
    except Exception:
        return None


def _wait_for_token(page, timeout: float, context=None) -> Optional[str]:
    """Poll localStorage.userToken dari semua tab sampai muncul atau timeout."""
    deadline = time.time() + timeout
    closed = False
    while time.time() < deadline:
        pages = context.pages if (context and context.pages) else [page]
        for p in pages:
            token = _safe_evaluate(p, _READ_TOKEN_JS)
            if token:
                return token
        try:
            page.wait_for_timeout(1000)
        except Exception as e:
            msg = str(e)
            if "Target page, context or browser has been closed" in msg \
                    or "TargetClosed" in type(e).__name__:
                closed = True
                break
            raise
    if closed:
        print("[auth] Window browser ditutup sebelum login selesai. "
              "Jalankan ulang perintah yang sama untuk mencoba lagi.")
    return None


def _safe_goto(page, url: str) -> None:
    """Navigasi, toleran terhadap net::ERR_ABORTED dari redirect SPA dan WAF."""
    try:
        page.goto(url, wait_until="commit", timeout=60000)
    except Exception as e:
        print(f"[auth] navigasi ke {url} terinterupsi ({type(e).__name__}); "
              "lanjutkan, selesaikan login di window jika diminta.")
    page.wait_for_timeout(2000)


def login(headless: bool = False, assume_logged_out: bool = False,
          profile_dir: Optional[Path] = None,
          session_file: Optional[Path] = None,
          timeout: float = None) -> Session:
    """Login interaktif. Buka window visible, tunggu login manual."""
    profile_dir = profile_dir or config.PROFILE_DIR
    session_file = session_file or config.SESSION_FILE
    if timeout is None:
        timeout = float(os.getenv("DSB_LOGIN_TIMEOUT", "600"))

    if not headless:
        _ensure_interactive_desktop()

    with sync_playwright() as p:
        context = _launch(p, headless=headless, profile_dir=profile_dir)
        page = context.pages[0] if context.pages else context.new_page()

        if not headless:
            _bring_window_to_front()

        existing = None
        if not assume_logged_out:
            _safe_goto(page, config.CHAT_URL)
            existing = _safe_evaluate(page, _READ_TOKEN_JS)

        if not existing:
            _safe_goto(page, config.CHAT_URL)
            print(f"[auth] Silakan selesaikan login di window yang terbuka (termasuk "
                  f"human-check/captcha jika muncul). Menunggu sesi hingga {timeout:.0f} detik...")
            if not _wait_for_token(page, timeout=timeout, context=context):
                context.close()
                raise RuntimeError(
                    f"Login timeout setelah {timeout:.0f} detik, tidak ada "
                    "token yang tertangkap.")

        session = None
        for p_page in context.pages:
            session = _capture_from_context(context, p_page)
            if session and session.token:
                break

        context.close()
        if session is None:
            raise RuntimeError("Sudah login tapi token tidak bisa dibaca.")
        session.save(session_file)
        return session


def _headless_refresh(profile_dir: Optional[Path] = None,
                      session_file: Optional[Path] = None,
                      forbid_token_in: Optional[List[Path]] = None
                      ) -> Optional[Session]:
    """Coba tangkap token headless dari profil persisten."""
    profile_dir = profile_dir or config.PROFILE_DIR
    session_file = session_file or config.SESSION_FILE
    with sync_playwright() as p:
        context = _launch(p, headless=True, profile_dir=profile_dir)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            _safe_goto(page, config.CHAT_URL)
            # Tunggu beberapa detik agar script background WAF/CloudFront menyetel cookie aws-waf-token
            for _ in range(6):
                cookies = {c["name"]: c["value"] for c in context.cookies()}
                if "aws-waf-token" in cookies:
                    break
                page.wait_for_timeout(1000)

            if any("/sign_in" in p.url for p in context.pages):
                log.warning("headless refresh terdeteksi diarahkan ke sign_in, batalkan refresh")
                return None
            session = None
            for p_page in context.pages:
                session = _capture_from_context(context, p_page)
                if session and session.token:
                    break
        finally:
            context.close()

    if session is None:
        return None

    _reject_if_shared(session, forbid_token_in, session_file)
    session.save(session_file)
    return session


class SessionCollisionError(RuntimeError):
    """Token hasil tangkapan ternyata milik akun lain."""


def get_session(allow_interactive: bool = True,
                session_file: Optional[Path] = None,
                profile_dir: Optional[Path] = None,
                forbid_token_in: Optional[List[Path]] = None) -> Session:
    """Kembalikan sesi yang bisa dipakai.

    Urutan: file cache yang masih segar, lalu refresh headless dari profil
    browser. Kalau keduanya gagal dan allow_interactive True, buka window
    visible untuk login manual. Kalau False, raise LoginRequired.

    `session_file` dan `profile_dir` memungkinkan satu pool memakai beberapa
    akun sekaligus, masing-masing dengan sesi tersendiri.

    `forbid_token_in` adalah daftar berkas sesi milik akun lain. Kalau
    token yang akan dikembalikan ternyata sudah dipakai salah satu akun
    itu, sesi ditolak dengan SessionCollisionError. Ini menahan satu
    identitas DeepSeek terpakai dua kali, yang membuat throttling muncul
    seakan-akan dari satu akun padahal berasal dari dua.

    Catatan: memakai Playwright sync API, jadi tidak boleh dipanggil dari dalam
    asyncio event loop. Panggil dari worker thread.
    """
    session_file = session_file or config.SESSION_FILE
    profile_dir = profile_dir or config.PROFILE_DIR

    cached = Session.load(session_file)
    if cached and cached.age < config.SESSION_MAX_AGE:
        _reject_if_shared(cached, forbid_token_in, session_file)
        return cached

    session = _headless_refresh(
        profile_dir=profile_dir, session_file=session_file,
        forbid_token_in=forbid_token_in)
    if session is not None:
        return session

    if not allow_interactive:
        raise LoginRequired()

    print("[auth] Tidak ada sesi valid, membuka window browser untuk login...")
    session = login(profile_dir=profile_dir, session_file=session_file,
                    assume_logged_out=True)
    _reject_if_shared(session, forbid_token_in, session_file)
    return session


def _reject_if_shared(session: "Session",
                      forbid_token_in: Optional[List[Path]],
                      session_file: Path) -> None:
    """Tolak sesi kalau tokennya sudah dipakai akun lain."""
    if not forbid_token_in:
        return
    for other_path in forbid_token_in:
        if other_path is None or Path(other_path) == Path(session_file):
            continue
        if not Path(other_path).exists():
            continue
        try:
            other = Session.load(Path(other_path))
        except Exception:
            continue
        if other is not None and other.token == session.token:
            raise SessionCollisionError(
                f"Token untuk sesi {session_file} sama dengan token di "
                f"{other_path}. Dua akun tidak boleh memakai satu "
                "identitas DeepSeek. Login ulang akun ini pada profil "
                "browsernya sendiri.")


if __name__ == "__main__":
    s = login()
    print(f"[auth] token tertangkap {s.token[:10]}... ({len(s.cookies)} cookies)")
