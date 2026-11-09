"""Buka DeepSeek di Chrome/Edge untuk satu akun langsung di desktop interaktif visual.

Dipanggil dari CLI atau dashboard:
    python -m deepseek_bridge.browser.browse <nama_akun>

Arsitektur:
1. Membuka binary Chrome/Edge/Playwright Chromium secara langsung (native executable) di Session desktop
   interaktif pengguna (WinSta0\\Default) tanpa pembungkus terisolasi,
   sehingga jendela visual 'DeepSeek' DIJAMIN muncul di monitor fisik.
2. Mengaktifkan port remote debugging (CDP) spesifik per akun.
3. Menghubungkan Playwright ke browser via `connect_over_cdp` untuk memantau
   `localStorage.userToken` dan cookies secara real-time tanpa mengganggu pengguna.
4. Tab non-DeepSeek (seperti Proton Mail untuk melihat kode OTP) diizinkan co-exist tanpa distorsi sesi.
5. Otomatis menyimpan `session.json` begitu login berhasil dan menyuntikkan visual banner ke tab DeepSeek.
6. Browser dibiarkan tetap aktif untuk verifikasi pengguna hingga jendela ditutup secara sukarela.
"""

from __future__ import annotations

import glob
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, List, Union

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [browse] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("browse")

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ..core import config
from ..state.registry import AccountRegistry
from .auth import (
    _capture_from_context, Session, _clean_stale_profile_locks, _READ_TOKEN_JS,
    is_deepseek_tab, _INJECT_SUCCESS_BANNER_JS, _safe_evaluate
)


class Win32Process:
    """Wrapper ringan untuk handle proses Windows yang diluncurkan via CreateProcessW."""

    def __init__(self, h_process, dw_pid: int):
        self.h_process = h_process
        self.pid = dw_pid

    def poll(self) -> Optional[int]:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        code = wintypes.DWORD()
        STILL_ACTIVE = 259
        if kernel32.GetExitCodeProcess(self.h_process, ctypes.byref(code)):
            if code.value == STILL_ACTIVE:
                return None
            return code.value
        return -1

    def wait(self, timeout: Optional[float] = None) -> int:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        ms = int(timeout * 1000) if timeout is not None else 0xFFFFFFFF
        kernel32.WaitForSingleObject(self.h_process, ms)
        return self.poll() or 0

    def terminate(self) -> None:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.TerminateProcess(self.h_process, 1)

    def __del__(self):
        try:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(self.h_process)
        except Exception:
            pass


def launch_gui_process(cmd_list_or_str: Union[str, List[str]], desktop: str = r"WinSta0\Default") -> Win32Process:
    """Luncurkan proses GUI langsung ke desktop interaktif pengguna fisik (WinSta0\\Default).

    Mengatasi batasan Python subprocess.STARTUPINFO di Windows yang tidak memiliki field
    lpDesktop sehingga proses anak selalu terisolasi di desktop sandbox runner.
    """
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32

    try:
        h_default = user32.OpenDesktopW("Default", 0, False, 0x01FF)
        if h_default:
            user32.SetThreadDesktop(h_default)
    except Exception:
        pass

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.c_char_p),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    si = STARTUPINFOW()
    si.cb = ctypes.sizeof(STARTUPINFOW)
    si.dwFlags = 0x00000001  # STARTF_USESHOWWINDOW
    si.wShowWindow = 1       # SW_SHOWNORMAL
    si.lpDesktop = desktop

    pi = PROCESS_INFORMATION()

    if isinstance(cmd_list_or_str, list):
        import subprocess
        cmd_str = subprocess.list2cmdline(cmd_list_or_str)
    else:
        cmd_str = cmd_list_or_str

    ret = kernel32.CreateProcessW(
        None,
        ctypes.create_unicode_buffer(cmd_str),
        None,
        None,
        False,
        0,
        None,
        None,
        ctypes.byref(si),
        ctypes.byref(pi),
    )
    if not ret:
        err = kernel32.GetLastError()
        raise RuntimeError(f"Gagal CreateProcessW ke '{desktop}' (Win32 error {err})")

    kernel32.CloseHandle(pi.hThread)
    return Win32Process(pi.hProcess, pi.dwProcessId)


def _bring_proc_to_foreground(pid: int) -> None:
    """Angkat jendela proses ke lapisan paling depan (foreground) di desktop fisik."""
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes
    try:
        user32 = ctypes.windll.user32
        h_default = user32.OpenDesktopW("Default", 0, False, 0x01FF)
        if h_default:
            user32.SetThreadDesktop(h_default)
        def cb(hwnd, lp):
            if user32.IsWindowVisible(hwnd):
                w_pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(w_pid))
                if w_pid.value == pid:
                    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                    user32.SetForegroundWindow(hwnd)
                    user32.BringWindowToTop(hwnd)
            return True
        user32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(cb), 0)
    except Exception:
        pass


