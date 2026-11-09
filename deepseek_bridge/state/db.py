"""Penyimpanan SQLite untuk DeepSeek Bridge.

Modul ini menambah lapisan tahan lama di atas berkas JSON yang sudah
ada. JSON tetap dipakai untuk keputusan thread karena sudah teruji,
sedangkan SQLite menyimpan apa yang JSON tidak bisa layani dengan baik:
riwayat percakapan, log request, dan agregat pemakaian token.

Kenapa tidak mengganti JSON:
  threads.json dipakai ThreadStore untuk pencocokan sidik setiap
  request. Menggantinya berarti mengubah jalur panas yang sudah lolos
  pengujian. SQLite di sini berperan sebagai catatan audit, bukan
  sebagai penggerak keputusan thread, sehingga kegagalan di lapisan ini
  tidak pernah membuat request gagal.

Skema:

  sessions     satu baris per percakapan DeepSeek
  messages     satu baris per pesan dalam percakapan itu
  requests     satu baris per request yang melewati bridge
  usage_daily  agregat token per key per hari UTC

Setiap tabel memakai kunci alami yang stabil supaya penulisan ulang
tidak membuat duplikat. Penulisan memakai INSERT OR REPLACE, jadi
memanggil fungsi yang sama dua kali tidak menggandakan baris.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Lokasi basis data. Bisa dipindah lewat variabel lingkungan supaya
# pengujian tidak menyentuh basis data produksi.
DB_PATH = Path(os.getenv(
    "DSB_DB_PATH",
    str(Path(__file__).resolve().parent.parent / "session" / "bridge.db"),
))

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    thread_key      TEXT PRIMARY KEY,
    owner           TEXT NOT NULL,
    account         TEXT NOT NULL,
    chat_session_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
    first_user_text TEXT NOT NULL DEFAULT '',
    model_type      TEXT NOT NULL DEFAULT '',
    turns           INTEGER NOT NULL DEFAULT 0,
    sent_chars      INTEGER NOT NULL DEFAULT 0,
    full_chars      INTEGER NOT NULL DEFAULT 0,
    system_hash     TEXT NOT NULL DEFAULT '',
    tools_hash      TEXT NOT NULL DEFAULT '',
    active          INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL,
    last_used_at    REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_owner
    ON sessions(owner, last_used_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_account
    ON sessions(account, last_used_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_chat
    ON sessions(chat_session_id);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_key  TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    tokens      INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    -- Kunci alami memakai sidik isi pesan, bukan hanya nomor urut.
    -- Dua penulisan turn yang sama karena server dimulai ulang tidak
    -- menggandakan baris, sementara pesan yang benar-benar berulang
    -- tetap bisa terpisah karena seq ikut dihitung.
    UNIQUE(thread_key, seq, role)
);

CREATE INDEX IF NOT EXISTS idx_messages_thread
    ON messages(thread_key, seq);

CREATE TABLE IF NOT EXISTS requests (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_key          TEXT NOT NULL DEFAULT '',
    key_name            TEXT NOT NULL DEFAULT '',
    account             TEXT NOT NULL DEFAULT '',
    model               TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL,
    error               TEXT NOT NULL DEFAULT '',
    prompt_tokens       INTEGER NOT NULL DEFAULT 0,
    completion_tokens   INTEGER NOT NULL DEFAULT 0,
    sent_chars          INTEGER NOT NULL DEFAULT 0,
    full_chars          INTEGER NOT NULL DEFAULT 0,
    reused              INTEGER NOT NULL DEFAULT 0,
    latency_ms          INTEGER NOT NULL DEFAULT 0,
    day                 TEXT NOT NULL,
    created_at          REAL NOT NULL,
    -- Analitik agentic. Semuanya sudah dikumpulkan client.py di `meta`,
    -- hanya belum punya tempat tersimpan. Tanpa kolom ini bridge tahu
    -- apa yang dicari model dan halaman apa yang dibuka, lalu membuangnya.
    conversation_mode   TEXT NOT NULL DEFAULT '',
    tool_searches       INTEGER NOT NULL DEFAULT 0,
    tool_opens          INTEGER NOT NULL DEFAULT 0,
    message_status      TEXT NOT NULL DEFAULT '',
    thinking            INTEGER NOT NULL DEFAULT 0,
    search              INTEGER NOT NULL DEFAULT 0,
    stage_count         INTEGER NOT NULL DEFAULT 0,
    auto_resume         INTEGER NOT NULL DEFAULT -1
);

CREATE INDEX IF NOT EXISTS idx_requests_key_day
    ON requests(key_name, day);
CREATE INDEX IF NOT EXISTS idx_requests_thread
    ON requests(thread_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_requests_day
    ON requests(day);

CREATE TABLE IF NOT EXISTS usage_daily (
    day                 TEXT NOT NULL,
    key_name            TEXT NOT NULL DEFAULT '',
    account             TEXT NOT NULL DEFAULT '',
    requests            INTEGER NOT NULL DEFAULT 0,
    rejected            INTEGER NOT NULL DEFAULT 0,
    prompt_tokens       INTEGER NOT NULL DEFAULT 0,
    completion_tokens   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(day, key_name, account)
);
"""


