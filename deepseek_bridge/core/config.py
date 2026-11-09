"""Konfigurasi DeepSeek Bridge.

Semua nilai bisa di-override lewat environment variable atau file .env
di direktori project. Tidak ada secret yang di-hardcode.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT / 'data'
SESSION_BASE = DATA_DIR / 'session' if (DATA_DIR / 'session').exists() else ROOT / 'session'
STATE_BASE = DATA_DIR / 'state' if (DATA_DIR / 'state').exists() else ROOT
load_dotenv(ROOT / ".env")

# --- Server ----------------------------------------------------------------
# Default dual-stack supaya bridge bisa dijangkau dari perangkat lain lewat IP
# Tailscale, baik IPv4 (100.x.x.x) maupun IPv6 (fd7a:115c:a1e0::/48).
#
# Sebelum patch ini default-nya 127.0.0.1, sehingga dashboard hanya bisa dibuka
# di mesin lokal. Patch pertama mengubahnya ke 0.0.0.0, tapi itu hanya IPv4:
# perangkat yang memilih alamat IPv6 Tailscale tidak bisa konek karena socket
# tidak listen di IPv6. Karena itu default sekarang `::`, yang pada Windows
# menerima IPv4 dan IPv6 sekaligus lewat satu socket dual-stack.
#
# Akses tetap aman karena Tailscale adalah jaringan privat: hanya perangkat
# yang login ke tailnet yang sama bisa menjangkau port ini. Kalau nanti
# bridge perlu diekspos ke internet publik, pakai `tailscale serve` atau
# `tailscale funnel`, jangan buka port langsung tanpa autentikasi.
HOST = os.getenv("DSB_HOST", "127.0.0.1")
PORT = int(os.getenv("DSB_PORT", "8787"))
# Batas request per menit per IP. Batas ini murni pengaman di sisi bridge.
# Batas sesungguhnya datang dari DeepSeek, dan hasil pengukuran menunjukkan
# akun hanya mengizinkan sekitar 2 completion bersamaan; melebihi itu
# dibalas "Messages too frequent" atau "Server busy". Pendinginan setelah
# kena throttle hanya butuh sekitar 5 detik.
RATE_LIMIT_PER_MINUTE = int(os.getenv("DSB_RATE_LIMIT", "600"))
# Jumlah completion DeepSeek yang boleh berjalan bersamaan PER AKUN. Pengukuran:
# 2 paralel stabil sukses, 3 ke atas mulai muncul penolakan.
MAX_CONCURRENT_COMPLETIONS = int(os.getenv("DSB_MAX_CONCURRENT", "2"))
# Tunggu maksimal untuk mendapatkan slot completion sebelum menolak request.
COMPLETION_QUEUE_TIMEOUT = float(os.getenv("DSB_QUEUE_TIMEOUT", "120"))

# --- Pool Akun -------------------------------------------------------------
# Daftar akun dipisah titik koma, misalnya:
#   DSB_ACCOUNTS=utama,tambahan1,tambahan2
# Setiap akun memakai direktori sesi sendiri:
#   session/<nama>/session.json dan session/<nama>/profile
# Nama "default" berarti memakai session/session.json seperti sebelumnya,
# sehingga konfigurasi satu akun lama tetap jalan tanpa perubahan.
ACCOUNTS_RAW = os.getenv("DSB_ACCOUNTS", "default")
ACCOUNT_NAMES = [a.strip() for a in ACCOUNTS_RAW.split(";") if a.strip()]

# Jeda minimum antar request pada satu akun, detik.
MIN_GAP_SECONDS = float(os.getenv("DSB_MIN_GAP", "0.2"))
# Lama pendinginan setelah satu akun kena throttle, detik.
COOLDOWN_SECONDS = float(os.getenv("DSB_COOLDOWN", "20"))
# Lama pendinginan setelah akun kena tantangan keamanan/challenge, detik (default 60s, bukan 1800s).
CHALLENGE_COOLDOWN_SECONDS = float(os.getenv("DSB_CHALLENGE_COOLDOWN", "60"))
# Aktifkan mode pool. Kalau False, perilaku sama seperti satu akun biasa.
POOL_ENABLED = os.getenv("DSB_POOL_ENABLED", "1") == "1"


def account_paths(name: str) -> tuple[Path, Path]:
    """Kembalikan (session_file, profile_dir) untuk sebuah nama akun.

    Nama "default" dan "acc1" memetakan ke lokasi sesi pertama supaya
    setup satu akun yang sudah berjalan tidak perlu dipindah. Nama lain
    mendapat direktori sendiri di session/<nama>/.
    """
    if name in ("default", "acc1"):
        return SESSION_FILE, PROFILE_DIR
    base = SESSION_BASE / name
    return base / "session.json", base / "profile"


def account_dir(name: str) -> Path:
    """Direktori kerja satu akun, tempat session.json dan threads.json."""
    session_file, _profile = account_paths(name)
    return session_file.parent

# --- Sesi ------------------------------------------------------------------
# Profil browser persisten. Sekali login manual, profil ini dipakai ulang
# untuk refresh token headless tanpa membuka window lagi.
PROFILE_DIR = Path(os.getenv("DSB_PROFILE_DIR", SESSION_BASE / "profile"))
SESSION_FILE = Path(os.getenv("DSB_SESSION_FILE", SESSION_BASE / "session.json"))
# Status mute per akun disimpan di sini supaya restart bridge tidak
# menguji ulang akun yang sudah diketahui di-mute DeepSeek.
POOL_STATE_FILE = Path(os.getenv(
    "DSB_POOL_STATE_FILE", SESSION_BASE / "pool_state.json"))
MODELS_FILE = Path(os.getenv(
    "DSB_MODELS_FILE", SESSION_BASE / "models.json"))
# --- Thread persisten ------------------------------------------------------
# Satu percakapan dipakai ulang lintas request. Tanpa ini setiap request
# membuka chat baru, pola yang mencolok dan memaksa seluruh riwayat
# dikirim ulang.
THREADS_ENABLED = os.getenv("DSB_THREADS", "1") == "1"
# Umur percakapan sebelum dianggap selesai dan chat baru dibuat. Terukur
# langsung dari server: `chat_session/create` mengembalikan `ttl_seconds`
# bernilai 259200, yaitu 3 hari. Nilai lama 7 hari membuat bridge percaya
# percakapan masih hidup padahal server sudah membuangnya, dan kegagalan
# baru muncul saat request berikutnya. Thread juga diputus kalau hulu
# menolak chat session lama atau klien meminta new_thread.
THREAD_TTL_SECONDS = int(os.getenv("DSB_THREAD_TTL", str(3 * 24 * 60 * 60)))
# Perubahan system prompt TIDAK memutus percakapan. System prompt Hermes
# memuat tanggal, memori, dan daftar perkakas yang bisa berubah tiap
# request; kalau itu dijadikan pemicu reset, setiap request membuka chat
# baru dan justru memicu deteksi otomasi.
THREAD_RESET_ON_SYSTEM = os.getenv("DSB_THREAD_RESET_ON_SYSTEM", "0") == "1"
# Jumlah maksimum percakapan hidup per owner. Pagar keras terakhir terhadap
# fragmentasi: sekali owner punya sesi aktif, request lanjutan tidak boleh
# membuka chat baru lagi. Angka satu berarti satu owner satu percakapan,
# persis seperti satu orang memakai satu tab di web DeepSeek. Dinaikkan ke
# dua atau lebih hanya kalau Anda memang ingin beberapa topik berjalan
# bersamaan dan siap menanggung risikonya.
THREAD_MAX_LIVE = int(os.getenv("DSB_THREAD_MAX_LIVE", "1"))
# Berapa detik request boleh menunggu slot konkurensi milik key sebelum
# ditolak. Menolak instan membuat router hulu menandai koneksi tidak sehat
# dan menaikkan level backoff, jadi menunggu sebentar jauh lebih murah.
KEY_WAIT_SECONDS = float(os.getenv("DSB_KEY_WAIT", "60"))
# Percobaan ulang untuk kegagalan sementara, yaitu gangguan jaringan atau
# stream yang terpotong sebelum penanda selesai. Balasan setengah jadi
# tidak pernah diteruskan ke klien, jadi setiap percobaan mulai bersih.
# Untuk coding ini penting karena tool call yang terkirim setengah jadi
# membuat Hermes gagal total, sedangkan error jaringan biasa sepele.
TRANSIENT_RETRIES = int(os.getenv("DSB_TRANSIENT_RETRIES", "2"))
# Jeda dasar antar percobaan ulang, detik. Dikalikan nomor percobaan,
# jadi percobaan kedua menunggu dua kali ini.
TRANSIENT_BACKOFF = float(os.getenv("DSB_TRANSIENT_BACKOFF", "2.0"))
# Batas panjang blok identitas yang dikirim pada turn pertama (utuh tanpa pemotongan).
# Dinaikkan ke 1.500.000 karakter agar seluruh system prompt, memori agen, dan deskripsi tool tidak pernah terpotong.
BOOTSTRAP_MAX_CHARS = int(os.getenv("DSB_BOOTSTRAP_MAX_CHARS", "1500000"))
# Pagar TOTAL untuk payload turn pertama, identitas plus instruksi perkakas plus transkrip.
# Dinaikkan ke 2.000.000 karakter mendekati batas native DeepSeek web (2.621.440 karakter).
BOOTSTRAP_TOTAL_CHARS = int(os.getenv("DSB_BOOTSTRAP_TOTAL_CHARS", "2000000"))
# Pagar keras panjang payload. Satu request tidak boleh melebihi ini.
# Dinaikkan ke 2.200.000 karakter (~730.000 token), memberi ruang lega bagi sesi panjang tanpa pemotongan senyap.
MAX_PROMPT_CHARS = int(os.getenv("DSB_MAX_PROMPT_CHARS", "2200000"))
# --- Isolasi profil -------------------------------------------------------
# Thread key hanya memakai nama API key, jadi dua profil yang berbagi satu
# key jatuh ke chat_session_id DeepSeek yang sama. Akibatnya percakapan
# Benjamin dan Elliot bercampur dalam satu chat, dan karena turn lanjutan
# tidak mengirim ulang system prompt, identitas profil hilang total setelah
# turn pertama.
#
# Nama profil disuntikkan ke thread key lewat header X-DSB-Profile atau
# field `profile` di body. Kalau tidak ada, perilaku lama dipertahankan
# supaya klien yang sudah jalan tidak rusak.
PROFILE_HEADER = os.getenv("DSB_PROFILE_HEADER", "X-DSB-Profile")
# Penanda identitas singkat yang dikirim tiap turn supaya model selalu tahu
# dia profil siapa, bahkan saat DeepSeek sudah memegang konteks percakapan.
# Panjangnya sengaja kecil: hanya nama profil dan satu baris peran.
PROFILE_STAMP_ENABLED = os.getenv("DSB_PROFILE_STAMP", "1") == "1"
PROFILE_STAMP_MAX_CHARS = int(os.getenv("DSB_PROFILE_STAMP_MAX_CHARS", "600"))
# Mode streaming thinking: kalau True, token thinking dialirkan juga ke dalam
# `content` yang dibungkus tag <think>...</think>, selain ke `reasoning_content`.
# Ini memungkinkan klien / UI universal (Hermes, Chatbox, Open WebUI, Cursor, dll.)
# menampilkan alur berpikir secara live, dan memungkinkan pengguna menginterupsi /
# membatalkan eksekusi di tengah-tengah proses berpikir sebelum tool dijalankan.
THINKING_INLINE = os.getenv("DSB_THINKING_INLINE", "1") == "1"
# Pagar payload turn lanjutan, sebagai pecahan dari CONTEXT_LENGTH.
# Ditingkatkan ke 75% pemakaian normal dengan hard cap 85% untuk mendukung transkrip masif.
# Dasar perhitungan CONTEXT_LENGTH = 890880 token, hasil recon settings scope=main.
PROFILE_PAYLOAD_FRACTION = float(os.getenv("DSB_PROFILE_PAYLOAD_FRACTION", "0.75"))
PROFILE_PAYLOAD_MAX_FRACTION = float(os.getenv("DSB_PROFILE_PAYLOAD_MAX_FRACTION", "0.85"))
# Rasio karakter per token untuk mengubah pagar token jadi pagar karakter.
# DeepSeek tidak mengekspos tokenizer, jadi dipakai rasio konservatif 3
# karakter per token. Teks campuran Indonesia dan Inggris dengan istilah
# teknis berada di antara 3 dan 4; memakai 3 membuat pagar lebih ketat,
# bukan lebih longgar, jadi aman terhadap batas sungguhan.
CHARS_PER_TOKEN = float(os.getenv("DSB_CHARS_PER_TOKEN", "3.0"))


def profile_payload_max_chars(fraction: float = None) -> int:
    """Pagar karakter untuk payload satu turn, dari pecahan CONTEXT_LENGTH.

    Hasilnya tidak pernah melewati PROFILE_PAYLOAD_MAX_FRACTION, berapa pun
    pecahan yang diminta. Dasar perhitungan CONTEXT_LENGTH, bukan
    INPUT_CHARACTER_LIMIT, karena yang relevan untuk satu turn adalah
    jendela konteks, bukan batas karakter input keseluruhan.
    """
    frac = PROFILE_PAYLOAD_FRACTION if fraction is None else fraction
    frac = max(0.0, min(frac, PROFILE_PAYLOAD_MAX_FRACTION))
    token_budget = CONTEXT_LENGTH * frac
    return int(token_budget * CHARS_PER_TOKEN)
# Token dianggap kedaluwarsa setelah sekian detik, lalu di-refresh dari profil.
SESSION_MAX_AGE = int(os.getenv("DSB_SESSION_MAX_AGE", str(30 * 24 * 60 * 60)))
# Kalau False, server tidak akan membuka window browser saat request masuk;
# malah mengembalikan 503 berisi instruksi login. Default True supaya
# pengalaman pertama kali mulus.
SERVER_INTERACTIVE_LOGIN = os.getenv("DSB_SERVER_INTERACTIVE_LOGIN", "1") == "1"

# --- DeepSeek --------------------------------------------------------------
BASE = "https://chat.deepseek.com"
CHAT_URL = f"{BASE}/"
SIGNIN_URL = f"{BASE}/sign_in"
COMPLETION_PATH = "/api/v0/chat/completion"
POW_CHALLENGE_PATH = "/api/v0/chat/create_pow_challenge"
SESSION_CREATE_PATH = "/api/v0/chat_session/create"
# Endpoint pemulihan. Ketiganya ada di bundle produksi dan belum dipakai
# bridge sama sekali sebelum patch ini.
#   continue      melanjutkan pesan berstatus INCOMPLETE, membalas JSON
#                 dengan biz_code 22 kalau pesan sudah FINISHED
#   resume_stream menyambung koneksi SSE yang putus
#   stop_stream   menghentikan generasi berjalan
CONTINUE_PATH = "/api/v0/chat/continue"
RESUME_STREAM_PATH = "/api/v0/chat/resume_stream"
STOP_STREAM_PATH = "/api/v0/chat/stop_stream"
# Riwayat terstruktur. Sumber tunggal untuk status pesan, beban token, dan
# alasan truncation. Jauh lebih akurat daripada menebak dari teks FINISHED.
HISTORY_PATH = "/api/v0/chat/history_messages"
# Status mute sebenarnya. Bundle membuktikan `chat.is_muted` dan
# `chat.mute_until` dibaca dari sini, bukan dari pesan error completion.
USERS_CURRENT_PATH = "/api/v0/users/current"
# Settings runtime. Membutuhkan parameter `did`, tanpa itu server membalas
# biz_code 2 INVALID_PARAM.
CLIENT_SETTINGS_PATH = "/api/v0/client/settings"
# Pencarian percakapan. Kursor `before_seq_id` bersifat longgar, dan
# `seq_id` harus diperlakukan sebagai string karena 18 digit melewati batas
# aman integer.
INDEX_QUERY_PATH = "/api/v0/index/query"
INDEX_PREPARE_PATH = "/api/v0/index/prepare"

# --- Klien -----------------------------------------------------------------
# Versi klien yang dikirim website, diambil dari tangkapan Playwright nyata.
# Sebelum patch ini bridge mengirim 2.0.0 padahal website mengirim 2.7.0.
CLIENT_VERSION = "2.7.0"

# --- Model -----------------------------------------------------------------
# DeepSeek web mengenal dua mode bernama, dipetakan ke model V4:
#   Instant Mode -> DeepSeek-V4-Flash  (model_type wire "default")
#   Expert Mode  -> DeepSeek-V4-Pro    (model_type wire "expert")
# Keduanya mendukung 1M konteks dan mode Thinking maupun Non-Thinking.
# Nama lama deepseek-chat dan deepseek-reasoner sudah pensiun per 24 Juli
# 2026, tapi tetap diterima sebagai alias supaya klien lama tidak rusak.
MODEL_MAP = {
    "dsk-auto": "default",
    "deepseek-auto": "default",
    "dsk": "default",
}

PUBLISHED_MODELS = ("dsk-auto", "deepseek-auto", "dsk")


def resolve_model_type(model: str) -> str:
    """Petakan nama model OpenAI-style ke model_type DeepSeek.

    Nama yang tidak dikenal di-fallback ke "default" (Instant) supaya klien
    yang mengirim model id sembarang tetap jalan.
    """
    return MODEL_MAP.get(model, "default")


def is_known_model(model: str) -> bool:
    return model in MODEL_MAP


# --- Metadata kapabilitas -------------------------------------------------
# Angka di bawah ini diambil langsung dari server, bukan asumsi:
#   GET /api/v0/client/settings?did=<uuid>&scope=main
#     normal_history_and_file_token_limit = 890880
#     r1_history_and_file_token_limit     = 890880
#   GET /api/v0/client/settings?did=<uuid>&scope=model
#     model_configs[].input_character_limit = 2621440 (karakter, bukan token)
# Nilai lama 1000000 dan 384000 adalah iklan, bukan pengukuran. Selisih
# 109120 token pada konteks berarti router hulu mengizinkan percakapan
# tumbuh melewati batas server, lalu request ditolak.
CONTEXT_LENGTH = 890880
# Batas output TIDAK diekspos server sebagai konstanta. Terverifikasi lewat
# recon ultra max pada 25 Oktober 2026:
#
#   settings-main.json    -> normal_history_and_file_token_limit = 890880
#                            r1_history_and_file_token_limit     = 890880
#   settings-model.json   -> file_feature.token_limit              = 890880
#                            file_feature.token_limit_with_thinking = 890880
#                            input_character_limit                 = 2621440
#   bundle 1.58 MB        -> max_output_tokens, max_tokens,
#                            maxTokens, outputLimit, responseLimit,
#                            MAX_LENGTH, too_long: NOL kemunculan
#
# Server tidak mendeklarasikan batas output dan klien tidak pernah mengirimnya.
# Balasan panjang tidak dipotong: server menandainya INCOMPLETE lalu klien
# memanggil endpoint continue. Itu arsitekturnya, dan bridge mengikutinya.
#
# Karena itu angka ini disamakan dengan deklarasi token server, bukan 8192.
# Nilai 8192 sebelumnya adalah estimasi tanpa dasar yang membuat 9router
# mengira DeepSeek hanya bisa menghasilkan segitu, sehingga kreativitas dan
# penalaran kritis terpotong sebelum sempat keluar.
#
# CATATAN KEJUJURAN: 890880 adalah inferensi dari tiga deklarasi konsisten,
# bukan fakta langsung dari server. Yang pasti: tidak ada batas output yang
# terlihat, dan 8192 terlalu kecil.
MAX_OUTPUT_TOKENS = int(os.getenv(
    "DSB_MAX_OUTPUT_TOKENS", str(CONTEXT_LENGTH)))
# Batas karakter input per request, dari settings scope=model.
INPUT_CHARACTER_LIMIT = 2621440
# Batas berkas, dari settings scope=model file_feature.
FILE_TOKEN_LIMIT = 890880
FILE_MAX_COUNT = 50
FILE_MAX_SIZE = 104857600
# Kuota suntingan dan regenerasi per pesan, dari settings scope=model.
EDIT_QUOTA = 5
REGENERATE_QUOTA = 5

MODEL_CAPABILITIES: dict = {
    # Satu model, satu entri. Instant Mode adalah satu-satunya yang
    # `enabled: true` dan `switchable: true` menurut settings scope=model.
    #
    # `thinking: True` bukan hiasan. Teruji langsung dengan prompt identik,
    # hanya thinking yang dibedakan:
    #   thinking=False search=True  -> mode SEARCH,      0 tool_search, 0 tool_open
    #   thinking=True  search=True  -> mode DEEP_SEARCH, 2 tool_search, 6 tool_open
    # Jadi thinking adalah syarat agar server naik ke mode agentic.
    "dsk-auto": {
        "vision": True,
        "reasoning": True,
        "thinking": True,
        "tools": True,
        "search": True,
        "streaming": True,
    },
    "deepseek-auto": {
        "vision": True,
        "reasoning": True,
        "thinking": True,
        "tools": True,
        "search": True,
        "streaming": True,
    },
    "dsk": {
        "vision": True,
        "reasoning": True,
        "thinking": True,
        "tools": True,
        "search": True,
        "streaming": True,
    },
}


def _load_dynamic_models() -> None:
    target_file = MODELS_FILE
    if not target_file.exists():
        alt = Path(__file__).resolve().parent / "session" / "models.json"
        if alt.exists():
            target_file = alt
    if target_file.exists():
        try:
            data = json.loads(target_file.read_text(encoding="utf-8"))
            if isinstance(data.get("models"), dict):
                MODEL_MAP.clear()
                MODEL_MAP.update(data["models"])
            if isinstance(data.get("published"), (list, tuple)):
                global PUBLISHED_MODELS
                PUBLISHED_MODELS = tuple(data["published"])
                modul = sys.modules.get(__name__)
                if modul:
                    setattr(modul, "PUBLISHED_MODELS", PUBLISHED_MODELS)
            if isinstance(data.get("capabilities"), dict):
                MODEL_CAPABILITIES.clear()
                MODEL_CAPABILITIES.update(data["capabilities"])
        except Exception:
            pass


_load_dynamic_models()


# Nilai yang boleh dimuat ulang saat runtime. Daftar ini sengaja eksplisit,
# bukan "semua atribut modul", supaya menambah setelan baru tidak otomatis
# ikut dimuat ulang tanpa dipikirkan dulu.
_RELOADABLE = (
    "HOST", "PORT", "RATE_LIMIT_PER_MINUTE", "MAX_CONCURRENT_COMPLETIONS",
    "COMPLETION_QUEUE_TIMEOUT", "ACCOUNTS_RAW", "ACCOUNT_NAMES",
    "MIN_GAP_SECONDS", "COOLDOWN_SECONDS", "CHALLENGE_COOLDOWN_SECONDS", "POOL_ENABLED",
    "THREADS_ENABLED", "THREAD_TTL_SECONDS", "THREAD_RESET_ON_SYSTEM",
    "THREAD_MAX_LIVE", "KEY_WAIT_SECONDS", "TRANSIENT_RETRIES",
    "TRANSIENT_BACKOFF", "BOOTSTRAP_MAX_CHARS", "BOOTSTRAP_TOTAL_CHARS",
    "MAX_PROMPT_CHARS", "SESSION_MAX_AGE", "SERVER_INTERACTIVE_LOGIN",
    "PROFILE_DIR", "SESSION_FILE", "POOL_STATE_FILE", "THINKING_INLINE",
)


def _read_env() -> dict:
    """Baca ulang .env lalu kembalikan setelan yang bisa dimuat ulang.

    `load_dotenv` dengan override=True mengganti nilai yang sudah ada di
    os.environ, jadi perubahan di berkas .env terbaca. Tanpa override,
    nilai lama di lingkungan menang dan hot reload terasa tidak bekerja.
    """
    load_dotenv(ROOT / ".env", override=True)
    return {
        "HOST": os.getenv("DSB_HOST", "127.0.0.1"),
        "PORT": int(os.getenv("DSB_PORT", "8787")),
        "RATE_LIMIT_PER_MINUTE": int(os.getenv("DSB_RATE_LIMIT", "600")),
        "MAX_CONCURRENT_COMPLETIONS": int(os.getenv("DSB_MAX_CONCURRENT", "2")),
        "COMPLETION_QUEUE_TIMEOUT": float(os.getenv("DSB_QUEUE_TIMEOUT", "120")),
        "ACCOUNTS_RAW": os.getenv("DSB_ACCOUNTS", "default"),
        "MIN_GAP_SECONDS": float(os.getenv("DSB_MIN_GAP", "1.0")),
        "COOLDOWN_SECONDS": float(os.getenv("DSB_COOLDOWN", "20")),
        "CHALLENGE_COOLDOWN_SECONDS": float(os.getenv("DSB_CHALLENGE_COOLDOWN", "60")),
        "POOL_ENABLED": os.getenv("DSB_POOL_ENABLED", "1") == "1",
        "THREADS_ENABLED": os.getenv("DSB_THREADS", "1") == "1",
        "THREAD_TTL_SECONDS": int(os.getenv("DSB_THREAD_TTL", str(7 * 24 * 60 * 60))),
        "THREAD_RESET_ON_SYSTEM": os.getenv("DSB_THREAD_RESET_ON_SYSTEM", "0") == "1",
        "THREAD_MAX_LIVE": int(os.getenv("DSB_THREAD_MAX_LIVE", "1")),
        "KEY_WAIT_SECONDS": float(os.getenv("DSB_KEY_WAIT", "60")),
        "TRANSIENT_RETRIES": int(os.getenv("DSB_TRANSIENT_RETRIES", "2")),
        "TRANSIENT_BACKOFF": float(os.getenv("DSB_TRANSIENT_BACKOFF", "2.0")),
        "BOOTSTRAP_MAX_CHARS": int(os.getenv("DSB_BOOTSTRAP_MAX_CHARS", "1500000")),
        "BOOTSTRAP_TOTAL_CHARS": int(os.getenv("DSB_BOOTSTRAP_TOTAL_CHARS", "2000000")),
        "MAX_PROMPT_CHARS": int(os.getenv("DSB_MAX_PROMPT_CHARS", "2200000")),
        "SESSION_MAX_AGE": int(os.getenv("DSB_SESSION_MAX_AGE", str(30 * 24 * 60 * 60))),
        "SERVER_INTERACTIVE_LOGIN": os.getenv("DSB_SERVER_INTERACTIVE_LOGIN", "1") == "1",
        "PROFILE_DIR": Path(os.getenv("DSB_PROFILE_DIR", SESSION_BASE / "profile")),
        "SESSION_FILE": Path(os.getenv("DSB_SESSION_FILE", SESSION_BASE / "session.json")),
        "POOL_STATE_FILE": Path(os.getenv(
            "DSB_POOL_STATE_FILE", SESSION_BASE / "pool_state.json")),
        "THINKING_INLINE": os.getenv("DSB_THINKING_INLINE", "1") == "1",
    }


def reload_config() -> dict:
    """Muat ulang setelan dari .env ke atribut modul ini.

    Dipakai endpoint /admin/reload supaya mengubah .env tidak lagi wajib
    restart proses. Karena seluruh modul membaca `config.NAMA` pada saat
    dipakai, memperbarui atribut modul ini sudah cukup untuk mengubah
    perilaku mereka.

    Mengembalikan dict berisi nama setelan yang berubah, beserta nilai
    lama dan baru, supaya pemanggil bisa melaporkan perubahan dengan jujur
    alih-alih hanya bilang berhasil.
    """
    baru = _read_env()
    # ACCOUNT_NAMES adalah turunan ACCOUNTS_RAW, bukan setelan terpisah.
    baru["ACCOUNT_NAMES"] = [
        a.strip() for a in baru["ACCOUNTS_RAW"].split(";") if a.strip()
    ]

    berubah = {}
    # `sys.modules[__name__]` dipakai, bukan `globals()`. Di dalam fungsi,
    # globals() mengembalikan dict biasa yang tidak punya __dict__, jadi
    # setattr(globals(), ...) gagal dengan AttributeError. Modul punya
    # __dict__, dan itu yang dilihat modul lain saat membaca config.NAMA.
    modul = sys.modules[__name__]
    for nama in _RELOADABLE:
        if nama not in baru:
            continue
        lama = getattr(modul, nama, None)
        nilai_baru = baru[nama]
        if lama != nilai_baru:
            setattr(modul, nama, nilai_baru)
            berubah[nama] = {"lama": str(lama), "baru": str(nilai_baru)}
    _load_dynamic_models()
    return berubah


def config_snapshot() -> dict:
    """Nilai setelan yang bisa dimuat ulang, untuk ditampilkan di dashboard."""
    return {nama: str(getattr(globals(), nama, None)) for nama in _RELOADABLE}
