"""Autentikasi: login manual sekali, capture sesi, lalu pakai ulang.

Browser hanya dipakai untuk membangun sesi signed-in, yaitu menangani form
login dan human-check. Browser tidak dipakai untuk chatting. Setelah itu
bearer token diambil dari `localStorage.userToken` bersama cookie sesi, lalu
diserahkan ke klien HTTP murni di `client.py`.

Profil Chromium persisten membuat human-check jadi urusan sekali saja: setelah
login, run berikutnya menangkap token secara headless tanpa window.

    python -m deepseek_bridge login [nama_akun]
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
from typing import Dict, List, Optional, Tuple

from playwright.sync_api import sync_playwright

from ..core import config

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
    """Cari window Chrome/Edge milik profil ini dan bawa ke foreground."""
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
                    if not title or any(k in title.lower() for k in ["deepseek", "chrome", "edge", "blank", "tab"]):
                        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                        user32.SetForegroundWindow(hwnd)
            return True

        user32.EnumWindows(WNDENUMPROC(enum_cb), 0)
    except Exception as e:
        log.debug("bring_to_front: %s", e)


def _clean_stale_profile_locks(profile_dir: Path) -> None:
    """Bersihkan file lock browser usang dan matikan proses browser yatim-piatu yang menahan profil."""
    try:
        import psutil
        prof_str = str(profile_dir.resolve()).lower()
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (p.info.get("name") or "").lower()
                if "chrome" in name or "msedge" in name:
                    cmdline = " ".join(p.info.get("cmdline") or []).lower()
                    if prof_str in cmdline:
                        log.warning("Menemukan proses browser yatim-piatu (PID %d) pada profil %s, mematikan...", p.info["pid"], profile_dir.name)
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
        "    deepseek-bridge login\n"
        "Jendela browser akan terbuka sekali untuk login manual; setelah itu\n"
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
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path = config.SESSION_FILE) -> Optional["Session"]:
        path = Path(path)
        if not path.exists():
            return None
        try:
            return cls(**json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return None


# Membaca bearer token yang disimpan web app setelah login.
# Verifikasi ketat: HANYA dievaluasi pada origin deepseek.com dan HANYA menerima format JWT (ey...).
_READ_TOKEN_JS = """
() => {
  try {
    if (!window.location.hostname.includes("deepseek.com")) {
      return null;
    }
    const raw = window.localStorage.getItem('userToken');
    if (!raw) return null;
    let token = null;
    try {
      const o = JSON.parse(raw);
      token = (o && o.value) ? o.value : (typeof o === 'string' ? o : null);
    } catch (e) {
      token = raw;
    }
    if (typeof token === 'string' && token.trim().startsWith('ey') && token.trim().length > 20) {
      return token.trim();
    }
    return null;
  } catch (e) { return null; }
}
"""

_INJECT_SUCCESS_BANNER_JS = """
() => {
  try {
    if (document.getElementById('deepseek-bridge-success-banner')) return;
    const b = document.createElement('div');
    b.id = 'deepseek-bridge-success-banner';
    b.style.cssText = 'position:fixed;top:16px;left:50%;transform:translateX(-50%);z-index:9999999;' +
        'background:linear-gradient(135deg,#059669,#10b981);color:#ffffff;padding:14px 28px;' +
        'border-radius:10px;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;' +
        'font-size:15px;font-weight:600;box-shadow:0 10px 25px rgba(0,0,0,0.35);border:1px solid #34d399;' +
        'display:flex;align-items:center;gap:12px;';
    b.innerHTML = '<span style="font-size:22px;">&#10003;</span> ' +
        '<span>[DeepSeek Bridge] Sesi Berhasil Terhubung &amp; Tersimpan! Jendela ini tetap aktif untuk verifikasi Anda. ' +
        'Tutup jendela ini jika sudah selesai.</span>';
    document.body.appendChild(b);
  } catch(e) {}
}
"""


def is_deepseek_tab(page) -> bool:
    """Periksa apakah tab adalah halaman DeepSeek (bukan Proton Mail atau tab lain)."""
    try:
        url = (page.url or "").lower()
        return "deepseek.com" in url
    except Exception:
        return False


def _safe_evaluate(page, js: str):
    """Jalankan page.evaluate, toleran terhadap semua error navigasi/konteks sesaat."""
    try:
        return page.evaluate(js)
    except Exception:
        return None


def _launch(p, headless: bool, profile_dir: Optional[Path] = None):
    """Buka persistent context dengan multi-channel fallback: Chrome -> Edge -> Playwright Chromium."""
    profile_dir = profile_dir or config.PROFILE_DIR
    config.SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    _clean_stale_profile_locks(profile_dir)

    # 1. Coba Google Chrome
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

    # 2. Coba Microsoft Edge
    try:
        return p.chromium.launch_persistent_context(
            str(profile_dir),
            headless=headless,
            channel="msedge",
            args=LAUNCH_ARGS,
            ignore_default_args=IGNORE_DEFAULT_ARGS,
        )
    except Exception as e:
        msg = str(e).lower()
        if "lock" in msg or "singleton" in msg or "profile in use" in msg:
            raise

    # 3. Fallback ke bundled Chromium Playwright
    return p.chromium.launch_persistent_context(
        str(profile_dir),
        headless=headless,
        args=LAUNCH_ARGS,
        ignore_default_args=IGNORE_DEFAULT_ARGS,
    )


def _capture_from_context(context, page) -> Optional[Session]:
    """Ambil token, cookie, user agent, dan device id secara aman HANYA dari tab DeepSeek.

    Tab non-DeepSeek (seperti Proton Mail untuk melihat OTP) tidak akan pernah dievaluasi atau
    diambil cookienya. Cookie yang disimpan disaring ketat hanya untuk domain .deepseek.com.
    """
    try:
        if not is_deepseek_tab(page):
            return None

        token = _safe_evaluate(page, _READ_TOKEN_JS)
        if not token or str(token).strip().lower() in ("null", "none", "undefined", ""):
            return None

        # Filter ketat cookie HANYA untuk domain deepseek.com
        raw_cookies = context.cookies()
        deepseek_cookies = {
            c["name"]: c["value"]
            for c in raw_cookies
            if "deepseek.com" in c.get("domain", "").lower()
        }

        ua = _safe_evaluate(page, "() => navigator.userAgent") or ""
        dev_id = _safe_evaluate(
            page,
            "() => window.localStorage.getItem('deviceId') || window.localStorage.getItem('device_id') || ''"
        ) or ""

        return Session(
            token=token,
            cookies=deepseek_cookies,
            user_agent=ua,
            captured_at=time.time(),
            device_id=dev_id,
        )
    except Exception:
        return None


def _wait_for_token(page, timeout: float, context=None) -> Tuple[Optional[str], Optional[any]]:
    """Poll localStorage.userToken HANYA dari tab DeepSeek sampai muncul atau timeout.

    Tab lain (seperti tab Proton Mail untuk melihat OTP) dibiarkan aktif tanpa intervensi.
    Jeda polling diatur halus (2.0s) guna mencegah deteksi bot / burst request.
    """
    deadline = time.time() + timeout
    closed = False
    while time.time() < deadline:
        pages = context.pages if (context and context.pages) else [page]
        for p in pages:
            if not is_deepseek_tab(p):
                continue
            token = _safe_evaluate(p, _READ_TOKEN_JS)
            if token:
                return token, p
        try:
            page.wait_for_timeout(2000)
        except Exception as e:
            msg = str(e)
            if "Target page, context or browser has been closed" in msg \
                    or "TargetClosed" in type(e).__name__:
                closed = True
                break
            raise
    if closed:
        log.info("Window browser ditutup sebelum login selesai.")
    return None, None


def _safe_goto(page, url: str) -> None:
    """Navigasi tunggal, toleran terhadap net::ERR_ABORTED dari redirect SPA dan WAF."""
    try:
        page.goto(url, wait_until="commit", timeout=60000)
    except Exception as e:
        log.debug("Navigasi ke %s terinterupsi (%s); dilanjutkan...", url, type(e).__name__)
    page.wait_for_timeout(2000)


def login(headless: bool = False, assume_logged_out: bool = False,
          profile_dir: Optional[Path] = None,
          session_file: Optional[Path] = None,
          timeout: float = None,
          interactive_wait: bool = False,
          account_name: str = "default") -> Session:
    """Login interaktif satu kali hit (single-hit) tanpa burst reload.

    Tab Proton Mail untuk verifikasi OTP diperbolehkan co-exist tanpa distorsi sesi.
    """
    profile_dir = Path(profile_dir or config.PROFILE_DIR)
    session_file = Path(session_file or config.SESSION_FILE)
    if timeout is None:
        timeout = float(os.getenv("DSB_LOGIN_TIMEOUT", "600"))

    if not headless:
        _ensure_interactive_desktop()

    with sync_playwright() as p:
        context = _launch(p, headless=headless, profile_dir=profile_dir)
        page = context.pages[0] if context.pages else context.new_page()

        if not headless:
            _bring_window_to_front()

        # SINGLE HIT: Navigasi sekali saja ke CHAT_URL
        _safe_goto(page, config.CHAT_URL)

        target_page = page
        session = None

        # Cek apakah sesi sudah ada dari profil persisten
        for p_page in context.pages:
            if is_deepseek_tab(p_page):
                s = _capture_from_context(context, p_page)
                if s and s.token:
                    session = s
                    target_page = p_page
                    break

        if not session:
            print("=" * 76)
            print("                DEEPSEEK BRIDGE - PROSES LOGIN MANDIRI")
            print("=" * 76)
            print(f"[*] Akun Target : {account_name}")
            print(f"[*] Berkas Sesi : {session_file}")
            print("[*] Jendela browser terbuka di desktop interaktif.")
            print("[*] Silakan login manual ke DeepSeek Chat.")
            print("[*] CATATAN OTP / PROTON MAIL:")
            print("    Jika menggunakan Proton Mail, buka tab baru di jendela browser ini")
            print("    untuk memeriksa OTP agar tidak diblokir oleh anti-abuse Proton.")
            print("    Sistem HANYA akan menangkap sesi DeepSeek dari tab chat.deepseek.com.")
            print(f"[*] Menunggu autentikasi selesai (hingga {timeout:.0f} detik)...")
            print("=" * 76)

            token, active_page = _wait_for_token(page, timeout=timeout, context=context)
            if not token:
                try:
                    context.close()
                except Exception:
                    pass
                raise RuntimeError(
                    f"Login timeout setelah {timeout:.0f} detik atau jendela ditutup sebelum login selesai."
                )
            target_page = active_page or page

            for p_page in context.pages:
                if is_deepseek_tab(p_page):
                    s = _capture_from_context(context, p_page)
                    if s and s.token:
                        session = s
                        target_page = p_page
                        break

        if session is None or not session.token:
            try:
                context.close()
            except Exception:
                pass
            raise RuntimeError("Login selesai namun token DeepSeek tidak dapat dibaca dari localStorage.")

        # Simpan sesi SEGERA ke disk
        session.save(session_file)

        # Injeksi visual success banner ke tab DeepSeek
        _safe_evaluate(target_page, _INJECT_SUCCESS_BANNER_JS)

        print("\n" + "=" * 76)
        print(" [SUCCESS] Sesi DeepSeek Berhasil Ditangkap dan Disimpan!")
        print("=" * 76)
        print(f"[*] Akun         : {account_name}")
        print(f"[*] Berkas Sesi  : {session_file}")
        print(f"[*] Token JWT    : {session.token[:15]}... ({len(session.token)} chars)")
        print(f"[*] DeepSeek Cks : {len(session.cookies)} cookies tersimpan")
        print("=" * 76)

        if not headless and interactive_wait:
            print("\n[*] Browser dibiarkan tetap aktif untuk verifikasi akun Anda.")
            print("[*] Anda dapat memastikan chat berfungsi atau menyelesaikan tab Proton Mail.")
            print("[*] Tekan [ENTER] di terminal ini untuk menutup browser secara aman...")
            print("    (Atau langsung tutup jendela browser Anda kapan saja)")
            try:
                input()
            except Exception:
                time.sleep(3.0)

        try:
            context.close()
        except Exception:
            pass

        return session


def interactive_login(account_name: str = "default") -> int:
    """Entry point CLI untuk perintah `deepseek-bridge login [account]`.

    Menangani isolasi akun, auto-register ke AccountRegistry jika belum ada,
    dan memandu pengguna melalui login tanpa burst request.
    """
    from ..state.registry import AccountRegistry, Account
    account_name = (account_name or "default").strip().lower()

    reg = AccountRegistry.load()
    acc = reg.get(account_name)
    if not acc:
        acc = Account(name=account_name)
        reg.add(acc)
        print(f"[*] Akun '{account_name}' didaftarkan otomatis ke registry.")

    session_file = acc.session
    profile_dir = acc.profile

    other_sessions = [a.session for a in reg.list() if a.name != account_name]

    try:
        session = login(
            headless=False,
            assume_logged_out=True,
            profile_dir=profile_dir,
            session_file=session_file,
            interactive_wait=True,
            account_name=account_name,
        )
        _reject_if_shared(session, other_sessions, session_file)
        print(f"\n[OK] Autentikasi untuk akun '{account_name}' selesai dengan sempurna.")
        return 0
    except KeyboardInterrupt:
        print("\n[!] Login dibatalkan oleh pengguna.")
        return 1
    except Exception as e:
        print(f"\n[ERROR] Login gagal: {e}")
        return 1


def _headless_refresh(profile_dir: Optional[Path] = None,
                      session_file: Optional[Path] = None,
                      forbid_token_in: Optional[List[Path]] = None
                      ) -> Optional[Session]:
    """Coba tangkap token headless dari profil persisten tanpa burst hit."""
    profile_dir = Path(profile_dir or config.PROFILE_DIR)
    session_file = Path(session_file or config.SESSION_FILE)
    with sync_playwright() as p:
        try:
            context = _launch(p, headless=True, profile_dir=profile_dir)
        except Exception as e:
            log.debug("Gagal membuka headless context: %s", e)
            return None
        page = context.pages[0] if context.pages else context.new_page()
        session = None
        try:
            _safe_goto(page, config.CHAT_URL)
            for _ in range(5):
                cookies = {c["name"]: c["value"] for c in context.cookies() if "deepseek.com" in c.get("domain", "").lower()}
                if "aws-waf-token" in cookies:
                    break
                page.wait_for_timeout(1000)

            for p_curr in context.pages:
                if "/sign_in" in (p_curr.url or "").lower():
                    log.debug("Headless refresh diarahkan ke sign_in, belum terotentikasi.")
                    return None

            for p_page in context.pages:
                if is_deepseek_tab(p_page):
                    session = _capture_from_context(context, p_page)
                    if session and session.token:
                        break
        finally:
            try:
                context.close()
            except Exception:
                pass

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
    """
    session_file = Path(session_file or config.SESSION_FILE)
    profile_dir = Path(profile_dir or config.PROFILE_DIR)

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
    acc = sys.argv[1] if len(sys.argv) > 1 else "default"
    sys.exit(interactive_login(acc))
