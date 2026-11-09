"""CLI untuk melihat dan memantau log request serta telemetry DeepSeek Bridge.

Dapat membaca langsung dari SQLite bridge.db tanpa mengganggu atau
me-restart server bridge yang sedang berjalan.
Mendukung mode real-time tailing (--follow / -f), filter per key, akun,
status, dan sesi percakapan, serta output format JSON.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sqlite3
import sys
import time
from typing import Any, List

from ..state import db as bridge_db


def _format_row_cli(r: sqlite3.Row) -> str:
    """Format satu baris request telemetry untuk tampilan CLI."""
    end_ts = float(r["created_at"] or time.time())
    lat_ms = int(r["latency_ms"] or 0)
    start_ts = max(0.0, end_ts - (lat_ms / 1000.0))
    start_str = datetime.datetime.fromtimestamp(start_ts).strftime("%Y-%m-%d %H:%M:%S")
    end_str = datetime.datetime.fromtimestamp(end_ts).strftime("%H:%M:%S")
    timeframe_str = f"{start_str} -> {end_str} ({lat_ms}ms)"

    key_str = r["key_name"] or "anon"
    acc_str = r["account"] or "-"
    raw_mod = r["model"] or "dsk-auto"
    mod_str = "dsk-auto" if raw_mod == "default" else raw_mod
    ses_str = r["thread_key"] or "-"
    pin = int(r["prompt_tokens"] or 0)
    pout = int(r["completion_tokens"] or 0)
    tot = pin + pout
    sent_c = int(r["sent_chars"] or 0)
    full_c = int(r["full_chars"] or 0)
    ctx_tok = max(pin, full_c // 4) if full_c > 0 else pin

    det_parts = []
    if r["status"]:
        det_parts.append(f"status={r['status']}")
    if r["reused"] is not None:
        det_parts.append("reused=1" if r["reused"] else "reused=0")
    det_parts.append("think=1" if r["thinking"] else "think=0")
    det_parts.append("search=1" if r["search"] else "search=0")
    if full_c > 0:
        pct = round(100.0 * sent_c / full_c)
        det_parts.append(f"payload={sent_c}/{full_c}c({pct}%)")
    if r["message_status"]:
        det_parts.append(f"msg={r['message_status']}")
    if r["error"]:
        det_parts.append(f"err={r['error']}")

    det_str = ", ".join(det_parts)

    return (
        f"Timeframe: [{timeframe_str:<34}] | Key: {key_str:<9} | Akun: {acc_str:<5} | "
        f"Model: {mod_str:<8} | Sesi: {ses_str:<27} | "
        f"Token: in={pin:<5} ctx={ctx_tok:<6} out={pout:<5} tot={tot:<6} | Detail: {det_str}"
    )


def cmd_log(args: argparse.Namespace) -> int:
    """Entry point untuk command 'deepseek-bridge log' / 'dsb log'."""
    db_path = bridge_db.DB_PATH
    if not db_path.exists():
        print(f"[bridge log] Database tidak ditemukan di {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    where_clauses: List[str] = []
    params: List[Any] = []

    if getattr(args, "key", None):
        where_clauses.append("key_name = ?")
        params.append(args.key)
    if getattr(args, "account", None):
        where_clauses.append("account = ?")
        params.append(args.account)
    if getattr(args, "status", None):
        where_clauses.append("status = ?")
        params.append(args.status)
    if getattr(args, "session", None):
        where_clauses.append("thread_key LIKE ?")
        params.append(f"%{args.session}%")

    where_str = ""
    if where_clauses:
        where_str = "WHERE " + " AND ".join(where_clauses)

    limit = getattr(args, "limit", 20) or 20
    is_json = getattr(args, "json", False)
    follow = getattr(args, "follow", False)

    query = f"SELECT * FROM requests {where_str} ORDER BY id DESC LIMIT ?"
    cur = conn.cursor()
    cur.execute(query, (*params, limit))
    rows = cur.fetchall()
    rows.reverse()

    last_id = 0
    if rows:
        last_id = rows[-1]["id"]

    if is_json:
        db_inst = bridge_db.get_db()
        data = [db_inst._enrich_request_row(dict(r)) for r in rows]
        print(json.dumps(data, indent=2))
        if not follow:
            return 0
    else:
        if not rows:
            print("[bridge log] Belum ada telemetry request tercatat.")
        else:
            print(f"[bridge log] Menampilkan {len(rows)} telemetry request terakhir:")
            print("=" * 165)
            for r in rows:
                print(_format_row_cli(r))
            print("=" * 165)

    if not follow:
        return 0

    print("[bridge log] Mengikuti telemetry live (tekan Ctrl+C untuk keluar)...")
    try:
        while True:
            time.sleep(1.0)
            follow_where = where_clauses + ["id > ?"]
            follow_params = params + [last_id]
            f_query = f"SELECT * FROM requests WHERE {' AND '.join(follow_where)} ORDER BY id ASC"
            cur.execute(f_query, follow_params)
            new_rows = cur.fetchall()
            for nr in new_rows:
                last_id = nr["id"]
                if is_json:
                    print(json.dumps(bridge_db.get_db()._enrich_request_row(dict(nr))))
                else:
                    print(_format_row_cli(nr))
    except KeyboardInterrupt:
        print("\n[bridge log] Pemantauan live dihentikan.")
        return 0
