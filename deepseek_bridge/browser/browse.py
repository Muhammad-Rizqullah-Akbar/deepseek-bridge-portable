"""Buka DeepSeek di Chrome untuk satu akun langsung di desktop interaktif visual.

Dipanggil dari CLI atau dashboard:
    python -m deepseek_bridge.browse <nama_akun>

Arsitektur:
1. Membuka binary Chrome secara langsung (native executable) di Session desktop
   interaktif pengguna (WinSta0\\Default) tanpa pembungkus Node.js terisolasi,
   sehingga jendela visual 'DeepSeek' DIJAMIN 100% muncul di monitor fisik Maestro.
2. Mengaktifkan port remote debugging (CDP) spesifik per akun.
3. Menghubungkan Playwright ke browser via `connect_over_cdp` untuk memantau
   `localStorage.userToken` dan cookies secara real-time tanpa mengganggu pengguna.
4. Otomatis menyimpan `session.json` begitu login berhasil.
5. Menutup koneksi dan proses secara bersih saat pengguna menutup jendela Chrome.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [browse] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("browse")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from deepseek_bridge import config
from deepseek_bridge.accounts import AccountRegistry
from deepseek_bridge.auth import (
    _capture_from_context, Session, _clean_stale_profile_locks, _READ_TOKEN_JS
)
from typing import Optional, List, Union


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
    """Angkat jendela proses ke lapisan paling depan (foreground) di desktop fisik Maestro."""
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
    """Temukan executable Google Chrome di sistem Windows."""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        candidates.append(str(Path(local_app) / "Google" / "Chrome" / "Application" / "chrome.exe"))

    for c in candidates:
        if Path(c).is_file():
            return c

    which_chrome = shutil.which("chrome") or shutil.which("chrome.exe")
    if which_chrome:
        return which_chrome

    return "chrome.exe"


def run_browse(account_name: str) -> int:
    reg = AccountRegistry.load()
    acc = reg.accounts.get(account_name)
    if acc is None:
        log.error("Akun '%s' tidak ditemukan di registry (%s)", account_name, reg.path)
        return 1

    profile_dir = Path(acc.profile_dir)
    session_file = Path(acc.session_file)
    profile_dir.mkdir(parents=True, exist_ok=True)
    session_file.parent.mkdir(parents=True, exist_ok=True)

    stop_file = profile_dir / ".stop_browser"
    stop_file.unlink(missing_ok=True)

    # Bersihkan file lock usang jika proses Chrome sebelumnya mati mendadak
    _clean_stale_profile_locks(profile_dir)

    chrome_exe = _find_chrome_path()
    if not Path(chrome_exe).is_file() and chrome_exe != "chrome.exe":
        log.error("Google Chrome tidak ditemukan di path standar!")
        return 1

    # Port CDP spesifik per akun agar tidak bentrok jika multi-browser dibuka
    digits = "".join(c for c in account_name if c.isdigit())
    cdp_port = 9330 + (int(digits) if digits else 1)

    log.info("Membuka Chrome visual desktop untuk akun '%s'...", account_name)
    log.info("  Profil: %s", profile_dir)
    log.info("  Sesi  : %s", session_file)
    log.info("  Port  : %d", cdp_port)

    # Luncurkan Chrome langsung sebagai aplikasi GUI desktop interaktif
    cmd = [
        chrome_exe,
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
        log.error("Gagal meluncurkan Chrome executable: %s", e)
        return 1

    log.info("Chrome terbuka di desktop Maestro (PID %d). Menghubungkan pengawas CDP...", proc.pid)
    time.sleep(0.8)
    _bring_proc_to_foreground(proc.pid)

    from playwright.sync_api import sync_playwright

    token_captured = False

    with sync_playwright() as p:
        browser = None
        # Beri waktu Chrome startup dan mengikat port CDP
        for attempt in range(40):
            if proc.poll() is not None:
                log.info("Chrome ditutup sebelum pengawas terhubung.")
                return 0
            try:
                browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
                break
            except Exception:
                time.sleep(0.5)

        if not browser:
            log.warning("Pengawas CDP tidak dapat terhubung, Chrome tetap aktif untuk pengguna.")
            try:
                proc.wait()
            except Exception:
                pass
            return 0

        log.info("Pengawas sesi CDP terhubung. Silakan login atau selesaikan captcha di Chrome.")

        last_check = 0.0
        try:
            while proc.poll() is None:
                # Periksa apakah ada permintaan tutup dari dashboard
                if stop_file.exists():
                    log.info("Sinyal stop diterima dari dashboard. Menutup browser...")
                    stop_file.unlink(missing_ok=True)
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    break

                now = time.time()
                if now - last_check >= 1.5:
                    last_check = now
                    try:
                        contexts = browser.contexts
                        if contexts:
                            for ctx in contexts:
                                for p_curr in ctx.pages:
                                    try:
                                        p_url = p_curr.url
                                    except Exception:
                                        continue
                                    if "deepseek.com" not in p_url:
                                        continue
                                    s = _capture_from_context(ctx, p_curr)
                                    if s and s.token:
                                        s.save(session_file)
                                        if not token_captured:
                                            log.info("Token & cookies berhasil ditangkap dan disimpan ke %s!", session_file.name)
                                            token_captured = True
                                            # Notifikasi server agar langsung reload tanpa perlu restart
                                            try:
                                                import urllib.request
                                                req = urllib.request.Request("http://127.0.0.1:8787/admin/reload", data=b"")
                                                urllib.request.urlopen(req, timeout=1.0)
                                                log.info("Sinyal reload terkirim ke server bridge.")
                                            except Exception:
                                                pass
                                        break
                    except Exception as loop_err:
                        # Error sementara selama navigasi/halaman berpindah, jangan putus loop
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
