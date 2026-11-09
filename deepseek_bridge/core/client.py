"""Klien HTTP murni untuk API chat DeepSeek.

Berbicara langsung ke API internal chat.deepseek.com memakai sesi yang sudah
tertangkap (lihat `auth.py`). Untuk setiap pesan:

    1. buat chat session    (POST /api/v0/chat_session/create)
    2. ambil PoW challenge  (POST /api/v0/chat/create_pow_challenge)
    3. selesaikan via WASM  (pow.DeepSeekPow)
    4. POST completion      dengan header x-ds-pow-response
    5. parse SSE stream     menjadi teks

TLS fingerprint sangat menentukan. Request lewat curl_cffi dengan impersonate
chrome supaya tidak ditolak di lapisan edge.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Iterator, List, Optional

from curl_cffi import requests as cffi_requests

from . import config
from ..browser.auth import Session, get_session
from ..browser.pow import DeepSeekPow

log = logging.getLogger("deepseek_bridge.client")

# curl_cffi.requests.Session tidak mendukung base_url seperti httpx, jadi semua
# path harus absolut. URL lengkap dibangun sekali di sini.
URL_SESSION_CREATE = config.BASE + config.SESSION_CREATE_PATH
URL_POW_CHALLENGE = config.BASE + config.POW_CHALLENGE_PATH
URL_COMPLETION = config.BASE + config.COMPLETION_PATH

_CID_SEP = ":"


def _encode_cid(session_id: str, message_id: Optional[int]) -> str:
    if message_id is None:
        return session_id
    return f"{session_id}{_CID_SEP}{message_id}"


def _decode_cid(conversation_id: Optional[str]) -> tuple[Optional[str], Optional[int]]:
    """Pecah conversation_id kembali menjadi (chat_session_id, parent_message_id)."""
    if not conversation_id:
        return None, None
    session_id, _, msg = conversation_id.partition(_CID_SEP)
    parent = int(msg) if msg.isdigit() else None
    return (session_id or None), parent


@dataclass
class Reply:
    """Balasan lengkap beserta id untuk melanjutkan percakapan."""

    text: str
    conversation_id: str
    thinking: str = ""
    # Analitik agentic yang dikumpulkan selama stream: conversation_mode,
    # tool_searches, tool_opens, message_status, dan lain-lain. Dipisahkan
    # dari `thinking` karena isinya bukan teks penalaran, melainkan fakta
    # tentang apa yang dilakukan model.
    meta: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return self.text


# Kode error completion, diekstrak dari bundle produksi. Sebelum patch ini
# bridge hanya mengenali biz_code 40003, sehingga 29 kode lain jatuh ke pesan
# generik "Request DeepSeek gagal" dan diagnosis mustahil.
COMPLETION_ERROR_CODES = {
    1: "INVALID_SESSION_ID",
    2: "PARENT_ROLE_NOT_ASSISTANT",
    3: "MAX_MESSAGE_COUNT_REACHED",
    4: "REGENERATE_TEMPLATE_RESPONSE",
    5: "MUTED_OR_EMPTY_CHAT_SESSION",
    6: "EMPTY_PROMPT",
    7: "RESUME_MESSAGE_NOT_FOUND",
    8: "RESUME_TIME_TOO_LONG",
    9: "INVALID_REF_FILE_ID",
    10: "SESSION_FILE_LIMIT_EXCEEDED",
    13: "THINKING_CANNOT_BE_WITH_FILE",
    15: "THINKING_QUOTA_EXCEEDED",
    17: "SEARCH_CANNOT_WITH_FILE",
    20: "CHARACTER_NOT_AVAILABLE",
    21: "SEARCH_NOT_ALLOWED_WITH_CHARACTER",
    22: "RESUME_MESSAGE_GOT_FULL_MESSAGE",
    23: "CURRENT_MESSAGE_IS_NOT_A_LEAF_MESSAGE",
    24: "CURRENT_MESSAGE_IS_LOCKED",
    25: "PARENT_MESSAGE_IS_LOCKED",
    26: "INVALID_PARENT_MESSAGE_ID_OR_MESSAGE_NOT_EXIST",
    28: "REF_FILE_AUDIT_UNSATISFIED",
}

# Kode error global, juga dari bundle.
GLOBAL_ERROR_CODES = {
    40029: "IP_ACCESS_RESTRICTED",
    40300: "POW_HEADER_ERROR",
    40301: "INVALID_POW_RESPONSE",
    40303: "CHALLENGE_REQUIRED",
    40304: "CHALLENGE_FAILED",
}

# Status pesan, dari enum MessageStatus di bundle. INCOMPLETE dan
# CONTEXT_LENGTH_EXCEEDED adalah status pertama kelas, bukan error. Bridge
# lama memperlakukan stream mati tanpa FINISHED sebagai truncation, padahal
# server punya cara memberitahu bahwa balasan sengaja tidak lengkap.
MESSAGE_STATUSES = (
    "FINISHED", "WIP", "INCOMPLETE", "CONTENT_FILTER",
    "CONTEXT_LENGTH_EXCEEDED", "TIMEOUT",
)

# Status yang berarti pesan bisa dilanjutkan lewat endpoint continue.
CONTINUABLE_STATUSES = ("INCOMPLETE", "CONTEXT_LENGTH_EXCEEDED", "TIMEOUT")

# Batas ronde auto-continue. Server tidak mendeklarasikan batas ini, jadi
# angkanya pilihan bridge sebagai penjaga, bukan kebenaran server.
#
# Alasannya: kalau server salah kirim status INCOMPLETE terus menerus, bridge
# tanpa batas akan memanggil continue selamanya dan turn tidak pernah berakhir.
# Dengan batas, kegagalan terlihat sebagai data di meta, bukan sebagai hang.
#
# 8 ronde dipilih karena satu balasan panjang biasanya selesai dalam 2 sampai
# 3 ronde. 8 memberi margin lebar tanpa membuka pintu loop tak berujung.
MAX_CONTINUE_ROUNDS = 8


def _has_repetition_loop(text: str) -> bool:
    """Deteksi loop pengulangan token degenerate (misal <||DSML|| invoke> berulang-ulang)."""
    if not text or len(text) < 30:
        return False
    norm = text.replace("\uff5c", "|")
    # Pengulangan tag DSML atau invoke tanpa parameter nyata
    if norm.count("DSML") >= 3 or norm.count("invoke>") >= 3:
        return True
    # Pengulangan baris yang identik berturut-turut lebih dari 3 kali
    lines = [l.strip() for l in norm.strip().splitlines() if l.strip()]
    if len(lines) >= 4:
        for i in range(len(lines) - 3):
            if lines[i] and lines[i] == lines[i+1] == lines[i+2] == lines[i+3]:
                return True
    # Pengulangan pola substring 15-40 karakter berulang di ekor
    for size in (15, 20, 25, 30, 40):
        if len(norm) >= size * 4:
            tail = norm[-size*4:]
            chunk = tail[-size:]
            if tail.count(chunk) >= 4:
                return True
    return False


def _clean_repetition_loop(text: str) -> str:
    """Bersihkan ekor teks yang terperangkap dalam loop pengulangan degenerate."""
    if not text:
        return text
    norm = text.replace("\uff5c", "|")
    pattern = r"((?:<[|]{1,2}DSML[|]{1,2}\s*invoke>\s*){2,})"
    m = re.search(pattern, norm, re.DOTALL)
    if m:
        clean_idx = m.start()
        return text[:clean_idx].rstrip()
    return text


def describe_biz_code(code: Optional[int]) -> str:
    """Nama resmi sebuah biz_code, atau string kosong kalau tidak dikenal."""
    if code is None:
        return ""
    return (COMPLETION_ERROR_CODES.get(code)
            or GLOBAL_ERROR_CODES.get(code)
            or "")


class DeepSeekError(RuntimeError):
    """Error dari API DeepSeek yang membawa kode machine-readable."""

    def __init__(self, message: str, biz_code: Optional[int] = None,
                 biz_msg: str = "", mute_until: Optional[float] = None,
                 transient: bool = False, truncated: bool = False):
        super().__init__(message)
        self.biz_code = biz_code
        self.biz_msg = biz_msg
        self.mute_until = mute_until
        # Sementara: gagal karena jaringan atau 5xx, bukan karena akun
        # bermasalah. Request semacam ini layak diulang pada koneksi baru.
        self.transient = transient
        # Terpotong: stream mulai berisi teks lalu mati sebelum selesai.
        # Ini bahaya untuk coding karena balasan setengah jadi membuat tool
        # call rusak, jadi wajib dibedakan dari balasan yang memang kosong.
        self.truncated = truncated

    @property
    def is_muted(self) -> bool:
        return self.biz_msg == "user is muted"

    @property
    def is_invalid_token(self) -> bool:
        # Dua jalur pengenalan. `biz_msg` adalah jalur utama, tapi DeepSeek
        # kadang mengirim penolakan token dengan `biz_msg` kosong dan hanya
        # menyertakan kode 40003. Kalau hanya mengandalkan pesan, kasus itu
        # tidak terdeteksi dan token mati dipakai ulang terus.
        if "INVALID_TOKEN" in (self.biz_msg or ""):
            return True
        return self.biz_code == 40003


class DeepSeekChallengeRequired(DeepSeekError):
    """DeepSeek mengembalikan 40303 CHALLENGE_REQUIRED (hCaptcha verification needed)."""

    def __init__(self, message: str, biz_code: int = 40303, biz_msg: str = "CHALLENGE_REQUIRED", challenge_data: Optional[dict] = None):
        super().__init__(message, biz_code=biz_code, biz_msg=biz_msg, transient=True)
        self.challenge_data = challenge_data or {}


def _biz(data: dict) -> dict:
    """Buka envelope `data.biz_data`, raise bila ada error level API.

    Mendukung dua bentuk envelope:
    1. Root-level error / challenge (code: 40303 CHALLENGE_REQUIRED, code: 40003 INVALID_TOKEN).
    2. Business-level envelope (code: 0, data: {biz_code: 0, biz_data: {...}}).
    """
    code = data.get("code")
    msg = data.get("msg") or ""

    # Jalur Root-Level Challenge (hCaptcha / WAF verification)
    if code == 40303 or msg == "CHALLENGE_REQUIRED":
        biz_info = data.get("data") if isinstance(data.get("data"), dict) else {}
        raise DeepSeekChallengeRequired(
            f"DeepSeek membutuhkan verifikasi Captcha (CHALLENGE_REQUIRED): {data}",
            biz_code=40303,
            biz_msg="CHALLENGE_REQUIRED",
            challenge_data=biz_info,
        )

    if code not in (0, None) and msg and msg != "success":
        name = describe_biz_code(code)
        suffix = f" [{name}]" if name else ""
        raise DeepSeekError(
            f"DeepSeek API error{suffix}: {msg or data}",
            biz_code=code,
            biz_msg=msg,
        )

    biz = data.get("data", {}) or {}
    biz_code = biz.get("biz_code")
    biz_msg = biz.get("biz_msg") or ""

    # biz_code 0 berarti sukses. Selain itu adalah penolakan, termasuk mute.
    if biz_code not in (0, None) or biz_msg:
        mute_until = (biz.get("biz_data") or {}).get("mute_until") \
            if isinstance(biz.get("biz_data"), dict) else None
        if biz_msg == "user is muted":
            raise DeepSeekError(
                "Akun DeepSeek di-mute dan tidak bisa dipakai untuk inference.",
                biz_code=biz_code, biz_msg=biz_msg, mute_until=mute_until,
            )
        name = describe_biz_code(biz_code)
        suffix = f" [{name}]" if name else ""
        raise DeepSeekError(
            f"DeepSeek API error{suffix}: {biz_msg or data}", biz_code=biz_code,
            biz_msg=biz_msg, mute_until=mute_until,
        )

    inner = biz.get("biz_data")
    if inner is None:
        # Beberapa penolakan, termasuk INVALID_TOKEN, datang dengan
        # `biz_msg` kosong sehingga lolos dari percabangan di atas dan
        # jatuh ke sini. Kalau `code` dan `biz_code` tidak dibawa, pemanggil
        # tidak bisa membedakan token mati dari respons cacat, dan pemulihan
        # otomatis tidak pernah terpicu. Karena itu kode selalu disertakan.
        raise DeepSeekError(
            f"Bentuk respons tidak terduga: {data}",
            biz_code=biz_code if biz_code is not None else code,
            biz_msg=biz_msg,
        )
    return inner


class PowPrewarmer:
    """Buffer PoW yang memecahkan challenge di latar belakang untuk memangkas latensi TTFT."""

    def __init__(self, target_size: int = 3):
        self.target_size = target_size
        self._queue: List[tuple[str, float]] = []
        self._lock = threading.Lock()
        self._busy = False

    def pop_valid(self) -> Optional[str]:
        now = time.time()
        with self._lock:
            while self._queue:
                header, exp = self._queue.pop(0)
                # Sisakan margin minimal 15 detik sebelum challenge kedaluwarsa
                if exp - now > 15.0:
                    return header
        return None

    def replenish_async(self, client: "DeepSeekClient") -> None:
        with self._lock:
            if self._busy or len(self._queue) >= self.target_size:
                return
            self._busy = True

        def worker():
            try:
                r = client._http.post(
                    URL_POW_CHALLENGE,
                    json={"target_path": config.COMPLETION_PATH},
                    timeout=12.0,
                )
                if r.status_code == 200 and r.content and r.content.strip():
                    challenge = _biz(r.json()).get("challenge")
                    if challenge:
                        with client._pow_lock:
                            hdr = client._pow.make_header(challenge)
                        exp = float(challenge.get("expire_at", 0)) / 1000.0
                        with self._lock:
                            self._queue.append((hdr, exp))
            except Exception:
                pass
            finally:
                with self._lock:
                    self._busy = False

        t = threading.Thread(target=worker, daemon=True)
        t.start()


class DeepSeekClient:
    """Satu klien berbagi satu sesi signed-in."""

    def __init__(self, session: Optional[Session] = None, allow_interactive: bool = True):
        self.session = session or get_session(allow_interactive=allow_interactive)
        self._pow = DeepSeekPow()
        self._pow_lock = threading.Lock()
        self._pow_prewarmer = PowPrewarmer()
        self._http = cffi_requests.Session(
            headers=self._base_headers(),
            cookies=self.session.cookies,
            timeout=(30.0, 300.0),
            impersonate="chrome120",
        )
        self._pow_prewarmer.replenish_async(self)

    def _base_headers(self) -> dict:
        # Versi klien dan device id diambil dari tangkapan Playwright nyata
        # pada chat.deepseek.com, bukan dikira-kira. Sebelum patch ini bridge
        # mengirim x-client-version 2.0.0 dan tidak mengirim x-device-id sama
        # sekali, padahal website mengirim 2.7.0 beserta UUID device. Selisih
        # itu bisa dipakai pembanding di sisi server.
        return {
            "authorization": f"Bearer {self.session.token}",
            "accept": "*/*",
            "content-type": "application/json",
            "user-agent": self.session.user_agent,
            "origin": config.BASE,
            "referer": f"{config.BASE}/",
            "x-app-version": config.CLIENT_VERSION,
            "x-client-version": config.CLIENT_VERSION,
            "x-client-platform": "web",
            "x-client-locale": "en_US",
            "x-client-bundle-id": "com.deepseek.chat",
            "x-device-id": self._device_id(),
            "x-client-timezone-offset": str(self._tz_offset()),
            "x-device-model": "",
        }

    def _device_id(self) -> str:
        """Device id stabil untuk sesi ini.

        Website mengirim UUID di x-device-id. Kalau berkas sesi sudah punya
        nilai, itu dipakai. Kalau tidak, nilai diturunkan dari token supaya
        stabil antar request tanpa perlu menyimpan berkas baru. UUID acak
        tiap request sengaja dihindari karena device id yang berubah justru
        lebih mudah dikenali daripada device id yang tidak dikirim.
        """
        if self.session.device_id:
            return self.session.device_id
        return str(uuid.uuid5(uuid.NAMESPACE_URL,
                              f"dsb-device:{self.session.token[:32]}"))

    def _tz_offset(self) -> int:
        """Selisih zona waktu dalam detik, sama seperti website."""
        if self.session.timezone_offset:
            return int(self.session.timezone_offset)
        return int(-time.timezone if not time.daylight else -time.altzone)

    # --- langkah protokol ---------------------------------------------------

    def _http_call_with_retry(self, fn, *args, **kwargs):
        """Jalankan request HTTP pendek (PoW challenge, create session, history)
        dengan retry otomatis bila terjadi gangguan DNS/jaringan sesaat,
        AWS WAF Challenge, atau respons kosong.
        """
        last_err: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                r = fn(*args, **kwargs)
                # Deteksi AWS WAF JavaScript Challenge (status 202 atau header x-amzn-waf-action)
                if r.status_code == 202 or r.headers.get("x-amzn-waf-action") == "challenge":
                    log.warning(
                        "HTTP DeepSeek terhadang AWS WAF Challenge (status %d, waf: %s). "
                        "Mencoba refresh sesi headless...",
                        r.status_code, r.headers.get("x-amzn-waf-action")
                    )
                    from ..browser.auth import _headless_refresh
                    from ..state.registry import AccountRegistry
                    try:
                        reg = AccountRegistry.load()
                        for acc in reg.accounts.values():
                            if acc.session.exists():
                                from ..browser.auth import Session
                                s = Session.load(acc.session)
                                if s and s.token == self.session.token:
                                    new_sess = _headless_refresh(profile_dir=acc.profile, session_file=acc.session)
                                    if new_sess:
                                        self.session = new_sess
                                        self._http.cookies.update(new_sess.cookies)
                                        self._http.headers["authorization"] = f"Bearer {new_sess.token}"
                                        log.info("Sesi akun %s berhasil diperbarui dengan token WAF baru", acc.name)
                                        time.sleep(1.0)
                                        continue
                                    break
                    except Exception as ref_err:
                        log.debug("Auto-refresh sesi saat WAF gagal: %s", ref_err)
                    raise DeepSeekError("AWS WAF Challenge aktif, sesi sedang disegarkan", transient=True)

                r.raise_for_status()
                if not r.content or not r.content.strip():
                    raise DeepSeekError("Respons HTTP dari DeepSeek kosong (0 bytes)", transient=True)
                return r
            except DeepSeekError as de:
                last_err = de
                if attempt < 3:
                    delay = 1.0 * attempt
                    log.warning("HTTP DeepSeek gagal sementara (percobaan %d/3: %s), ulangi dalam %.1fs",
                                attempt, de, delay)
                    time.sleep(delay)
            except Exception as e:
                last_err = e
                if attempt < 3:
                    delay = 0.8 * attempt
                    log.warning("HTTP DeepSeek gagal sementara (percobaan %d/3: %s), ulangi dalam %.1fs",
                                attempt, e, delay)
                    time.sleep(delay)
        raise DeepSeekError(
            f"Gagal jaringan/WAF ke DeepSeek setelah 3 percobaan: {last_err}",
            transient=True,
        ) from last_err

    def create_chat_session(self) -> str:
        r = self._http_call_with_retry(self._http.post, URL_SESSION_CREATE, json={})
        try:
            data = r.json()
        except Exception as e:
            raise DeepSeekError(f"Gagal parse JSON create_chat_session: {e}", transient=True) from e
        return _biz(data)["chat_session"]["id"]

    def _pow_header(self) -> str:
        cached_hdr = self._pow_prewarmer.pop_valid()
        if cached_hdr:
            self._pow_prewarmer.replenish_async(self)
            return cached_hdr

        r = self._http_call_with_retry(
            self._http.post,
            URL_POW_CHALLENGE,
            json={"target_path": config.COMPLETION_PATH},
        )
        try:
            data = r.json()
        except Exception as e:
            raise DeepSeekError(f"Gagal parse JSON PoW challenge: {e}", transient=True) from e
        challenge = _biz(data).get("challenge")
        if not challenge:
            raise DeepSeekError("Respons PoW challenge tidak memuat 'challenge'", transient=True)
        with self._pow_lock:
            hdr = self._pow.make_header(challenge)
        self._pow_prewarmer.replenish_async(self)
        return hdr

    # --- API publik ---------------------------------------------------------

    def history_messages(self, session_id: str) -> List[dict]:
        """Riwayat terstruktur satu sesi, apa adanya dari server.

        Sumber tunggal untuk status pesan, beban token, dan alasan truncation.
        Setiap pesan membawa 17 field termasuk `status`, `incomplete_message`,
        `accumulated_token_usage`, `has_pending_fragment`, dan `fragments`.

        Sebelum patch ini bridge tidak memanggil endpoint ini sama sekali, jadi
        truncation ditebak dari tidak adanya penanda FINISHED padahal server
        punya field khusus untuk menyatakannya.
        """
        r = self._http_call_with_retry(
            self._http.get,
            config.BASE + config.HISTORY_PATH,
            params={"chat_session_id": session_id},
        )
        try:
            data = r.json()
        except Exception as e:
            raise DeepSeekError(f"Gagal parse JSON history_messages: {e}", transient=True) from e
        return _biz(data).get("chat_messages") or []

    def message_status(self, session_id: str, message_id: int) -> Optional[str]:
        """Status resmi satu pesan, atau None kalau pesan tidak ditemukan."""
        for m in self.history_messages(session_id):
            if m.get("message_id") == message_id:
                return m.get("status")
        return None

    def session_token_usage(self, session_id: str) -> Optional[int]:
        """Beban token kumulatif sesi, persis dari server.

        Angka ini naik monoton dan dipakai untuk memutuskan kapan memotong
        riwayat. Bridge lama memperkirakan token dari panjang teks.
        """
        msgs = self.history_messages(session_id)
        if not msgs:
            return None
        return msgs[-1].get("accumulated_token_usage")

    def continue_message(self, session_id: str, message_id: int,
                         fallback_to_resume: bool = True) -> dict:
        """Lanjutkan pesan yang berstatus INCOMPLETE.

        Teruji langsung: endpoint ini mengembalikan JSON, bukan SSE, dan
        membalas `biz_code` 22 ketika pesan sudah FINISHED. Jadi ia pemeriksa
        status, bukan mekanisme streaming. Pemanggil sebaiknya memeriksa
        status lewat `message_status` lebih dulu, supaya tidak memanggilnya
        pada pesan yang sudah lengkap.
        """
        r = self._http.post(
            config.BASE + config.CONTINUE_PATH,
            json={"chat_session_id": session_id,
                  "message_id": message_id,
                  "fallback_to_resume": fallback_to_resume},
            headers={"x-ds-pow-response": self._pow_header()},
        )
        return _biz(r.json())

    def resume_stream(self, session_id: str, message_id: int) -> dict:
        """Sambung koneksi SSE yang putus.

        Badan request hanya dua field, tanpa prompt dan tanpa
        parent_message_id. Server melanjutkan dari pesan yang sudah ada.
        """
        r = self._http.post(
            config.BASE + config.RESUME_STREAM_PATH,
            json={"chat_session_id": session_id, "message_id": message_id},
            headers={"x-ds-pow-response": self._pow_header()},
        )
        return _biz(r.json())

    def stop_stream(self, session_id: str, message_id: int) -> dict:
        """Hentikan generasi yang sedang berjalan."""
        r = self._http.post(
            config.BASE + config.STOP_STREAM_PATH,
            json={"chat_session_id": session_id, "message_id": message_id},
            headers={"x-ds-pow-response": self._pow_header()},
        )
        return _biz(r.json())

    def check_mute(self) -> dict:
        """Status mute akun, dibaca dari sumber yang benar.

        Bundle membuktikan status mute berasal dari `/api/v0/users/current`
        pada field `chat.is_muted` dan `chat.mute_until`, bukan dari pesan
        error completion. Memanggil ini sebelum request berarti akun yang
        dibungkam diketahui lebih dulu, bukan setelah request gagal.
        """
        r = self._http.get(config.BASE + config.USERS_CURRENT_PATH)
        data = _biz(r.json())
        chat = data.get("chat") or {}
        return {
            "is_muted": bool(chat.get("is_muted")),
            "mute_until": chat.get("mute_until"),
            "status": data.get("status"),
        }

    def stream(
        self,
        prompt: str,
        conversation_id: Optional[str] = None,
        model: Optional[str] = None,
        thinking: bool = False,
        search: bool = False,
    ) -> "_Stream":
        """Stream balasan. Iterasi untuk dapat potongan teks.

        Setelah selesai, baca `.conversation_id` untuk melanjutkan thread.
        `model` adalah nilai wire DeepSeek: "default" (Instant) atau "expert".
        Tidak bisa dikombinasikan dengan conversation_id, karena model sebuah
        thread ditetapkan saat thread dibuat.
        """
        if conversation_id and model is not None:
            raise ValueError(
                "`model` tidak bisa dipakai bersama `conversation_id`; model "
                "sebuah thread ditetapkan saat dibuat. Kirim `model` hanya di turn pertama."
            )
        session_id, parent_id = _decode_cid(conversation_id)
        if session_id is None:
            session_id = self.create_chat_session()
            model_type: Optional[str] = model or "default"
        else:
            model_type = None
        return _Stream(self, prompt, session_id, parent_id, model_type, thinking, search)

    def chat(
        self,
        prompt: str,
        conversation_id: Optional[str] = None,
        model: Optional[str] = None,
        thinking: bool = False,
        search: bool = False,
    ) -> Reply:
        """Kembalikan balasan lengkap beserta conversation_id-nya.

        `meta` ikut diteruskan ke Reply. Sebelum patch ini `chat` membuang
        analitik yang sudah dikumpulkan `_Stream`, sehingga pemanggil non
        streaming tidak pernah bisa mencatat conversation_mode, tool_searches,
        dan tool_opens walau datanya sudah ada.
        """
        s = self.stream(prompt, conversation_id=conversation_id,
                        model=model, thinking=thinking, search=search)
        for _kind, _text in s:
            pass
        return Reply(text=s.text, conversation_id=s.conversation_id,
                     thinking=s.thinking, meta=dict(s.meta))

    def close(self) -> None:
        self._http.close()


class _Stream:
    """Iterable potongan balasan, berupa tuple (jenis, teks).

    Jenis bernilai "thinking" untuk alur penalaran dan "content" untuk
    balasan final. Keduanya tersedia terpisah lewat properti thinking dan
    text setelah stream diiterasi, supaya pemanggil bisa menampilkannya di
    kanal berbeda seperti yang dilakukan Hermes.
    """

    def __init__(self, client: DeepSeekClient, prompt: str, session_id: str,
                 parent_id: Optional[int], model: Optional[str],
                 thinking: bool, search: bool):
        self._client = client
        self._prompt = prompt
        self._session_id = session_id
        self._parent_id = parent_id
        self._model = model
        self._thinking = thinking
        self._search = search
        self._message_id: Optional[int] = None
        self._thinking_parts: List[str] = []
        self._content_parts: List[str] = []
        # Metadata hasil stream: status pesan dari server, mode percakapan,
        # daftar query TOOL_SEARCH, daftar URL TOOL_OPEN, dan tanda
        # `auto_resume` dari event close.
        self.meta: dict = {}

    @property
    def text(self) -> str:
        """Balasan final, tanpa alur penalaran."""
        return "".join(self._content_parts)

    @property
    def thinking(self) -> str:
        """Alur penalaran kalau mode berpikir aktif."""
        return "".join(self._thinking_parts)

    def __iter__(self) -> Iterator[tuple]:
        body = {
            "chat_session_id": self._session_id,
            "parent_message_id": self._parent_id,
            "prompt": self._prompt,
            "ref_file_ids": [],
            "thinking_enabled": self._thinking,
            "search_enabled": self._search,
            "action": None,
            "preempt": False,
        }
        if self._model is not None:
            body["model_type"] = self._model
        # Challenge PoW berumur pendek, jadi diselesaikan tepat sebelum request.
        headers = {"x-ds-pow-response": self._client._pow_header()}
        meta: dict = {}
        # Flag yang dikirim dicatat ke meta supaya audit bisa memverifikasi
        # apa yang benar-benar diminta, bukan menyimpulkannya dari respons.
        # Tanpa ini kolom thinking dan search di basis data selalu nol walau
        # bridge memang mengirim true.
        meta["thinking"] = bool(self._thinking)
        meta["search"] = bool(self._search)
        try:
            with self._client._http.stream(
                "POST", URL_COMPLETION, json=body, headers=headers
            ) as resp:
                # Akun yang di-mute dibalas HTTP 200 dengan badan JSON, bukan SSE.
                # Kalau dibiarkan, parser SSE tidak menemukan teks dan request
                # tampak sukses dengan balasan kosong. Jadi periksa lebih dulu.
                content_type = (resp.headers.get("content-type") or "").lower()
                if "application/json" in content_type:
                    # Respons streaming tidak punya read(), jadi kumpulkan chunk.
                    text = b"".join(resp.iter_content()).decode("utf-8", "ignore")
                    try:
                        _biz(json.loads(text))
                    except DeepSeekError:
                        raise
                    except Exception:
                        raise DeepSeekError(f"Respons tak terduga: {text[:200]}")
                    raise DeepSeekError(
                        "Respons JSON tanpa SSE dari endpoint completion.")

                resp.raise_for_status()
                finished = False
                for kind, text in _parse_sse(resp.iter_lines(), meta):
                    if kind == "__finished__":
                        finished = True
                        continue
                    if kind == "__bad_lines__":
                        log.warning("SSE DeepSeek: %d baris tidak terbaca, "
                                    "contoh: %s", 1, text[:200])
                        continue
                    if kind == "thinking":
                        self._thinking_parts.append(text)
                    else:
                        self._content_parts.append(text)
                    yield kind, text
        except DeepSeekError:
            raise
        except Exception as e:
            raise DeepSeekError(
                f"Aliran SSE DeepSeek terputus karena gangguan jaringan: {e}",
                transient=True,
                truncated=bool(self._content_parts),
            ) from e

        if meta.get("message_id") is not None:
            self._message_id = meta["message_id"]

        # Urutan pemeriksaan status pasca-stream sangat krusial:
        # 1. Periksa `status in CONTINUABLE_STATUSES` LEBIH DULU sebelum
        #    `auto_resume is False`, karena DeepSeek mengirim `event: close`
        #    (`auto_resume: false`) di akhir hampir semua stream, termasuk saat
        #    `message_status == "INCOMPLETE"`.
        # 2. `yield from` potongan hasil `_lanjutkan_sampai_selesai` supaya
        #    pemanggil (`stream_chunks`) benar-benar menerima teks lanjutan.
        # 3. Kalau setelah itu belum ada satu karakter pun di `_content_parts`
        #    (mis. stream mati saat baru menghasilkan `thinking`), naikkan
        #    `DeepSeekError(transient=True)` agar server mengulang turn secara
        #    otomatis, bukan mengirim balasan kosong dengan finish_reason
        #    `incomplete` yang menghentikan agen di Hermes.
        if not finished and (self._content_parts or self._thinking_parts):
            status = meta.get("message_status")
            if status in CONTINUABLE_STATUSES:
                if _has_repetition_loop("".join(self._content_parts)):
                    log.warning("auto-continue dilewati: terdeteksi degenerate repetition loop dalam output!")
                    meta["repetition_loop_detected"] = True
                    self._content_parts = [_clean_repetition_loop("".join(self._content_parts))]
                else:
                    yield from self._lanjutkan_sampai_selesai(meta)
                    status = meta.get("message_status")
            if not self._content_parts and status != "FINISHED":
                self.meta = dict(meta)
                raise DeepSeekError(
                    "Stream DeepSeek berhenti saat penalaran sebelum menghasilkan "
                    "konten balasan (status=" + str(status) + ").",
                    transient=True,
                    truncated=True,
                )
            if status not in ("FINISHED", None) and status not in CONTINUABLE_STATUSES:
                if meta.get("auto_resume") is False and self._content_parts:
                    log.info("Stream DeepSeek ditutup server dengan auto_resume "
                             "false, tidak diperlakukan sebagai truncation.")
                else:
                    self.meta = dict(meta)
                    raise DeepSeekError(
                        "Stream DeepSeek terpotong sebelum selesai: "
                        f"{len(''.join(self._content_parts))} karakter balasan diterima "
                        "tanpa penanda FINISHED.",
                        transient=True, truncated=True)
            elif status is None and meta.get("auto_resume") is not False:
                self.meta = dict(meta)
                raise DeepSeekError(
                    "Stream DeepSeek terpotong sebelum selesai: "
                    f"{len(''.join(self._content_parts))} karakter balasan diterima "
                    "tanpa penanda FINISHED.",
                    transient=True, truncated=True)

        self.meta = dict(meta)

    def _lanjutkan_sampai_selesai(self, meta: dict) -> Iterator[tuple]:
        """Lanjutkan balasan INCOMPLETE lewat endpoint continue dan yield potongan barunya."""
        ronde = 0
        while ronde < MAX_CONTINUE_ROUNDS:
            if self._message_id is None:
                log.warning("continue dilewati: message_id tidak diketahui")
                return

            if _has_repetition_loop("".join(self._content_parts)):
                log.warning("continue dihentikan: terdeteksi degenerate repetition loop dalam output!")
                meta["repetition_loop_detected"] = True
                self._content_parts = [_clean_repetition_loop("".join(self._content_parts))]
                return

            ronde += 1
            log.info("balasan INCOMPLETE, lanjutkan ronde %d/%d untuk pesan %s",
                     ronde, MAX_CONTINUE_ROUNDS, self._message_id)
            try:
                hasil = self._client.continue_message(
                    self._session_id, self._message_id)
            except DeepSeekError as e:
                log.warning("continue ronde %d gagal: %s", ronde, e)
                meta["continue_failed"] = str(e)
                return
            except Exception as e:
                log.warning("continue ronde %d gagal tak terduga: %s", ronde, e)
                meta["continue_failed"] = str(e)
                return

            pesan = (hasil or {}).get("chat_message") or {}
            existing_think = "".join(self._thinking_parts)
            existing_content = "".join(self._content_parts)
            for frag in pesan.get("fragments") or []:
                if not isinstance(frag, dict):
                    continue
                teks = frag.get("content")
                if not teks:
                    continue
                if frag.get("type") in ("THINK", "THINKING"):
                    delta = teks[len(existing_think):] if teks.startswith(existing_think) else teks
                    if delta:
                        self._thinking_parts.append(delta)
                        existing_think += delta
                        yield "thinking", delta
                else:
                    delta = teks[len(existing_content):] if teks.startswith(existing_content) else teks
                    if delta:
                        self._content_parts.append(delta)
                        existing_content += delta
                        yield "content", delta
            status_baru = pesan.get("status")
            if status_baru:
                meta["message_status"] = status_baru
            meta["continue_rounds"] = ronde
            if pesan.get("accumulated_token_usage") is not None:
                meta["accumulated_token_usage"] = pesan[
                    "accumulated_token_usage"]
            if status_baru == "FINISHED":
                log.info("balasan selesai setelah %d ronde continue", ronde)
                return
            if status_baru not in CONTINUABLE_STATUSES:
                log.warning("continue berhenti: status tak dikenal %r",
                            status_baru)
                return
        log.warning("continue berhenti: batas %d ronde tercapai",
                    MAX_CONTINUE_ROUNDS)
        meta["continue_exhausted"] = True

    @property
    def conversation_id(self) -> str:
        return _encode_cid(self._session_id, self._message_id)


def _is_message_status_path(path: Optional[str]) -> bool:
    """True kalau path menunjuk status PESAN, bukan status FRAGMENT.

    DeepSeek memakai nama field yang sama untuk dua hal berbeda:

        response/status            -> status pesan, FINISHED berarti balasan selesai
        response/fragments/-1      -> status satu fragment, FINISHED berarti
                                      satu TOOL_SEARCH selesai

    Terverifikasi pada sse_deepsearch_raw.txt: status FINISHED untuk fragment
    muncul di idx 50 dan 1747, sementara stream masih berjalan dan menghasilkan
    2.600 event sesudahnya. Jadi path wajib diperiksa, tidak boleh hanya
    melihat nama field.
    """
    if not path:
        return False
    bersih = path.rstrip("/")
    return bersih == "response/status" or bersih.endswith("/response/status")


def _parse_sse(lines, meta: Optional[dict] = None) -> Iterator[tuple]:
    """Ubah SSE completion DeepSeek menjadi delta (jenis, teks).

    Jenis yang dikembalikan adalah "thinking" untuk alur penalaran dan
    "content" untuk balasan final. Pemisahan ini penting karena Hermes
    menampilkan keduanya di kanal berbeda, dan membiarkan penalaran
    tercampur ke balasan final membuat tampilan thinking tidak benar.

    Stream DeepSeek memakai beberapa event selain data mentah:

      event: ready
      data: {"request_message_id":1,"response_message_id":2,...}

      event: update_session
      data: {"updated_at":...}

      data: {"v":{"response":{...,"fragments":[{"type":"RESPONSE","content":"H"}]}}}
      data: {"p":"response/fragments/-1/content","o":"APPEND","v":"alo"}
      data: {"v":" dunia"}
      data: {"p":"response","o":"BATCH","v":[...]}

      event: title
      data: {"content":"Judul ringkas balasan"}

      event: close
      data: {"click_behavior":"none","auto_resume":false}

    Dua jalur teks yang harus ditangani. Yang utama adalah fragment RESPONSE
    pada snapshot frame lalu APPEND frame. Namun untuk balasan sangat pendek,
    DeepSeek tidak mengirim APPEND frame sama sekali dan hanya menyertakan
    teks di frame `event: title`. Jadi frame title dipakai sebagai fallback
    ketika tidak ada teks yang terambil dari fragment.

    Fragment berpikir memakai type THINK. Path-nya berbeda dari path balasan,
    yaitu memakai penanda fragments lain, jadi pelacakan path memisahkan
    keduanya tanpa mencampur.
    """
    active_path: Optional[str] = None
    active_frag_id = None
    thinking_mode = False
    emitted_by_frag: dict = {}
    title_text: Optional[str] = None
    _bad_lines: List[str] = []
    current_event: Optional[str] = None

    def handle_frag(frag: dict):
        nonlocal thinking_mode, active_path, active_frag_id
        if not isinstance(frag, dict):
            return
        ftype = frag.get("type")
        fid = frag.get("id") or id(frag)
        text = frag.get("content") or ""

        if ftype in ("THINK", "THINKING"):
            thinking_mode = True
            active_frag_id = fid
            active_path = "response/fragments/-1/content"
            prev = emitted_by_frag.get(fid, 0)
            if len(text) > prev:
                delta = text[prev:]
                emitted_by_frag[fid] = len(text)
                yield "thinking", delta

        elif ftype == "RESPONSE":
            thinking_mode = False
            active_frag_id = fid
            active_path = "response/fragments/-1/content"
            prev = emitted_by_frag.get(fid, 0)
            if len(text) > prev:
                delta = text[prev:]
                emitted_by_frag[fid] = len(text)
                yield "content", delta

        elif ftype == "TOOL_SEARCH" and meta is not None:
            meta.setdefault("tool_searches", []).append(
                [q.get("query") for q in (frag.get("queries") or [])])
        elif ftype == "TOOL_OPEN" and meta is not None:
            result = frag.get("result") or {}
            meta.setdefault("tool_opens", []).append(result.get("url"))

    for raw in lines:
        if not raw:
            continue
        line = raw.decode("utf-8", "ignore") if isinstance(raw, (bytes, bytearray)) else raw
        if line.startswith("event:"):
            current_event = line[len("event:"):].strip()
            continue
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            obj = json.loads(payload)
        except json.JSONDecodeError:
            _bad_lines.append(payload[:200])
            continue

        if "content" in obj and isinstance(obj.get("content"), str) \
                and "v" not in obj and "p" not in obj:
            title_text = obj["content"]
            continue

        if current_event == "close" and "auto_resume" in obj:
            if meta is not None:
                meta["auto_resume"] = bool(obj.get("auto_resume"))
                meta["click_behavior"] = obj.get("click_behavior")
            continue

        if current_event == "ready" and "response_message_id" in obj:
            if meta is not None:
                meta["message_id"] = obj.get("response_message_id")
                meta["request_message_id"] = obj.get("request_message_id")
                meta["model_type"] = obj.get("model_type")
            continue

        v = obj.get("v")

        # 1. Snapshot frame: objek response penuh
        if isinstance(v, dict) and "response" in v:
            if meta is not None:
                _capture_message_id(meta, v)
            for frag in v["response"].get("fragments", []):
                yield from handle_frag(frag)
            continue

        # 2. Batch frame (hanya jika p bukan langsung path fragments)
        if "p" in obj and isinstance(v, list) and obj.get("o") != "APPEND" and "fragments" not in str(obj.get("p", "")):
            induk_path = obj.get("p")
            for item in v:
                if not isinstance(item, dict):
                    continue
                if "type" in item and "p" not in item:
                    yield from handle_frag(item)
                    continue
                sub_p = item.get("p") or ""
                sub_v = item.get("v")
                if sub_p == "quasi_status":
                    if sub_v == "FINISHED":
                        thinking_mode = False
                        if meta is not None and not meta.get("_finished_emitted"):
                            meta["_finished_emitted"] = True
                            yield "__finished__", ""
                elif sub_p == "conversation_mode":
                    if meta is not None:
                        meta["conversation_mode"] = sub_v
                elif sub_p == "status":
                    lengkap = f"{induk_path}/{sub_p}" if induk_path else sub_p
                    if meta is not None:
                        if _is_message_status_path(lengkap):
                            meta["message_status"] = sub_v
                        else:
                            meta["fragment_status"] = sub_v
                    if sub_v == "FINISHED" and _is_message_status_path(lengkap):
                        thinking_mode = False
                        if meta is not None and not meta.get("_finished_emitted"):
                            meta["_finished_emitted"] = True
                            yield "__finished__", ""
                elif "fragments" in sub_p:
                    if isinstance(sub_v, list):
                        for f in sub_v:
                            yield from handle_frag(f)
                    elif isinstance(sub_v, dict):
                        yield from handle_frag(sub_v)
            continue

        # 3. Path frame
        if "p" in obj:
            p_val = obj.get("p", "")
            if not (isinstance(v, list) and "fragments" in p_val):
                active_path = p_val

            if "fragments" in p_val:
                if isinstance(v, list):
                    for f in v:
                        yield from handle_frag(f)
                    continue
                elif isinstance(v, dict):
                    yield from handle_frag(v)
                    continue

            if p_val == "response/status":
                if meta is not None:
                    meta["message_status"] = v
                if v == "FINISHED":
                    thinking_mode = False
                    if meta is not None and not meta.get("_finished_emitted"):
                        meta["_finished_emitted"] = True
                        yield "__finished__", ""
                continue

            if p_val == "response/conversation_mode":
                if meta is not None:
                    meta["conversation_mode"] = v
                continue

            if meta is not None and active_path and active_path.endswith("message_id") \
                    and isinstance(v, int):
                meta["message_id"] = v

            if isinstance(v, str) and active_path and active_path.endswith("content"):
                if active_frag_id is not None:
                    emitted_by_frag[active_frag_id] = emitted_by_frag.get(active_frag_id, 0) + len(v)
                yield ("thinking" if thinking_mode else "content"), v
            continue

        # 4. Append polos ke path yang sedang aktif
        if isinstance(v, str) and active_path and active_path.endswith("content"):
            if active_frag_id is not None:
                emitted_by_frag[active_frag_id] = emitted_by_frag.get(active_frag_id, 0) + len(v)
            yield ("thinking" if thinking_mode else "content"), v

    # Fallback ke judul jika fragment teks kosong
    if not emitted_by_frag and title_text:
        yield "content", title_text

    if _bad_lines:
        yield "__bad_lines__", "\n".join(_bad_lines[:5])


def _capture_message_id(meta: dict, snapshot: dict) -> None:
    """Best-effort: ambil message_id asisten dari snapshot frame."""
    for container in (snapshot.get("response"), snapshot):
        if isinstance(container, dict):
            mid = container.get("message_id", container.get("id"))
            if isinstance(mid, int):
                meta["message_id"] = mid
                return