def _find_chrome_path() -> str:
    """Temukan executable Chromium di sistem Windows (Chrome -> Edge -> Playwright)."""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        candidates.append(str(Path(local_app) / "Google" / "Chrome" / "Application" / "chrome.exe"))
        candidates.append(str(Path(local_app) / "Microsoft" / "Edge" / "Application" / "msedge.exe"))
        pw_matches = glob.glob(str(Path(local_app) / "ms-playwright" / "chromium-*" / "chrome-win" / "chrome.exe"))
        candidates.extend(pw_matches)

    for c in candidates:
        if Path(c).is_file():
            return c

    for name in ("chrome", "chrome.exe", "msedge", "msedge.exe"):
        which = shutil.which(name)
        if which:
            return which

    return "chrome.exe"


def run_browse(account_name: str) -> int:
    reg = AccountRegistry.load()
    acc = reg.get(account_name)
    if acc is None:
        log.error("Akun '%s' tidak ditemukan di registry (%s)", account_name, reg.path)
        return 1

    profile_dir = Path(acc.profile)
    session_file = Path(acc.session)
    profile_dir.mkdir(parents=True, exist_ok=True)
    session_file.parent.mkdir(parents=True, exist_ok=True)

    stop_file = profile_dir / ".stop_browser"
    stop_file.unlink(missing_ok=True)

    # Bersihkan file lock usang jika proses browser sebelumnya mati mendadak
    _clean_stale_profile_locks(profile_dir)

    browser_exe = _find_chrome_path()
    if not Path(browser_exe).is_file() and browser_exe not in ("chrome.exe", "msedge.exe"):
        log.error("Browser Chromium/Chrome/Edge tidak ditemukan!")
        return 1

    digits = "".join(c for c in account_name if c.isdigit())
    if digits:
        cdp_port = 9330 + int(digits)
    else:
        cdp_port = 9330 + (abs(hash(account_name)) % 500) + 1

    log.info("Membuka browser visual desktop untuk akun '%s'...", account_name)
    log.info("  Binary : %s", browser_exe)
    log.info("  Profil : %s", profile_dir)
    log.info("  Sesi   : %s", session_file)
    log.info("  Port   : %d", cdp_port)

    cmd = [
        browser_exe,
        f"--user-data-dir={profile_dir}",
        f"--remote-debugging-port={cdp_port}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        config.CHAT_URL,
    ]

    try:
        if sys.platform == "win32":
            proc = launch_gui_process(cmd, desktop=r"WinSta0\Default")
        else:
            proc = subprocess.Popen(cmd)
    except Exception as e:
        log.error("Gagal meluncurkan browser executable: %s", e)
        return 1

    log.info("Browser terbuka di desktop (PID %d). Menghubungkan pengawas CDP...", proc.pid)
    time.sleep(1.0)
    _bring_proc_to_foreground(proc.pid)

    from playwright.sync_api import sync_playwright

    token_captured = False

    with sync_playwright() as p:
        browser = None
        for attempt in range(40):
            try:
                browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
                break
            except Exception:
                if proc.poll() is not None and attempt > 6:
                    log.info("Browser ditutup sebelum pengawas CDP terhubung.")
                    return 0
                time.sleep(0.5)

        if not browser:
            log.warning("Pengawas CDP tidak dapat terhubung, browser tetap aktif untuk pengguna.")
            try:
                proc.wait()
            except Exception:
                pass
            return 0

        log.info("Pengawas sesi CDP terhubung. Silakan login atau selesaikan captcha di browser.")

        last_check = 0.0
        try:
            while browser.is_connected():
                if stop_file.exists():
                    log.info("Sinyal stop diterima. Menutup browser...")
                    stop_file.unlink(missing_ok=True)
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    break

                now = time.time()
                if now - last_check >= 2.0:
                    last_check = now
                    try:
                        contexts = browser.contexts
                        if contexts:
                            for ctx in contexts:
                                for p_curr in ctx.pages:
                                    if not is_deepseek_tab(p_curr):
                                        continue
                                    s = _capture_from_context(ctx, p_curr)
                                    if s and s.token:
                                        s.save(session_file)
                                        _safe_evaluate(p_curr, _INJECT_SUCCESS_BANNER_JS)
                                        if not token_captured:
                                            log.info("Token & cookies berhasil ditangkap dan disimpan ke %s!", session_file.name)
                                            token_captured = True
                                            try:
                                                import urllib.request
                                                req = urllib.request.Request("http://127.0.0.1:8787/admin/reload", data=b"")
                                                urllib.request.urlopen(req, timeout=1.0)
                                                log.info("Sinyal reload terkirim ke server bridge.")
                                            except Exception:
                                                pass
                                        break
                    except Exception as loop_err:
                        log.debug("Pengawas loop CDP toleran terhadap: %s", loop_err)

                time.sleep(0.5)
        finally:
            if stop_file.exists():
                try:
                    proc.terminate()
                except Exception:
                    pass
            stop_file.unlink(missing_ok=True)

    log.info("Selesai untuk akun '%s'.", account_name)
    return 0


if __name__ == "__main__":
    acc_name = sys.argv[1] if len(sys.argv) > 1 else "default"
    sys.exit(run_browse(acc_name))
