"""Sistem Auto-Update & Changelog untuk DeepSeek Bridge Portable.

Mendukung deteksi update berbasis Git ref (clone repo) maupun GitHub REST API (fallback),
menampilkan changelog terstruktur (commit logs), dan melakukan hot-update dependensi.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..core import config

log = logging.getLogger("deepseek_bridge.updater")

GITHUB_REPO = "Muhammad-Rizqullah-Akbar/deepseek-bridge-portable"
GITHUB_COMMITS_API = f"https://api.github.com/repos/{GITHUB_REPO}/commits?per_page=15"


def _is_git_repo(root: Optional[Path] = None) -> bool:
    """Periksa apakah proyek merupakan clone git aktif."""
    r = Path(root or config.ROOT)
    return (r / ".git").exists() and shutil.which("git") is not None


def get_local_commit(root: Optional[Path] = None) -> str:
    """Ambil short hash commit lokal saat ini."""
    r = Path(root or config.ROOT)
    if _is_git_repo(r):
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(r),
                capture_output=True,
                text=True,
                timeout=3,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass
    return "unknown"


def get_current_branch(root: Optional[Path] = None) -> str:
    """Ambil nama branch git yang aktif."""
    r = Path(root or config.ROOT)
    if _is_git_repo(r):
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(r),
                capture_output=True,
                text=True,
                timeout=3,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass
    return "main"


def check_updates(root: Optional[Path] = None, timeout: float = 6.0) -> Dict[str, Any]:
    """Periksa apakah ada versi atau commit baru di hulu (upstream origin/main).

    Kembalikan ringkasan terstruktur beserta daftar changelog komit baru.
    """
    r = Path(root or config.ROOT)
    local_commit = get_local_commit(r)
    branch = get_current_branch(r)

    # 1. METODE GIT (CLONE REPO)
    if _is_git_repo(r):
        try:
            # Jalankan fetch ringan ke remote
            fetch_res = subprocess.run(
                ["git", "fetch", "origin", branch, "--quiet"],
                cwd=str(r),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if fetch_res.returncode == 0:
                local_full = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=str(r), capture_output=True, text=True, timeout=3
                ).stdout.strip()

                remote_full = subprocess.run(
                    ["git", "rev-parse", f"origin/{branch}"],
                    cwd=str(r), capture_output=True, text=True, timeout=3
                ).stdout.strip()

                if local_full and remote_full and local_full != remote_full:
                    # Ambil daftar changelog commit antara local dan remote
                    log_res = subprocess.run(
                        ["git", "log", f"HEAD..origin/{branch}", "--pretty=format:%h|%s|%cr|%an"],
                        cwd=str(r), capture_output=True, text=True, timeout=4
                    )
                    commits = []
                    if log_res.returncode == 0 and log_res.stdout.strip():
                        for line in log_res.stdout.splitlines():
                            parts = line.split("|")
                            if len(parts) >= 4:
                                commits.append({
                                    "hash": parts[0].strip(),
                                    "subject": parts[1].strip(),
                                    "date": parts[2].strip(),
                                    "author": parts[3].strip(),
                                })
                    return {
                        "is_git": True,
                        "update_available": True,
                        "local_commit": local_commit,
                        "remote_commit": remote_full[:7],
                        "branch": branch,
                        "behind": len(commits),
                        "commits": commits,
                        "source": "git",
                        "error": None,
                    }
                else:
                    return {
                        "is_git": True,
                        "update_available": False,
                        "local_commit": local_commit,
                        "remote_commit": local_commit,
                        "branch": branch,
                        "behind": 0,
                        "commits": [],
                        "source": "git",
                        "error": None,
                    }
        except subprocess.TimeoutExpired:
            log.debug("git fetch timeout setelah %.1fs", timeout)
        except Exception as e:
            log.debug("git update check gagal: %s", e)

    # 2. METODE GITHUB REST API FALLBACK (UNTUK ZIP ATAU OFFLINE GIT)
    try:
        req = urllib.request.Request(
            GITHUB_COMMITS_API,
            headers={"User-Agent": "DeepSeekBridge-Portable-Updater"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, list) and data:
                latest_remote_sha = data[0]["sha"][:7]
                remote_message = data[0]["commit"]["message"].splitlines()[0]
                is_diff = (local_commit != "unknown" and local_commit != latest_remote_sha)

                commits = []
                for item in data[:8]:
                    c_sha = item["sha"][:7]
                    if c_sha == local_commit:
                        break
                    msg = item["commit"]["message"].splitlines()[0]
                    author = item["commit"]["author"]["name"]
                    commits.append({
                        "hash": c_sha,
                        "subject": msg,
                        "date": item["commit"]["author"]["date"][:10],
                        "author": author,
                    })

                return {
                    "is_git": _is_git_repo(r),
                    "update_available": is_diff if local_commit != "unknown" else False,
                    "local_commit": local_commit,
                    "remote_commit": latest_remote_sha,
                    "branch": branch,
                    "behind": len(commits),
                    "commits": commits,
                    "source": "github_api",
                    "error": None,
                }
    except Exception as e:
        return {
            "is_git": _is_git_repo(r),
            "update_available": False,
            "local_commit": local_commit,
            "remote_commit": "unknown",
            "branch": branch,
            "behind": 0,
            "commits": [],
            "source": "none",
            "error": str(e),
        }


def apply_update(root: Optional[Path] = None) -> Tuple[bool, str, List[Dict[str, str]]]:
    """Terapkan pembaruan kode secara aman tanpa menghapus session atau database.

    Kembalikan: (berhasil: bool, pesan: str, changelog: list)
    """
    r = Path(root or config.ROOT)
    if not _is_git_repo(r):
        return (
            False,
            "Proyek ini tidak dipasang via git clone. Untuk mengaktifkan auto-update 1-click, "
            "silakan clone repositori resmi: https://github.com/Muhammad-Rizqullah-Akbar/deepseek-bridge-portable.git",
            [],
        )

    # Cek dulu changelog yang akan ditarik
    info = check_updates(r)
    if not info.get("update_available"):
        return True, "Sistem sudah berada pada versi terbaru (up to date). Tidak ada pembaruan.", []

    commits_to_apply = info.get("commits", [])

    # Jalankan git pull --rebase agar commit lokal tidak rusak
    branch = info.get("branch", "main")
    try:
        pull_res = subprocess.run(
            ["git", "pull", "--rebase", "origin", branch],
            cwd=str(r),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if pull_res.returncode != 0:
            # Fallback ke pull biasa
            pull_fallback = subprocess.run(
                ["git", "pull", "origin", branch],
                cwd=str(r),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if pull_fallback.returncode != 0:
                err_msg = pull_fallback.stderr.strip() or pull_res.stderr.strip()
                return False, f"Git pull gagal: {err_msg}", []

        # Periksa apakah dependensi berubah
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-e", "."],
                cwd=str(r),
                capture_output=True,
                text=True,
                timeout=60,
            )
        except Exception as pip_err:
            log.warning("Pembaruan dependensi pip opsional: %s", pip_err)

        new_commit = get_local_commit(r)
        return (
            True,
            f"Pembaruan berhasil diterapkan! Versi aktif saat ini: {new_commit}",
            commits_to_apply,
        )
    except Exception as e:
        return False, f"Gagal menerapkan update: {e}", []


def cmd_version(args=None) -> int:
    """Tampilkan versi, commit hash, dan status sinkronisasi ke remote."""
    r = Path(config.ROOT)
    local_sha = get_local_commit(r)
    branch = get_current_branch(r)

    print("=" * 76)
    print("              DEEPSEEK BRIDGE PORTABLE - VERSION AUDIT")
    print("=" * 76)
    print(f"[*] Versi Klien    : {getattr(config, 'CLIENT_VERSION', '2.5.0')}")
    print(f"[*] Git Commit     : {local_sha}")
    print(f"[*] Active Branch  : {branch}")
    print(f"[*] Repository     : https://github.com/{GITHUB_REPO}")
    print(f"[*] Mode Instalasi : {'Git Cloned (Auto-Update Aktif)' if _is_git_repo(r) else 'Standalone Zip'}")
    print("=" * 76)
    return 0


def cmd_update(args) -> int:
    """Perintah CLI untuk memeriksa dan menerapkan pembaruan sistem secara interaktif."""
    r = Path(config.ROOT)
    only_check = getattr(args, "check", False)
    auto_yes = getattr(args, "yes", False)

    print("=" * 76)
    print("             DEEPSEEK BRIDGE - PEMERIKSAAN PEMBARUAN")
    print("=" * 76)
    print("[*] Menghubungi repositori hulu (GitHub origin/main)...")
    
    info = check_updates(r)
    local_commit = info.get("local_commit", "unknown")
    remote_commit = info.get("remote_commit", "unknown")
    branch = info.get("branch", "main")

    if info.get("error") and not info.get("commits"):
        print(f"\n[!] Gagal memeriksa pembaruan: {info['error']}")
        print("[*] Pastikan perangkat Anda terhubung ke internet.")
        return 1

    if not info.get("update_available"):
        print("\n[OK] Sistem Anda sudah mutakhir (UP TO DATE)!")
        print(f"[*] Commit aktif saat ini : {local_commit} ({branch})")
        print("=" * 76)
        return 0

    behind = info.get("behind", 0)
    commits = info.get("commits", [])

    print(f"\n[⚡] PEMBARUAN TERSEDIA! ({behind} commit baru dari origin/{branch})")
    print(f"[*] Commit Lokal   : {local_commit}")
    print(f"[*] Commit Hulu    : {remote_commit}")
    print("-" * 76)
    print("DAFTAR PERUBAHAN (CHANGELOG):")
    for c in commits:
        print(f"  • {c['hash']} - {c['subject']} ({c['date']}) <{c['author']}>")
    print("=" * 76)

    if only_check:
        print("[*] Opsi --check aktif. Jalankan 'deepseek-bridge update' untuk menerapkan.")
        return 0

    if not auto_yes:
        try:
            confirm = input("\nTerapkan pembaruan sekarang? Data sesi & kredensial DIJAMIN aman. [Y/n]: ").strip().lower()
            if confirm and confirm not in ("y", "yes"):
                print("[*] Pembaruan dibatalkan oleh pengguna.")
                return 0
        except (KeyboardInterrupt, EOFError):
            print("\n[*] Pembaruan dibatalkan.")
            return 0

    print("\n[*] Menarik pembaruan dari GitHub...")
    ok, msg, _ = apply_update(r)
    if ok:
        print(f"\n[SUCCESS] {msg}")
        print("[*] Seluruh patch terbaru dan perbaikan sistem telah aktif.")
        print("=" * 76)
        return 0
    else:
        print(f"\n[ERROR] {msg}")
        return 1