def _day_of(ts: float) -> str:
    """Tanggal UTC dalam bentuk YYYY-MM-DD.

    Kuota harian dihitung berdasarkan UTC, bukan waktu lokal, supaya
    reset harian tidak bergeser tergantung zona waktu mesin.
    """
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def _local_midnight_epoch(day: str) -> float:
    """Epoch awal hari lokal untuk tanggal `YYYY-MM-DD`.

    Dipakai filter rentang pada tabel `requests` yang menyimpan `created_at`
    sebagai epoch detik. Tanggal tidak valid mengembalikan 0 supaya query
    tidak melempar, dan pemanggil melihat nol baris alih-alih crash.
    """
    try:
        import datetime as _dt
        y, m, d = (int(x) for x in day.split("-"))
        return _dt.datetime(y, m, d).timestamp()
    except Exception:
        return 0.0


class BridgeDB:
    """Satu koneksi SQLite dengan kunci sendiri, aman untuk multi-thread.

    Koneksi dibuat sekali dan dipakai ulang. SQLite mengizinkan satu
    penulis pada satu waktu, sehingga setiap penulisan dibungkus
    transaksi singkat dan ditahan selama mungkin hanya untuk itu.
    """

    def __init__(self, path: Path = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path), check_same_thread=False, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self) -> None:
        """Tambahkan kolom analitik ke basis data yang sudah ada.

        `CREATE TABLE IF NOT EXISTS` tidak menambah kolom ke tabel yang sudah
        terbentuk, jadi basis data lama tetap kehilangan analitik agentic
        walau skema baru sudah benar. Fungsi ini memeriksa kolom yang ada lalu
        menambahkan yang hilang, sekali jalan, aman diulang.

        `auto_resume` memakai default -1, bukan 0, karena 0 adalah nilai sah
        yang berarti server sengaja menutup stream. Tanpa pembeda, baris lama
        dan baris yang benar-benar bernilai false tidak bisa dibedakan.
        """
        ada = {r[1] for r in self._conn.execute("PRAGMA table_info(requests)")}
        tambahan = (
            ("conversation_mode", "TEXT NOT NULL DEFAULT ''"),
            ("tool_searches", "INTEGER NOT NULL DEFAULT 0"),
            ("tool_opens", "INTEGER NOT NULL DEFAULT 0"),
            ("message_status", "TEXT NOT NULL DEFAULT ''"),
            ("thinking", "INTEGER NOT NULL DEFAULT 0"),
            ("search", "INTEGER NOT NULL DEFAULT 0"),
            ("stage_count", "INTEGER NOT NULL DEFAULT 0"),
            ("auto_resume", "INTEGER NOT NULL DEFAULT -1"),
            ("continue_rounds", "INTEGER NOT NULL DEFAULT 0"),
            ("continue_exhausted", "INTEGER NOT NULL DEFAULT 0"),
        )
        for nama, tipe in tambahan:
            if nama not in ada:
                self._conn.execute(
                    f"ALTER TABLE requests ADD COLUMN {nama} {tipe}")

        # Tabel events untuk kejadian diskret: pergantian akun, throttle,
        # mute, pemulihan tool call. Dipinjam dari claude_bridge.db yang
        # sudah punya pola ini.
        self._conn.executescript("""
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    kind      TEXT NOT NULL,
    account   TEXT NOT NULL DEFAULT '',
    key_name  TEXT NOT NULL DEFAULT '',
    detail    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind, ts DESC);
""")

    # --- sesi -------------------------------------------------------------

    def upsert_session(self, rec: dict) -> None:
        """Tulis atau perbarui satu sesi.

        `rec` adalah bentuk yang sama dengan yang dipakai ThreadStore,
        jadi pemanggil tidak perlu mengubah data sebelum menyimpan.
        """
        now = time.time()
        self._write(
            """INSERT INTO sessions
               (thread_key, owner, account, chat_session_id, conversation_id,
                title, first_user_text, model_type, turns, sent_chars,
                full_chars, system_hash, tools_hash, active,
                created_at, last_used_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(thread_key) DO UPDATE SET
                 owner=excluded.owner,
                 account=excluded.account,
                 chat_session_id=excluded.chat_session_id,
                 conversation_id=excluded.conversation_id,
                 title=excluded.title,
                 first_user_text=excluded.first_user_text,
                 model_type=excluded.model_type,
                 turns=excluded.turns,
                 sent_chars=excluded.sent_chars,
                 full_chars=excluded.full_chars,
                 system_hash=excluded.system_hash,
                 tools_hash=excluded.tools_hash,
                 active=excluded.active,
                 last_used_at=excluded.last_used_at""",
            (
                str(rec.get("thread_key") or ""),
                str(rec.get("owner") or ""),
                str(rec.get("account") or ""),
                str(rec.get("chat_session_id") or ""),
                str(rec.get("conversation_id") or ""),
                str(rec.get("title") or ""),
                str(rec.get("first_user_text") or ""),
                str(rec.get("model_type") or ""),
                int(rec.get("turns") or 0),
                int(rec.get("sent_chars") or 0),
                int(rec.get("full_chars") or 0),
                str(rec.get("system_hash") or ""),
                str(rec.get("tools_hash") or ""),
                1 if rec.get("active") else 0,
                float(rec.get("created_at") or now),
                float(rec.get("last_used_at") or now),
            ),
        )

    def set_active(self, thread_key: str, active: bool) -> None:
        self._write(
            "UPDATE sessions SET active=? WHERE thread_key=?",
            (1 if active else 0, thread_key),
        )

    def clear_active(self, owner: str) -> None:
        """Matikan penanda aktif untuk semua sesi milik satu owner.

        Dipanggil sebelum menandai sesi lain sebagai aktif, supaya satu
        owner tidak pernah punya dua sesi aktif sekaligus.
        """
        self._write(
            "UPDATE sessions SET active=0 WHERE owner=? AND active=1",
            (owner,),
        )

    def list_sessions(self, owner: Optional[str] = None,
                      account: Optional[str] = None,
                      limit: int = 200) -> List[dict]:
        sql = "SELECT * FROM sessions"
        where, args = [], []
        if owner:
            where.append("owner=?")
            args.append(owner)
        if account:
            where.append("account=?")
            args.append(account)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY last_used_at DESC LIMIT ?"
        args.append(int(limit))
        return self._read(sql, tuple(args))

    def get_session(self, thread_key: str) -> Optional[dict]:
        rows = self._read(
            "SELECT * FROM sessions WHERE thread_key=?", (thread_key,))
        return rows[0] if rows else None

    def count_sessions(self, owner: Optional[str] = None) -> int:
        if owner:
            rows = self._read(
                "SELECT COUNT(*) AS n FROM sessions WHERE owner=?", (owner,))
        else:
            rows = self._read("SELECT COUNT(*) AS n FROM sessions")
        return int(rows[0]["n"]) if rows else 0

    # --- pesan ------------------------------------------------------------

    def add_message(self, thread_key: str, role: str, content: str,
                    tokens: int = 0, seq: Optional[int] = None) -> None:
        """Simpan satu pesan. `seq` dihitung otomatis kalau tidak diberikan.

        Nomor urut diambil dari pesan terakhir sesi itu, jadi urutan
        percakapan tetap benar walau pesan datang dari beberapa thread.
        """
        if seq is None:
            rows = self._read(
                "SELECT MAX(seq) AS m FROM messages WHERE thread_key=?",
                (thread_key,))
            seq = int(rows[0]["m"] or 0) + 1 if rows else 1
        self._write(
            """INSERT INTO messages
               (thread_key, seq, role, content, tokens, created_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(thread_key, seq, role) DO UPDATE SET
                 content=excluded.content,
                 tokens=excluded.tokens""",
            (thread_key, int(seq), str(role), str(content),
             int(tokens), time.time()),
        )

    def messages(self, thread_key: str, limit: int = 500) -> List[dict]:
        return self._read(
            """SELECT seq, role, content, tokens, created_at
               FROM messages WHERE thread_key=?
               ORDER BY seq LIMIT ?""",
            (thread_key, int(limit)),
        )

    # --- request ----------------------------------------------------------

    def log_request(self, *, thread_key: str = "", key_name: str = "",
                    account: str = "", model: str = "", status: str = "ok",
                    error: str = "", prompt_tokens: int = 0,
                    completion_tokens: int = 0, sent_chars: int = 0,
                    full_chars: int = 0, reused: bool = False,
                    latency_ms: int = 0, ts: Optional[float] = None,
                    conversation_mode: str = "", tool_searches: int = 0,
                    tool_opens: int = 0, message_status: str = "",
                    thinking: bool = False, search: bool = False,
                    stage_count: int = 0,
                    auto_resume: Optional[bool] = None,
                    continue_rounds: int = 0,
                    continue_exhausted: bool = False) -> None:
        """Catat satu request beserta pemakaian token dan analitik agentic.

        Analitik agentic datang dari `meta` yang dikumpulkan client.py. Semua
        opsional supaya pemanggil lama yang belum mengirimnya tetap jalan, dan
        baris yang dihasilkan jelas berbeda dari baris yang datanya memang
        kosong.

        `auto_resume` memakai None sebagai penanda "tidak diketahui", disimpan
        sebagai -1. Nilai False adalah nilai sah yang berarti server sengaja
        menutup stream, jadi tidak boleh disamakan dengan ketiadaan data.
        """
        now = time.time() if ts is None else float(ts)
        day = _day_of(now)
        with self._lock:
            self._conn.execute(
                """INSERT INTO requests
                   (thread_key, key_name, account, model, status, error,
                    prompt_tokens, completion_tokens, sent_chars, full_chars,
                    reused, latency_ms, day, created_at,
                    conversation_mode, tool_searches, tool_opens,
                    message_status, thinking, search, stage_count, auto_resume,
                    continue_rounds, continue_exhausted)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (str(thread_key), str(key_name), str(account), str(model),
                 str(status), str(error), int(prompt_tokens),
                 int(completion_tokens), int(sent_chars), int(full_chars),
                 1 if reused else 0, int(latency_ms), day, now,
                 str(conversation_mode), int(tool_searches), int(tool_opens),
                 str(message_status), 1 if thinking else 0,
                 1 if search else 0, int(stage_count),
                 -1 if auto_resume is None else (1 if auto_resume else 0),
                 int(continue_rounds), 1 if continue_exhausted else 0),
            )
            self._bump_usage(day, key_name, account, ok=(status == "ok"),
                             prompt_tokens=prompt_tokens,
                             completion_tokens=completion_tokens)
            self._conn.commit()

    def log_event(self, kind: str, account: str = "", key_name: str = "",
                  detail: str = "", ts: Optional[float] = None) -> None:
        """Catat kejadian diskret yang bukan request.

        Dipakai untuk pergantian akun, throttle, mute, dan pemulihan tool
        call. Dipisah dari `requests` karena kejadian ini bisa terjadi tanpa
        ada request yang tercatat, dan mencampurnya membuat analitika request
        tidak bisa dipercaya.
        """
        now = time.time() if ts is None else float(ts)
        with self._lock:
            self._conn.execute(
                """INSERT INTO events (ts, kind, account, key_name, detail)
                   VALUES (?,?,?,?,?)""",
                (now, str(kind), str(account), str(key_name), str(detail)[:500]),
            )
            self._conn.commit()

    def list_events(self, kind: Optional[str] = None, limit: int = 100
                    ) -> List[dict]:
        """Kejadian terbaru, bisa difilter per jenis."""
        sql = "SELECT * FROM events"
        args: tuple = ()
        if kind:
            sql += " WHERE kind = ?"
            args = (str(kind),)
        sql += " ORDER BY ts DESC LIMIT ?"
        return self._read(sql, args + (int(limit),))

    # --- analitik agentic --------------------------------------------------

    def agentic_by_mode(self, date_from: str, date_to: str,
                        account: Optional[str] = None) -> List[dict]:
        """Jumlah request per conversation_mode dalam rentang tanggal.

        Mode kosong berarti baris warisan yang dibuat sebelum patch analitik,
        jadi angkanya dipisah supaya tidak menyesatkan.
        """
        sql = """SELECT conversation_mode AS mode, COUNT(*) AS requests,
                        SUM(tool_searches) AS searches,
                        SUM(tool_opens) AS opens,
                        AVG(stage_count) AS avg_stages
                 FROM requests
                 WHERE day >= ? AND day <= ?"""
        args: list = [date_from, date_to]
        if account:
            sql += " AND account = ?"
            args.append(account)
        sql += " GROUP BY conversation_mode ORDER BY requests DESC"
        return self._read(sql, tuple(args))

    def agentic_totals(self, date_from: str, date_to: str,
                       account: Optional[str] = None) -> dict:
        """Agregat analitik agentic untuk KPI dashboard."""
        sql = """SELECT COUNT(*) AS requests,
                        SUM(tool_searches) AS searches,
                        SUM(tool_opens) AS opens,
                        SUM(CASE WHEN thinking = 1 THEN 1 ELSE 0 END)
                            AS thinking_on,
                        SUM(CASE WHEN search = 1 THEN 1 ELSE 0 END)
                            AS search_on,
                        SUM(stage_count) AS stages,
                        MAX(stage_count) AS max_stages,
                        SUM(CASE WHEN auto_resume = 0 THEN 1 ELSE 0 END)
                            AS closed_by_server
                 FROM requests
                 WHERE day >= ? AND day <= ?"""
        args: list = [date_from, date_to]
        if account:
            sql += " AND account = ?"
            args.append(account)
        rows = self._read(sql, tuple(args))
        return dict(rows[0]) if rows else {}

    def agentic_recent(self, date_from: str, date_to: str,
                       account: Optional[str] = None,
                       limit: int = 200) -> List[dict]:
        """Baris request terbaru lengkap dengan analitik agentic."""
        sql = """SELECT id, thread_key, key_name, account, model, status,
                        conversation_mode, tool_searches, tool_opens,
                        message_status, thinking, search, stage_count,
                        auto_resume, continue_rounds, continue_exhausted,
                        latency_ms, day, created_at
                 FROM requests
                 WHERE day >= ? AND day <= ?"""
        args: list = [date_from, date_to]
        if account:
            sql += " AND account = ?"
            args.append(account)
        sql += " ORDER BY id DESC LIMIT ?"
        return self._read(sql, tuple(args) + (int(limit),))

    def _bump_usage(self, day: str, key_name: str, account: str, *,
                    ok: bool, prompt_tokens: int, completion_tokens: int
                    ) -> None:
        """Tambahkan satu baris agregat harian.

        Dipanggil di dalam transaksi yang sudah dipegang pemanggil, jadi
        tidak mengambil kunci lagi.
        """
        self._conn.execute(
            """INSERT INTO usage_daily
               (day, key_name, account, requests, rejected,
                prompt_tokens, completion_tokens)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(day, key_name, account) DO UPDATE SET
                 requests=requests+excluded.requests,
                 rejected=rejected+excluded.rejected,
                 prompt_tokens=prompt_tokens+excluded.prompt_tokens,
                 completion_tokens=completion_tokens+excluded.completion_tokens""",
            (day, key_name, account, 1 if ok else 0, 0 if ok else 1,
             int(prompt_tokens), int(completion_tokens)),
        )

    def _enrich_request_row(self, r: dict) -> dict:
        """Lengkapi baris request SQLite dengan seluruh field telemetry terstandar."""
        end_ts = float(r.get("created_at") or time.time())
        lat_ms = float(r.get("latency_ms") or 0.0)
        start_ts = max(0.0, end_ts - (lat_ms / 1000.0))
        pin = int(r.get("prompt_tokens") or 0)
        pout = int(r.get("completion_tokens") or 0)
        sent_c = int(r.get("sent_chars") or 0)
        full_c = int(r.get("full_chars") or 0)
        ctx_tok = max(pin, full_c // 4) if full_c > 0 else pin
        reused = bool(r.get("reused"))
        cached_tok = max(0, ctx_tok - pin) if reused else 0
        raw_model = str(r.get("model") or "").strip()
        model_norm = "dsk-auto" if raw_model in ("", "default") else raw_model

        det_parts = []
        det_parts.append("reused=1" if reused else "reused=0")
        det_parts.append("think=1" if r.get("thinking") else "think=0")
        det_parts.append("search=1" if r.get("search") else "search=0")
        if full_c > 0:
            pct = round(100.0 * sent_c / full_c)
            det_parts.append(f"payload={sent_c}/{full_c}c ({pct}%)")
        elif sent_c > 0:
            det_parts.append(f"payload={sent_c}c")
        if r.get("conversation_mode"):
            det_parts.append(f"mode={r['conversation_mode']}")
        if r.get("tool_searches"):
            det_parts.append(f"searches={r['tool_searches']}")
        if r.get("tool_opens"):
            det_parts.append(f"opens={r['tool_opens']}")
        if r.get("message_status"):
            det_parts.append(f"msg={r['message_status']}")
        if r.get("error"):
            det_parts.append(f"err={r['error']}")

        r["model"] = model_norm
        r["ts"] = round(end_ts, 3)
        r["start_ts"] = round(start_ts, 3)
        r["end_ts"] = round(end_ts, 3)
        r["key"] = r.get("key_name") or "anon"
        r["thread"] = r.get("thread_key") or "-"
        r["total_tokens"] = pin + pout
        r["context_tokens"] = ctx_tok
        r["cached_tokens"] = cached_tok
        r["detail"] = " | ".join(det_parts)
        return r

    def list_requests(self, key_name: Optional[str] = None,
                      account: Optional[str] = None,
                      status: Optional[str] = None,
                      day: Optional[str] = None,
                      thread_key: Optional[str] = None,
                      limit: int = 200) -> List[dict]:
        sql = """SELECT id, thread_key, key_name, account, model, status,
                        error, prompt_tokens, completion_tokens, sent_chars,
                        full_chars, reused, latency_ms, day, created_at,
                        conversation_mode, tool_searches, tool_opens,
                        message_status, thinking, search, stage_count,
                        auto_resume, continue_rounds, continue_exhausted
                 FROM requests"""
        where, args = [], []
        if thread_key:
            where.append("thread_key=?")
            args.append(thread_key)
        if key_name:
            where.append("key_name=?")
            args.append(key_name)
        if account:
            where.append("account=?")
            args.append(account)
        if status:
            where.append("status=?")
            args.append(status)
        if day:
            where.append("day=?")
            args.append(day)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        return [self._enrich_request_row(r) for r in self._read(sql, tuple(args))]

    # --- agregat ----------------------------------------------------------

    def usage_by_key(self, day: Optional[str] = None) -> List[dict]:
        """Total pemakaian per key, opsional dibatasi satu hari."""
        if day:
            sql = """SELECT key_name,
                            SUM(requests) AS requests,
                            SUM(rejected) AS rejected,
                            SUM(prompt_tokens) AS prompt_tokens,
                            SUM(completion_tokens) AS completion_tokens
                     FROM usage_daily WHERE day=?
                     GROUP BY key_name ORDER BY prompt_tokens DESC"""
            return self._read(sql, (day,))
        sql = """SELECT key_name,
                        SUM(requests) AS requests,
                        SUM(rejected) AS rejected,
                        SUM(prompt_tokens) AS prompt_tokens,
                        SUM(completion_tokens) AS completion_tokens
                 FROM usage_daily GROUP BY key_name
                 ORDER BY prompt_tokens DESC"""
        return self._read(sql)

    def usage_today_by_key(self) -> List[dict]:
        return self.usage_by_key(_day_of(time.time()))

    def totals(self, day: Optional[str] = None) -> dict:
        """Ringkasan seluruh pemakaian, dipakai kartu KPI."""
        if day:
            rows = self._read(
                """SELECT SUM(requests) AS requests,
                          SUM(rejected) AS rejected,
                          SUM(prompt_tokens) AS prompt_tokens,
                          SUM(completion_tokens) AS completion_tokens
                   FROM usage_daily WHERE day=?""", (day,))
        else:
            rows = self._read(
                """SELECT SUM(requests) AS requests,
                          SUM(rejected) AS rejected,
                          SUM(prompt_tokens) AS prompt_tokens,
                          SUM(completion_tokens) AS completion_tokens
                   FROM usage_daily""")
        row = rows[0] if rows else {}
        return {
            "requests": int(row["requests"] or 0),
            "rejected": int(row["rejected"] or 0),
            "prompt_tokens": int(row["prompt_tokens"] or 0),
            "completion_tokens": int(row["completion_tokens"] or 0),
            "tokens": int(row["prompt_tokens"] or 0)
                      + int(row["completion_tokens"] or 0),
        }

    def usage_by_day(self, days: int = 14) -> List[dict]:
        """Deret harian untuk graf tren, terbaru di akhir."""
        return self._read(
            """SELECT day,
                      SUM(requests) AS requests,
                      SUM(prompt_tokens) AS prompt_tokens,
                      SUM(completion_tokens) AS completion_tokens
               FROM usage_daily GROUP BY day
               ORDER BY day DESC LIMIT ?""", (int(days),))

    def usage_by_range(self, date_from: str, date_to: str,
                       account: Optional[str] = None) -> List[dict]:
        """Deret harian dalam rentang tanggal, untuk time series.

        Bentuk `day` adalah `YYYY-MM-DD` sehingga perbandingan string aman.
        Rentang inklusif di kedua ujung. Filter akun opsional supaya satu
        akun bisa dilihat terpisah dari kumpulan akun.
        """
        sql = """SELECT day,
                        SUM(requests) AS requests,
                        SUM(rejected) AS rejected,
                        SUM(prompt_tokens) AS prompt_tokens,
                        SUM(completion_tokens) AS completion_tokens
                 FROM usage_daily WHERE day BETWEEN ? AND ?"""
        params: list = [date_from, date_to]
        if account:
            sql += " AND account=?"
            params.append(account)
        sql += " GROUP BY day ORDER BY day ASC"
        return self._read(sql, tuple(params))

    def usage_by_key_range(self, date_from: str, date_to: str,
                           account: Optional[str] = None) -> List[dict]:
        """Agregat per key dalam rentang tanggal, untuk tabel pembagian."""
        sql = """SELECT key_name,
                        SUM(requests) AS requests,
                        SUM(rejected) AS rejected,
                        SUM(prompt_tokens) AS prompt_tokens,
                        SUM(completion_tokens) AS completion_tokens
                 FROM usage_daily WHERE day BETWEEN ? AND ?"""
        params: list = [date_from, date_to]
        if account:
            sql += " AND account=?"
            params.append(account)
        sql += " GROUP BY key_name ORDER BY prompt_tokens DESC"
        return self._read(sql, tuple(params))

    def totals_range(self, date_from: str, date_to: str,
                     account: Optional[str] = None) -> dict:
        """Ringkasan agregat dalam rentang tanggal, dipakai kartu KPI."""
        sql = """SELECT SUM(requests) AS requests,
                        SUM(rejected) AS rejected,
                        SUM(prompt_tokens) AS prompt_tokens,
                        SUM(completion_tokens) AS completion_tokens
                 FROM usage_daily WHERE day BETWEEN ? AND ?"""
        params: list = [date_from, date_to]
        if account:
            sql += " AND account=?"
            params.append(account)
        rows = self._read(sql, tuple(params))
        row = rows[0] if rows else {}
        prompt = int(row["prompt_tokens"] or 0)
        completion = int(row["completion_tokens"] or 0)
        return {
            "requests": int(row["requests"] or 0),
            "rejected": int(row["rejected"] or 0),
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "tokens": prompt + completion,
        }

    def requests_range(self, date_from: str, date_to: str,
                       key_name: Optional[str] = None,
                       account: Optional[str] = None,
                       status: Optional[str] = None,
                       limit: int = 500) -> List[dict]:
        """Baris request dalam rentang tanggal, untuk tabel riwayat.

        `created_at` disimpan sebagai epoch detik, jadi batas rentang
        dikonversi ke epoch lokal pada awal dan akhir hari. Memakai kolom
        `day` yang sudah ada akan salah ketika zona waktu proses berbeda
        dari zona waktu saat baris ditulis, karena itu epoch dipakai.

        Rentang inklusif di kedua ujung.
        """
        start = _local_midnight_epoch(date_from)
        end = _local_midnight_epoch(date_to) + 86399.999
        sql = "SELECT * FROM requests WHERE (created_at BETWEEN ? AND ? OR day BETWEEN ? AND ?)"
        params: list = [start, end, date_from, date_to]
        if key_name:
            sql += " AND key_name=?"
            params.append(key_name)
        if account:
            sql += " AND account=?"
            params.append(account)
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        return [self._enrich_request_row(r) for r in self._read(sql, tuple(params))]

    def usage_span(self) -> dict:
        """Rentang tanggal yang benar-benar ada datanya.

        Dipakai dashboard untuk mengisi batas rentang bawaan, supaya audit
        membuka seluruh sejarah alih-alih hanya hari ini ketika tabel
        `usage_daily` sudah berisi hari lama.
        """
        rows = self._read(
            """SELECT MIN(day) AS first_day, MAX(day) AS last_day,
                      COUNT(DISTINCT day) AS days
               FROM usage_daily""")
        row = rows[0] if rows else {}
        local_today = time.strftime("%Y-%m-%d")
        last_d = row["last_day"]
        if not last_d or local_today > str(last_d):
            last_d = local_today
        return {
            "first_day": row["first_day"] or local_today,
            "last_day": last_d,
            "days": int(row["days"] or 0),
        }

    # --- utilitas ---------------------------------------------------------

    def _write(self, sql: str, args: tuple) -> None:
        with self._lock:
            try:
                self._conn.execute(sql, args)
                self._conn.commit()
            except sqlite3.Error:
                self._conn.rollback()
                raise

    def _read(self, sql: str, args: tuple = ()) -> List[dict]:
        with self._lock:
            cur = self._conn.execute(sql, args)
            return [dict(r) for r in cur.fetchall()]

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass


# Satu instans dipakai bersama. Dibuat malas supaya mengimpor modul ini
# tidak langsung membuat berkas basis data di mesin yang hanya menjalankan
# pengujian unit.
_shared: Optional[BridgeDB] = None
_shared_lock = threading.Lock()


def get_db() -> BridgeDB:
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = BridgeDB()
        return _shared


def migrate_threads_json(session_root: Path) -> int:
    """Pindahkan threads.json setiap akun ke basis data.

    Berkas JSON asli tidak dihapus. Kalau migasi dijalankan dua kali,
    hasilnya sama karena penulisan memakai kunci alami thread_key.

    Mengembalikan jumlah sesi yang berhasil dipindahkan.
    """
    root = Path(session_root)
    if not root.exists():
        return 0
    db = get_db()
    pindah = 0
    for account_dir in sorted(root.iterdir()):
        if not account_dir.is_dir():
            continue
        f = account_dir / "threads.json"
        if not f.exists():
            continue
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        threads = raw.get("threads", {}) if isinstance(raw, dict) else {}
        if not isinstance(threads, dict):
            continue
        account = str(raw.get("account") or account_dir.name)
        for key, rec in threads.items():
            if not isinstance(rec, dict):
                continue
            row = dict(rec)
            row["thread_key"] = key
            row["account"] = account
            db.upsert_session(row)
            pindah += 1
    return pindah
