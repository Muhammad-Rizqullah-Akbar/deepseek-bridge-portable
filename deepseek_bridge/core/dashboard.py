"""Dashboard web untuk DeepSeek Bridge.

Satu halaman HTML tanpa build step, dilayani langsung oleh FastAPI.
Menampilkan status akun, API key beserta pemakaiannya, dan log request
terakhir. Semua data diambil dari endpoint JSON yang sama yang dipakai
dashboard, jadi bisa juga diakses lewat curl.

Halaman dibuka di: http://127.0.0.1:8787/
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Optional

from fastapi.responses import HTMLResponse

from .. import config
from .. import db as bridge_db
from ..apikeys import get_store
from ..pool import AccountPool

# Log request terakhir, disimpan di memori. Ukuran dibatasi supaya
# tidak tumbuh tanpa batas pada server yang jalan lama.
_LOG_MAX = 200
_log_lock = threading.Lock()
_request_log: deque = deque(maxlen=_LOG_MAX)


def record_request(key_name: Optional[str], account: Optional[str],
                   model: str, status: str, latency_ms: float,
                   detail: str = "", thread: Optional[str] = None,
                   prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
    """Catat satu request untuk tab telemetry dashboard.

    `thread` dan jumlah token ikut dicatat supaya satu baris log bisa
    ditelusuri ke percakapan mana ia milik, dan biaya tokennya berapa.
    """
    entry = {
        "ts": round(time.time(), 1),
        "key": key_name or "anon",
        "account": account or "-",
        "model": model,
        "status": status,
        "latency_ms": round(latency_ms, 1),
        "detail": detail[:200],
        "thread": thread or "-",
        "prompt_tokens": int(prompt_tokens),
        "completion_tokens": int(completion_tokens),
        "total_tokens": int(prompt_tokens) + int(completion_tokens),
    }
    with _log_lock:
        _request_log.append(entry)


def recent_log(limit: int = 50) -> list:
    with _log_lock:
        items = list(_request_log)
    items.reverse()
    return items[:limit]


def _account_rows(pool: AccountPool) -> list:
    rows = []
    for st in pool.stats():
        rows.append({
            "name": st["name"],
            "muted": st["muted"],
            "cooling": st["cooling"],
            "cooldown_remaining": round(st["cooldown_remaining"], 1),
            "total_ok": st["total_ok"],
            "total_throttled": st["total_throttled"],
            "last_request_ago": st["last_request_ago"],
        })
    return rows


def _key_rows() -> list:
    rows = []
    for k in get_store().all():
        rows.append({
            "name": k.name,
            "key": k.key[:11] + "...",
            "rpm": k.rpm,
            "min_gap_s": k.min_gap_s,
            "rpm_effective": k.effective_rpm(),
            "max_concurrent": k.max_concurrent,
            "daily_budget": k.daily_budget,
            "accounts": k.accounts,
            "models": k.models,
            "enabled": k.enabled,
            "used_today": k.used_today,
            "quota_remaining": k.quota_remaining(),
            "total_requests": k.total_requests,
            "total_rejected": k.total_rejected,
            "tokens_today": k.tokens_today,
            "tokens_total": k.tokens_total,
            "prompt_tokens_total": k.prompt_tokens_total,
            "completion_tokens_total": k.completion_tokens_total,
        })
    return rows


def key_usage() -> list:
    """Ringkasan pemakaian per key, diurutkan dari yang paling banyak token.

    Dipakai panel audit supaya pemakaian tiap key terlihat tanpa harus
    membaca log baris per baris.
    """
    rows = _key_rows()
    rows.sort(key=lambda r: r.get("tokens_total") or 0, reverse=True)
    return rows


PAGE = r"""<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DeepSeek Bridge Router</title>
<style>
:root {
  --bg: #0f1115; --panel: #171a20; --panel2: #14171c; --line: #252a33;
  --text: #e7eaf0; --dim: #a8b2c1; --dim2: #8b95a3;
  --ok: #4ade80; --warn: #fbbf24; --bad: #f87171; --accent: #5b9cf8;
}
* { box-sizing: border-box; }
html, body { margin: 0; }
body {
  background: var(--bg); color: var(--text);
  font: 13.5px/1.5 system-ui, "Segoe UI", Roboto, sans-serif;
  -webkit-font-smoothing: antialiased;
}
code, .mono { font-family: ui-monospace, "Cascadia Code", Consolas, monospace; }
.topbar {
  position: sticky; top: 0; z-index: 20;
  display: flex; align-items: center; gap: 12px;
  padding: 12px 24px; background: rgba(15,17,21,.92);
  border-bottom: 1px solid var(--line); backdrop-filter: blur(6px);
}
.topbar h1 { font-size: 14px; font-weight: 600; margin: 0; letter-spacing: .01em; }
.topbar .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--ok); }
.topbar .spacer { flex: 1; }
.topbar .meta { font-size: 11.5px; color: var(--dim2); font-variant-numeric: tabular-nums; }
.wrap { max-width: 1180px; margin: 0 auto; padding: 20px 24px 64px; }
.kpis { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; margin-bottom: 20px; }
.kpi { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; }
.kpi .label { font-size: 10.5px; text-transform: uppercase; letter-spacing: .09em; color: var(--dim2); }
.kpi .value { font-size: 22px; font-weight: 600; line-height: 1.25; font-variant-numeric: tabular-nums; }
.kpi .value small { font-size: 11.5px; font-weight: 400; color: var(--dim2); }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; margin-bottom: 20px; overflow: hidden; }
.card-head { display: flex; align-items: center; gap: 10px; padding: 11px 16px; border-bottom: 1px solid var(--line); }
.card-head h2 { font-size: 11.5px; text-transform: uppercase; letter-spacing: .1em; color: var(--dim); margin: 0; font-weight: 600; }
.card-head .note { margin-left: auto; font-size: 11.5px; color: var(--dim2); }
.card-body { padding: 14px 16px; }
.card-body.flush { padding: 0; }
table { width: 100%; border-collapse: collapse; }
th {
  font-size: 10.5px; text-transform: uppercase; letter-spacing: .08em; color: var(--dim2);
  font-weight: 600; text-align: left; padding: 9px 14px; background: var(--panel2);
  border-bottom: 1px solid var(--line); white-space: nowrap;
}
td { padding: 10px 14px; border-bottom: 1px solid #1d222a; vertical-align: middle; }
tbody tr:last-child td { border-bottom: none; }
tbody tr:hover { background: rgba(255,255,255,.018); }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
td.acc { min-width: 210px; }
td.acc .name { font-weight: 600; }
td.acc .email { font-size: 11.5px; color: var(--dim); }
td.acc .path {
  font-size: 11px; color: var(--dim2); max-width: 300px; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; font-family: ui-monospace, Consolas, monospace;
}
.acts { display: flex; gap: 6px; justify-content: flex-end; white-space: nowrap; }
.pill {
  display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 999px;
  font-size: 11px; font-weight: 600; white-space: nowrap;
}
.pill.ok { background: rgba(74,222,128,.13); color: var(--ok); }
.pill.warn { background: rgba(251,191,36,.13); color: var(--warn); }
.pill.bad { background: rgba(248,113,113,.13); color: var(--bad); }
.pill.off { background: rgba(152,162,179,.13); color: var(--dim); }
.btn {
  font: inherit; font-size: 11.5px; padding: 4px 10px; border-radius: 6px;
  background: #1e232b; color: var(--text); border: 1px solid var(--line); cursor: pointer;
  transition: border-color .12s, color .12s, background .12s;
}
.btn:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
.btn:disabled { opacity: .55; cursor: progress; }
.btn.primary { background: rgba(91,156,248,.12); border-color: rgba(91,156,248,.45); color: var(--accent); }
.btn.primary:hover:not(:disabled) { background: rgba(91,156,248,.2); }
.btn.danger { background: rgba(248,81,73,.12); border-color: rgba(248,81,73,.45); color: #f85149; }
.btn.danger:hover:not(:disabled) { background: rgba(248,81,73,.25); border-color: #f85149; }
.btn.ghost { background: transparent; border-color: transparent; color: var(--dim2); padding: 4px 6px; }
.btn.ghost:hover:not(:disabled) { color: var(--accent); border-color: transparent; }
.stack { display: flex; flex-direction: column; gap: 2px; }
.sub { font-size: 11px; color: var(--dim2); }
.alert {
  background: rgba(248,113,113,.1); border: 1px solid rgba(248,113,113,.38);
  color: var(--bad); border-radius: 8px; padding: 10px 12px; font-size: 12px;
  line-height: 1.6; margin: 0 0 12px;
}
.empty { padding: 18px; text-align: center; color: var(--dim2); font-size: 12px; }

/* Gelembung percakapan. Pesan pengguna rata kanan dengan nada berbeda,
   balasan model rata kiri, supaya alur obrolan terbaca cepat tanpa
   membaca nama peran setiap baris. */
.chat { display: flex; flex-direction: column; gap: 10px; }
.msg { max-width: 78%; padding: 10px 14px; border-radius: 12px;
       border: 1px solid var(--line); }
.msg .who { font-size: 11px; color: var(--dim2); margin-bottom: 4px;
            text-transform: uppercase; letter-spacing: .06em; }
.msg .body { white-space: pre-wrap; word-break: break-word;
             font-size: 13px; line-height: 1.55; }
.msg.u { align-self: flex-end; background: var(--panel2); }
.msg.a { align-self: flex-start; background: var(--bg); }
.cvrow { display: flex; justify-content: space-between;
         align-items: flex-start; gap: 12px; margin-bottom: 14px; }
tr.klik { cursor: pointer; }
tr.klik:hover { background: var(--panel2); }
.form { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.form input {
  background: #10131a; border: 1px solid var(--line); color: var(--text);
  border-radius: 7px; padding: 7px 10px; font: inherit; font-size: 12.5px; min-width: 180px;
}
.form input:focus { outline: none; border-color: var(--accent); }
.hint { font-size: 11.5px; color: var(--dim2); margin: 10px 0 0; line-height: 1.65; }
.hint code, td code, .kv code { background: #10131a; border: 1px solid var(--line); padding: 1px 6px; border-radius: 4px; font-size: 11.5px; }
#accmsg:empty { display: none; }
#accmsg { margin-top: 10px; }
#accmsg .ok { color: var(--ok); font-size: 12px; line-height: 1.7; }
#accmsg .alert { display: inline-block; margin: 0; }
.kv { display: grid; grid-template-columns: 92px 1fr; gap: 6px 12px; font-size: 12.5px; }
.kv .k { color: var(--dim2); font-size: 11.5px; padding-top: 2px; }
.policy { padding: 11px 16px; border-top: 1px solid var(--line); background: var(--panel2); font-size: 11.5px; color: var(--dim2); line-height: 1.7; }
.policy b { color: var(--dim); font-weight: 600; }
@media (max-width: 1080px) {
  .kpis { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (max-width: 860px) {
  .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .wrap { padding: 16px 14px 48px; }
  td.acc .path { max-width: 150px; }
}
</style>
</head>
<body>
<div class="topbar">
  <span class="dot" id="health"></span>
  <h1>DeepSeek Bridge Router</h1>
  <span class="spacer"></span>
  <span class="meta" id="clock"></span>
</div>

<div class="wrap">

  <div id="warn"></div>

  <div class="kpis">
    <div class="kpi"><div class="label">Akun terdaftar</div><div class="value" id="kpi_acc">-</div></div>
    <div class="kpi"><div class="label">Sesi aktif</div><div class="value" id="kpi_sesi">-</div></div>
    <div class="kpi"><div class="label">Request sukses</div><div class="value" id="kpi_ok">-</div></div>
    <div class="kpi"><div class="label">Throttle</div><div class="value" id="kpi_thr">-</div></div>
  </div>

  <div class="card">
    <div class="card-head">
      <h2>Akun &amp; Profil Chromium</h2>
      <span class="note" id="accnote"></span>
    </div>
    <div id="acctable"></div>
  </div>

  <div class="card">
    <div class="card-head"><h2>Tambah Akun</h2></div>
    <div class="card-body">
      <div class="form">
        <input id="acc_name" placeholder="nama akun, mis. acc3" size="16">
        <input id="acc_email" placeholder="alamat Proton Mail" size="26">
        <button class="btn primary" id="acc_add">Tambah akun + profil</button>
      </div>
      <p class="hint">Satu akun, satu profil Chromium persisten. Penambahan akun langsung membuat
        <code>session/&lt;nama&gt;/profile</code> dan <code>session/&lt;nama&gt;/session.json</code>,
        jadi tidak berbagi login dengan akun lain dan tidak perlu login ulang tiap 6 jam.</p>
      <div id="accmsg"></div>
    </div>
  </div>

  <div class="card">
    <div class="card-head"><h2>Endpoint</h2></div>
    <div class="card-body">
      <div class="kv">
        <div class="k">Base URL</div><div><code>http://127.0.0.1:8787/v1</code></div>
        <div class="k">Auth</div><div>header <code>Authorization: Bearer &lt;key&gt;</code></div>
        <div class="k">Model</div><div><code>dsk-auto</code></div>
        <div class="k">Limit</div><div class="sub" id="limits"></div>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-head"><h2>API Key</h2><span class="note">kuota, RPM, dan jeda per key</span></div>
    <div class="card-body flush" id="keys"></div>
    <div class="policy">
      <div class="form">
        <input id="nk_nama" placeholder="nama key" size="14">
        <label>RPM</label><input id="nk_rpm" type="number" value="10" min="1" size="4">
        <label>Jeda (detik)</label><input id="nk_jeda" type="number" value="6" min="0" size="4">
        <label>Konkurrensi</label><input id="nk_konk" type="number" value="1" min="1" size="4">
        <button class="btn" onclick="buatKey()">Buat Key</button>
      </div>
      <div class="hint" id="nk_pesan"></div>
    </div>
  </div>

  <div class="card">
    <div class="card-head"><h2>Audit &amp; Log</h2><span class="note">halaman terpisah</span></div>
    <div class="card-body">
      <p class="hint">Pemakaian per API key, daftar sesi percakapan, dan log request
      lengkap dengan token dipindah ke halaman tersendiri. Dengan beberapa API key,
      menumpuk semuanya di satu halaman membuat tabel tidak terbaca.</p>
      <p class="hint"><a class="btn primary" href="/audit">Buka halaman audit</a></p>
    </div>
  </div>

</div>

<script>
const esc = s => String(s).replace(/[&<>"]/g, c => (
  {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function pill(text, cls) { return `<span class="pill ${cls}">${esc(text)}</span>`; }

function routingPill(rt) {
  if (!rt) return pill('tidak di pool', 'off');
  if (rt.muted) return pill('muted', 'bad');
  if (rt.cooling) return pill('cooling ' + rt.cooldown_remaining + 's', 'warn');
  return pill('sehat', 'ok');
}

function isi(id, html) { document.getElementById(id).innerHTML = html; }

// Buat API key baru. RPM dan jeda dikirim terpisah supaya laju nyata
// bisa dihitung dari jeda, bukan hanya dari plafon per menit.
async function buatKey() {
  const nama = document.getElementById('nk_nama').value.trim();
  const pesan = document.getElementById('nk_pesan');
  if (!nama) {
    pesan.textContent = 'nama key wajib diisi';
    return;
  }
  pesan.textContent = 'membuat...';
  try {
    const r = await fetch('/admin/keys', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        name: nama,
        rpm: parseInt(document.getElementById('nk_rpm').value || '10', 10),
        min_gap_s: parseFloat(document.getElementById('nk_jeda').value || '6'),
        max_concurrent: parseInt(document.getElementById('nk_konk').value || '1', 10),
      }),
    });
    const d = await r.json();
    if (d.error) {
      pesan.textContent = d.error.message || 'gagal membuat key';
      return;
    }
    pesan.innerHTML = 'key <code>' + esc(d.key) + '</code> dibuat. ' +
      'Laju nyata ' + d.rpm_effective + ' request/menit karena jeda ' +
      d.min_gap_s + ' detik. Simpan sekarang, token lengkap tidak bisa dilihat lagi.';
    document.getElementById('nk_nama').value = '';
    load();
  } catch (e) {
    pesan.textContent = 'gagal menghubungi server';
  }
}

// Ringkas angka token supaya tabel API Key tetap ringkas.
function besar(n) {
  if (!n) return '0';
  if (n < 1000) return String(n);
  return (n / 1000).toFixed(1) + 'k';
}

// Login ulang satu akun. Endpoint membuka window browser visible dan
// menunggu sampai login selesai, jadi permintaan ini bisa berjalan lama.
async function relogin(account, btn) {
  // Panggil peluncur window browser interaktif langsung
  openProfile(account, btn);
}

async function openProfile(name, btn) {
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Membuka Chrome...';
  try {
    const r = await fetch('/admin/accounts/' + encodeURIComponent(name) + '/open', {method: 'POST'});
    const o = await r.json();
    if (!r.ok) throw new Error(o.error || o.detail || 'gagal membuka');
    let count = 0;
    const pollTimer = setInterval(async () => {
      count++;
      await load();
      try {
        const check = await (await fetch('/admin/accounts/' + encodeURIComponent(name) + '/open')).json();
        if (check.open || count >= 5) {
          clearInterval(pollTimer);
        }
      } catch (err) {
        clearInterval(pollTimer);
      }
    }, 1200);
  } catch (e) {
    alert('Gagal membuka profil ' + name + ': ' + e.message);
    btn.disabled = false;
    btn.textContent = label;
  }
}

async function deleteAccount(name, btn) {
  if (!confirm('Hapus akun \'' + name + '\' dari registry?\n\nBerkas sesi dan profil di disk tidak akan dihapus.')) return;
  btn.disabled = true;
  try {
    const r = await fetch('/admin/accounts/' + encodeURIComponent(name), {method: 'DELETE'});
    const o = await r.json();
    if (!r.ok) throw new Error(o.error || o.detail || 'gagal menghapus');
    load();
  } catch (e) {
    alert('Gagal menghapus akun ' + name + ': ' + e.message);
    btn.disabled = false;
  }
}

async function closeProfile(name, btn) {
  btn.disabled = true;
  btn.textContent = 'Menutup Chrome...';
  try {
    await fetch('/admin/accounts/' + encodeURIComponent(name) + '/close', {method: 'POST'});
    let count = 0;
    const pollTimer = setInterval(async () => {
      count++;
      await load();
      try {
        const check = await (await fetch('/admin/accounts/' + encodeURIComponent(name) + '/open')).json();
        if (!check.open || count >= 4) {
          clearInterval(pollTimer);
        }
      } catch (err) {
        clearInterval(pollTimer);
      }
    }, 1000);
  } catch (e) {
    btn.disabled = false;
    btn.textContent = 'Tutup Browser';
  }
}

function copyPath(path, btn) {
  navigator.clipboard.writeText(path).then(() => {
    const asli = btn.textContent;
    btn.textContent = 'tersalin';
    setTimeout(() => { btn.textContent = asli; }, 1200);
  });
}

async function addAccount() {
  const name = document.getElementById('acc_name').value.trim();
  const email = document.getElementById('acc_email').value.trim();
  const box = document.getElementById('accmsg');
  if (!name) { box.innerHTML = '<span class="alert">Nama akun wajib diisi.</span>'; return; }
  try {
    const r = await fetch('/admin/accounts', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name, email}),
    });
    const o = await r.json();
    if (!r.ok) { box.innerHTML = `<span class="alert">${esc(o.error || o.detail || 'gagal')}</span>`; return; }
    box.innerHTML = `<span class="ok">Akun <b>${esc(o.account)}</b> ditambahkan. Profil Chromium ` +
      `<code>${esc(o.profile_dir)}</code> ${o.profile_ready ? 'siap dipakai' : 'belum dibuat'}. ` +
      `Klik Buka Browser untuk login pertama, sesudah itu sesi bertahan tanpa relog.</span>`;
    document.getElementById('acc_name').value = '';
    document.getElementById('acc_email').value = '';
    load();
  } catch (e) {
    box.innerHTML = `<span class="alert">${esc(e.message)}</span>`;
  }
}

function hm(ts) {
  if (!ts) return '-';
  return new Date(ts * 1000).toLocaleTimeString('id-ID', {hour12: false});
}

async function checkIsolation() {
  try {
    const o = await (await fetch('/v1/pool/check')).json();
    const masalah = [];
    for (const [hash, names] of Object.entries(o.duplicate_tokens || {})) {
      masalah.push(`Token kembar (${hash}...): ${names.join(' dan ')} memakai akun DeepSeek yang sama.`);
    }
    const iso = o.isolation || {};
    if (iso.session_files_unique === false) masalah.push('Dua akun memakai berkas sesi yang sama.');
    if (iso.profile_dirs_unique === false) masalah.push('Dua akun memakai profil browser yang sama.');
    isi('warn', masalah.length
      ? `<div class="alert"><b>PERINGATAN ISOLASI</b><br>${masalah.map(m => esc(m)).join('<br>')}</div>`
      : '');
  } catch (e) {
    isi('warn', '');
  }
}

function policyLine(p) {
  if (!p) return '';
  const m = v => Math.round(v / 60);
  return `<div class="policy">Profil Chromium tiap akun persisten. Token di-refresh otomatis tiap
    <b>${m(p.keepalive_interval_seconds)} menit</b> bila sisa umurnya di bawah
    <b>${Math.round(p.refresh_before_expiry_seconds / 3600)} jam</b>. Batas usia token
    <b>${Math.round(p.token_max_age_seconds / 3600)} jam</b>, completion serentak
    <b>${p.max_concurrent_per_account}</b> per akun. Login ulang hanya perlu bila profil logout.</div>`;
}

// Satu akun, satu profil, satu sesi: tabel gabungan runtime + registry.
function renderAccounts(o, rtMap) {
  const rows = (o.accounts || []).map(a => {
    const rt = rtMap[a.name];
    const sesi = a.session_age_human
      ? `<div class="stack"><span>${esc(a.session_age_human)}</span>
         <span class="sub">${a.expires_in_human && a.expires_in_human !== '0d'
            ? 'sisa ' + esc(a.expires_in_human) : 'kedaluwarsa'}</span></div>`
      : pill('belum login', 'warn');
    const refresh = a.last_refresh_human
      ? pill(a.last_refresh_human + ' lalu', 'ok') : pill('menunggu', 'warn');
    return `<tr>
      <td class="acc">
        <div class="name">${esc(a.name)}</div>
        ${a.email ? `<div class="email">${esc(a.email)}</div>` : ''}
        <div class="path" title="${esc(a.profile_dir)}">${esc(a.profile_dir)}</div>
      </td>
      <td>${a.profile_exists ? pill('siap', 'ok') : pill('belum', 'warn')}</td>
      <td>${sesi}</td>
      <td>${refresh}</td>
      <td><code>${esc(a.token_prefix || '-')}</code></td>
      <td>${routingPill(rt)}${rt ? `<span class="sub"> · ${rt.total_ok} ok</span>` : ''}</td>
      <td>
        <div class="acts">
          ${a.window_open
            ? `<button class="btn closewin" data-acc="${esc(a.name)}">Tutup Browser</button>`
            : `<button class="btn primary openwin" data-acc="${esc(a.name)}">Buka Browser</button>`}
          <button class="btn relog" data-acc="${esc(a.name)}">Login Ulang</button>
          <button class="btn danger delacc" data-acc="${esc(a.name)}">Hapus</button>
          <button class="btn ghost copy" data-path="${esc(a.profile_dir)}" title="Salin path profil">copy</button>
        </div>
      </td>
    </tr>`;
  }).join('');
  isi('acctable', `
    <table><thead><tr>
      <th>Akun</th><th>Profil</th><th>Sesi</th><th>Refresh</th><th>Token</th>
      <th>Status</th><th style="text-align:right">Aksi</th>
    </tr></thead><tbody>
      ${rows || '<tr><td colspan="7" class="empty">Belum ada akun. Tambahkan di bawah.</td></tr>'}
    </tbody></table>
    ${policyLine(o.policy)}`);
  document.querySelectorAll('#acctable button.relog').forEach(b => {
    b.onclick = () => relogin(b.dataset.acc, b);
  });
  document.querySelectorAll('#acctable button.openwin').forEach(b => {
    b.onclick = () => openProfile(b.dataset.acc, b);
  });
  document.querySelectorAll('#acctable button.delacc').forEach(b => {
    b.onclick = () => deleteAccount(b.dataset.acc, b);
  });
  document.querySelectorAll('#acctable button.closewin').forEach(b => {
    b.onclick = () => closeProfile(b.dataset.acc, b);
  });
  document.querySelectorAll('#acctable button.copy').forEach(b => {
    b.onclick = () => copyPath(b.dataset.path, b);
  });
}

async function load() {
  try {
    const [stats, keys, registry] = await Promise.all([
      fetch('/api/status').then(r => r.json()),
      fetch('/admin/keys').then(r => r.json()),
      fetch('/admin/accounts').then(r => r.json()),
    ]);

    document.getElementById('clock').textContent =
      'refresh ' + new Date().toLocaleTimeString('id-ID', {hour12: false});
    document.getElementById('health').style.background = 'var(--ok)';

    const rtMap = {};
    let ok = 0, thr = 0;
    for (const a of stats.accounts || []) { rtMap[a.name] = a; ok += a.total_ok; thr += a.total_throttled; }

    const reg = registry.accounts || [];
    const aktif = reg.filter(a => a.token_prefix).length;
    isi('kpi_acc', `${reg.length}` + (reg.filter(a => a.profile_exists).length !== reg.length
      ? ` <small>${reg.filter(a => a.profile_exists).length} berprofil</small>` : ''));
    isi('kpi_sesi', `${aktif}<small>/${reg.length}</small>`);
    isi('kpi_ok', `${ok}`);
    isi('kpi_thr', `${thr}`);
    isi('accnote', reg.length ? 'klik Buka Browser untuk memakai profil yang sudah login' : '');

    isi('limits', `${stats.max_concurrent_per_account} completion/akun, ` +
      `jeda ${stats.min_gap_seconds}s, cooldown ${stats.cooldown_seconds}s`);

    renderAccounts(registry, rtMap);
    checkIsolation();

    isi('keys', keys.keys.length ? `
      <table><thead><tr><th>Nama</th><th>Key</th><th class="num">RPM</th>
      <th class="num" title="Jeda minimum antar request dalam detik. Inilah yang membuat jarak request terlihat seperti orang yang mengetik dan membaca balasan.">Jeda</th>
      <th class="num" title="Laju request yang benar-benar bisa dicapai, dihitung dari jeda. Angka ini, bukan kolom RPM, yang menentukan seberapa cepat bridge bisa melayani.">RPM Nyata</th>
      <th class="num">Conc</th><th class="num">Kuota Harian</th>
      <th class="num">Request</th><th class="num">Ditolak</th>
      <th class="num">Token Hari Ini</th><th class="num">Token Total</th>
      <th>Akun</th><th>Status</th></tr></thead><tbody>
      ${keys.keys.map(k => `<tr>
        <td><b>${esc(k.name)}</b></td>
        <td><code>${esc(k.key)}</code></td>
        <td class="num">${k.rpm === 0 ? '∞' : k.rpm}</td>
        <td class="num">${k.min_gap_s ? k.min_gap_s + 's' : '-'}</td>
        <td class="num">${k.rpm_effective === 0 ? '∞' : k.rpm_effective}</td>
        <td class="num">${k.max_concurrent === 0 ? '∞' : k.max_concurrent}</td>
        <td class="num">${k.daily_budget === 0 ? '∞' : (k.used_today + '/' + k.daily_budget)}</td>
        <td class="num">${k.total_requests}</td>
        <td class="num">${k.total_rejected}</td>
        <td class="num">${k.tokens_today ? besar(k.tokens_today) : '-'}</td>
        <td class="num">${k.tokens_total ? besar(k.tokens_total) : '-'}</td>
        <td class="sub">${k.accounts ? esc(k.accounts.join(', ')) : 'semua'}</td>
        <td>${k.enabled ? pill('aktif', 'ok') : pill('mati', 'off')}</td>
      </tr>`).join('')}
      </tbody></table>` : '<div class="empty">Belum ada API key.</div>');

  } catch (e) {
    document.getElementById('health').style.background = 'var(--bad)';
    console.error(e);
  }
}

load();
setInterval(load, 5000);
document.getElementById('acc_add').onclick = addAccount;
['acc_name', 'acc_email'].forEach(id => {
  document.getElementById(id).addEventListener('keydown', ev => {
    if (ev.key === 'Enter') addAccount();
  });
});
</script>
</body>
</html>
"""


def register_routes(app) -> None:
    """Pasang route dashboard pada aplikasi FastAPI."""

    @app.get("/", response_class=HTMLResponse)
    def dashboard():
        return HTMLResponse(content=PAGE)

    @app.get("/audit", response_class=HTMLResponse)
    def audit():
        """Halaman audit terpisah: pemakaian per key, sesi, dan log request.

        Dipisah dari dashboard utama karena dengan beberapa API key, menumpuk
        tabel akun, sesi, dan log di satu halaman membuat semuanya tidak
        terbaca. Halaman ini punya filter per key, per akun, dan per status.
        """
        return HTMLResponse(content=AUDIT_PAGE)

    @app.get("/api/status")
    def api_status():
        from .server import get_pool
        pool = get_pool()
        return {
            "accounts": _account_rows(pool),
            "account_count": len(pool.accounts),
            "max_concurrent_per_account": config.MAX_CONCURRENT_COMPLETIONS,
            "min_gap_seconds": config.MIN_GAP_SECONDS,
            "cooldown_seconds": config.COOLDOWN_SECONDS,
        }

    @app.get("/api/log")
    def api_log():
        return {"items": recent_log()}

    @app.get("/api/db/sessions")
    def api_db_sessions(owner: str = "", account: str = "", limit: int = 200):
        """Daftar sesi dari SQLite, bertahan setelah server dimatikan."""
        rows = bridge_db.get_db().list_sessions(
            owner=owner or None, account=account or None, limit=limit)
        return {"count": len(rows), "sessions": rows}

    @app.get("/api/db/session/{thread_key}")
    def api_db_session(thread_key: str):
        """Satu sesi beserta seluruh isi percakapannya."""
        db = bridge_db.get_db()
        sesi = db.get_session(thread_key)
        if not sesi:
            return {"error": "sesi tidak ditemukan"}
        pesan = db.messages(thread_key)
        reqs = db.list_requests(thread_key=thread_key, limit=100)
        return {
            "session": sesi,
            "messages": pesan,
            "requests": reqs,
            "message_count": len(pesan),
            "request_count": len(reqs),
            "tokens": sum(m["tokens"] for m in pesan),
        }

    @app.get("/api/db/requests")
    def api_db_requests(key: str = "", account: str = "", status: str = "",
                        day: str = "", limit: int = 200):
        """Log request dari SQLite, bisa difilter per key dan status."""
        rows = bridge_db.get_db().list_requests(
            key_name=key or None, account=account or None,
            status=status or None, day=day or None, limit=limit)
        return {"count": len(rows), "items": rows}

    @app.get("/api/db/usage")
    def api_db_usage(day: str = ""):
        """Agregat token per key, dari SQLite."""
        db = bridge_db.get_db()
        return {
            "day": day or None,
            "keys": db.usage_by_key(day or None),
            "totals": db.totals(day or None),
            "trend": db.usage_by_day(14),
        }

    @app.get("/api/db/telemetry")
    def api_db_telemetry(date_from: str = "", date_to: str = "",
                         account: str = "", key: str = "", status: str = "",
                         limit: int = 500):
        """Telemetry lengkap dalam rentang tanggal.

        Satu panggilan mengembalikan KPI agregat, deret harian untuk time
        series, pembagian per key, dan baris riwayat request. Rentang kosong
        berarti seluruh sejarah, supaya audit lama tetap bisa dibuka.

        Bentuk tanggal `YYYY-MM-DD`. Perbandingan string aman karena kolom
        `day` dan sepuluh karakter pertama `created_at` memakai bentuk sama.
        """
        db = bridge_db.get_db()
        if not date_from or not date_to:
            span = db.usage_span()
            today = time.strftime("%Y-%m-%d")
            date_from = date_from or span["first_day"] or today
            date_to = date_to or span["last_day"] or today
        return {
            "range": {"from": date_from, "to": date_to,
                      "account": account or None,
                      "status": status or None},
            "totals": db.totals_range(date_from, date_to, account or None),
            "series": db.usage_by_range(date_from, date_to, account or None),
            "by_key": db.usage_by_key_range(date_from, date_to, account or None),
            "requests": db.requests_range(date_from, date_to,
                                          key or None, account or None,
                                          status or None, limit),
        }

    @app.get("/api/db/agentic")
    def api_db_agentic(date_from: str = "", date_to: str = "",
                       account: str = "", limit: int = 200):
        """Analitik agentic: mode, pencarian, halaman dibuka, status.

        Endpoint ini membaca kolom yang sebelumnya hilang. Sebelum patch
        analitik, client.py sudah mengumpulkan `conversation_mode`,
        `tool_searches`, dan `tool_opens` lalu membuangnya karena tidak ada
        yang meneruskan. Sekarang angkanya bisa dilihat tanpa membuka basis
        data langsung.
        """
        db = bridge_db.get_db()
        if not date_from or not date_to:
            span = db.usage_span()
            today = time.strftime("%Y-%m-%d")
            date_from = date_from or span["first_day"] or today
            date_to = date_to or span["last_day"] or today
        return {
            "range": {"from": date_from, "to": date_to,
                      "account": account or None},
            "by_mode": db.agentic_by_mode(date_from, date_to, account or None),
            "totals": db.agentic_totals(date_from, date_to, account or None),
            "recent": db.agentic_recent(date_from, date_to, account or None,
                                        limit),
        }

    @app.get("/api/db/events")
    def api_db_events(kind: str = "", limit: int = 100):
        """Kejadian diskret: throttle, mute, dan pemulihan tool call."""
        return {"items": bridge_db.get_db().list_events(kind or None, limit)}

    @app.get("/api/usage")
    def api_usage():
        """Ringkasan pemakaian per API key, untuk audit transparan.

        Berisi jumlah request, request ditolak, dan perkiraan token
        prompt, completion, serta total. Angka token adalah estimasi dari
        panjang karakter karena API web DeepSeek tidak mengembalikannya.
        """
        return {"keys": key_usage()}

    @app.get("/api/sessions")
    def api_sessions():
        """Sesi percakapan per API key, untuk audit tanpa buka web DeepSeek.

        Satu baris adalah satu percakapan: siapa pemiliknya, judulnya,
        berapa turn, di akun mana, dan berapa payload yang terkirim lawan
        payload penuh. Rasio keduanya menunjukkan seberapa efektif thread
        persisten menahan payload.
        """
        from .server import get_pool, _account_dirs, _thread_registry
        pool = get_pool()
        dirs = _account_dirs(list(pool.accounts.keys()))
        rows = _thread_registry().sessions(None, dirs)
        return {"count": len(rows), "sessions": rows}


AUDIT_PAGE = r"""<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Audit &amp; Log - DeepSeek Bridge Router</title>
<style>

:root {
  --bg: #0f1115; --panel: #171a20; --panel2: #14171c; --line: #252a33;
  --text: #e7eaf0; --dim: #a8b2c1; --dim2: #8b95a3;
  --ok: #4ade80; --warn: #fbbf24; --bad: #f87171; --accent: #5b9cf8;
}
* { box-sizing: border-box; }
html, body { margin: 0; }
body {
  background: var(--bg); color: var(--text);
  font: 13.5px/1.5 system-ui, "Segoe UI", Roboto, sans-serif;
  -webkit-font-smoothing: antialiased;
}
code, .mono { font-family: ui-monospace, "Cascadia Code", Consolas, monospace; }
.topbar {
  position: sticky; top: 0; z-index: 20;
  display: flex; align-items: center; gap: 12px;
  padding: 12px 24px; background: rgba(15,17,21,.92);
  border-bottom: 1px solid var(--line); backdrop-filter: blur(6px);
}
.topbar h1 { font-size: 14px; font-weight: 600; margin: 0; letter-spacing: .01em; }
.topbar .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--ok); }
.topbar .spacer { flex: 1; }
.topbar .meta { font-size: 11.5px; color: var(--dim2); font-variant-numeric: tabular-nums; }
.wrap { max-width: 1180px; margin: 0 auto; padding: 20px 24px 64px; }
.kpis { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 12px; margin-bottom: 20px; }
.kpi { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; }
.kpi .label { font-size: 10.5px; text-transform: uppercase; letter-spacing: .09em; color: var(--dim2); }
.kpi .value { font-size: 22px; font-weight: 600; line-height: 1.25; font-variant-numeric: tabular-nums; }
.kpi .value small { font-size: 11.5px; font-weight: 400; color: var(--dim2); }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; margin-bottom: 20px; overflow: hidden; }
.card-head { display: flex; align-items: center; gap: 10px; padding: 11px 16px; border-bottom: 1px solid var(--line); }
.card-head h2 { font-size: 11.5px; text-transform: uppercase; letter-spacing: .1em; color: var(--dim); margin: 0; font-weight: 600; }
.card-head .note { margin-left: auto; font-size: 11.5px; color: var(--dim2); }
.card-body { padding: 14px 16px; }
.card-body.flush { padding: 0; }
table { width: 100%; border-collapse: collapse; }
th {
  font-size: 10.5px; text-transform: uppercase; letter-spacing: .08em; color: var(--dim2);
  font-weight: 600; text-align: left; padding: 9px 14px; background: var(--panel2);
  border-bottom: 1px solid var(--line); white-space: nowrap;
}
td { padding: 10px 14px; border-bottom: 1px solid #1d222a; vertical-align: middle; }
tbody tr:last-child td { border-bottom: none; }
tbody tr:hover { background: rgba(255,255,255,.018); }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
td.acc { min-width: 210px; }
td.acc .name { font-weight: 600; }
td.acc .email { font-size: 11.5px; color: var(--dim); }
td.acc .path {
  font-size: 11px; color: var(--dim2); max-width: 300px; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; font-family: ui-monospace, Consolas, monospace;
}
.acts { display: flex; gap: 6px; justify-content: flex-end; white-space: nowrap; }
.pill {
  display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 999px;
  font-size: 11px; font-weight: 600; white-space: nowrap;
}
.pill.ok { background: rgba(74,222,128,.13); color: var(--ok); }
.pill.warn { background: rgba(251,191,36,.13); color: var(--warn); }
.pill.bad { background: rgba(248,113,113,.13); color: var(--bad); }
.pill.off { background: rgba(152,162,179,.13); color: var(--dim); }
.btn {
  font: inherit; font-size: 11.5px; padding: 4px 10px; border-radius: 6px;
  background: #1e232b; color: var(--text); border: 1px solid var(--line); cursor: pointer;
  transition: border-color .12s, color .12s, background .12s;
}
.btn:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
.btn:disabled { opacity: .55; cursor: progress; }
.btn.primary { background: rgba(91,156,248,.12); border-color: rgba(91,156,248,.45); color: var(--accent); }
.btn.primary:hover:not(:disabled) { background: rgba(91,156,248,.2); }
.btn.ghost { background: transparent; border-color: transparent; color: var(--dim2); padding: 4px 6px; }
.btn.ghost:hover:not(:disabled) { color: var(--accent); border-color: transparent; }
.stack { display: flex; flex-direction: column; gap: 2px; }
.sub { font-size: 11px; color: var(--dim2); }
.alert {
  background: rgba(248,113,113,.1); border: 1px solid rgba(248,113,113,.38);
  color: var(--bad); border-radius: 8px; padding: 10px 12px; font-size: 12px;
  line-height: 1.6; margin: 0 0 12px;
}
.empty { padding: 18px; text-align: center; color: var(--dim2); font-size: 12px; }

/* Gelembung percakapan. Pesan pengguna rata kanan dengan nada berbeda,
   balasan model rata kiri, supaya alur obrolan terbaca cepat tanpa
   membaca nama peran setiap baris. */
.chat { display: flex; flex-direction: column; gap: 10px; }
.msg { max-width: 78%; padding: 10px 14px; border-radius: 12px;
       border: 1px solid var(--line); }
.msg .who { font-size: 11px; color: var(--dim2); margin-bottom: 4px;
            text-transform: uppercase; letter-spacing: .06em; }
.msg .body { white-space: pre-wrap; word-break: break-word;
             font-size: 13px; line-height: 1.55; }
.msg.u { align-self: flex-end; background: var(--panel2); }
.msg.a { align-self: flex-start; background: var(--bg); }
.cvrow { display: flex; justify-content: space-between;
         align-items: flex-start; gap: 12px; margin-bottom: 14px; }
tr.klik { cursor: pointer; }
tr.klik:hover { background: var(--panel2); }
.form { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.form input {
  background: #10131a; border: 1px solid var(--line); color: var(--text);
  border-radius: 7px; padding: 7px 10px; font: inherit; font-size: 12.5px; min-width: 180px;
}
.form input:focus { outline: none; border-color: var(--accent); }
.hint { font-size: 11.5px; color: var(--dim2); margin: 10px 0 0; line-height: 1.65; }
.hint code, td code, .kv code { background: #10131a; border: 1px solid var(--line); padding: 1px 6px; border-radius: 4px; font-size: 11.5px; }
#accmsg:empty { display: none; }
#accmsg { margin-top: 10px; }
#accmsg .ok { color: var(--ok); font-size: 12px; line-height: 1.7; }
#accmsg .alert { display: inline-block; margin: 0; }
.kv { display: grid; grid-template-columns: 92px 1fr; gap: 6px 12px; font-size: 12.5px; }
.kv .k { color: var(--dim2); font-size: 11.5px; padding-top: 2px; }
.policy { padding: 11px 16px; border-top: 1px solid var(--line); background: var(--panel2); font-size: 11.5px; color: var(--dim2); line-height: 1.7; }
.policy b { color: var(--dim); font-weight: 600; }
@media (max-width: 1080px) {
  .kpis { grid-template-columns: repeat(3, minmax(0, 1fr)); }
}
@media (max-width: 860px) {
  .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .wrap { padding: 16px 14px 48px; }
  td.acc .path { max-width: 150px; }
}

.tabs { display: flex; gap: 4px; margin-bottom: 16px; border-bottom: 1px solid var(--line); }
.tab {
  font: inherit; font-size: 12px; padding: 8px 14px; background: transparent;
  border: none; border-bottom: 2px solid transparent; color: var(--dim2); cursor: pointer;
}
.tab:hover { color: var(--text); }
.tab.on { color: var(--accent); border-bottom-color: var(--accent); }
.pane { display: none; }
.pane.on { display: block; }
.filter { display: flex; gap: 8px; align-items: center; margin-bottom: 14px; flex-wrap: wrap; }
.filter select, .filter input {
  background: #10131a; border: 1px solid var(--line); color: var(--text);
  border-radius: 7px; padding: 6px 10px; font: inherit; font-size: 12px;
}
.filter label { font-size: 11.5px; color: var(--dim2); }
td.wrap { max-width: 420px; white-space: normal; line-height: 1.45; }
td.mono { font-family: ui-monospace, Consolas, monospace; font-size: 11.5px; }
.bar { height: 5px; border-radius: 3px; background: #1d222a; overflow: hidden; min-width: 60px; }
.bar > i { display: block; height: 100%; background: var(--accent); }
.bar > i.hot { background: var(--warn); }
</style>
</head>
<body>
<div class="topbar">
  <span class="dot" id="health"></span>
  <h1>Audit &amp; Log</h1>
  <span class="spacer"></span>
  <a class="btn ghost" href="/" style="color:var(--dim2);text-decoration:none">kembali ke dashboard</a>
  <span class="meta" id="clock"></span>
</div>

<div class="wrap">

  <div class="kpis">
    <div class="kpi"><div class="label">Total token</div><div class="value" id="k_tok">-</div></div>
    <div class="kpi"><div class="label">Token hari ini</div><div class="value" id="k_toktoday">-</div></div>
    <div class="kpi"><div class="label">Total request</div><div class="value" id="k_req">-</div></div>
    <div class="kpi"><div class="label">Ditolak</div><div class="value" id="k_rej">-</div></div>
    <div class="kpi"><div class="label">Sesi percakapan</div><div class="value" id="k_sesi">-</div></div>
  </div>

  <div class="tabs">
    <button class="tab on" data-pane="p_usage">Pemakaian per Key</button>
    <button class="tab" data-pane="p_sesi">Sesi Percakapan</button>
    <button class="tab" data-pane="p_log">Log Request</button>
  </div>

  <div class="pane on" id="p_usage">
    <div class="card">
      <div class="card-head">
        <h2>Pemakaian per API Key</h2>
        <span class="note">token adalah estimasi dari panjang karakter, API web DeepSeek tidak mengembalikannya</span>
      </div>
      <div class="card-body flush" id="usage"></div>
    </div>
  </div>

  <div class="pane" id="p_sesi">
    <div class="card">
      <div class="card-head">
        <h2>Sesi Percakapan</h2>
        <span class="note" id="sesinote">satu baris adalah satu percakapan DeepSeek</span>
      </div>
      <div class="filter">
        <label>Key</label>
        <select id="f_sesi_key"><option value="">semua</option></select>
        <label>Akun</label>
        <select id="f_sesi_acc"><option value="">semua</option></select>
        <label>Cari judul</label>
        <input id="f_sesi_q" placeholder="kata kunci" size="20">
      </div>
      <div class="card-body flush" id="sesi"></div>
    </div>
    <div class="card">
      <div class="card-head">
        <h2>Isi Percakapan</h2>
        <span class="note">klik satu baris sesi di atas untuk membaca isinya</span>
      </div>
      <div class="card-body" id="percakapan">
        <div class="empty">Belum ada sesi yang dipilih.</div>
      </div>
    </div>
  </div>

  <div class="pane" id="p_log">
    <div class="card">
      <div class="card-head"><h2>Log Request</h2><span class="note" id="lognote"></span></div>
      <div class="filter">
        <label>Dari</label>
        <input id="f_log_from" type="date">
        <label>Sampai</label>
        <input id="f_log_to" type="date">
        <label>Akun</label>
        <select id="f_log_acc"><option value="">semua</option></select>
        <button class="btn primary" id="f_log_go">Terapkan</button>
        <button class="btn" id="f_log_all">Seluruh sejarah</button>
        <label>Status</label>
        <select id="f_log_status">
          <option value="">semua</option>
          <option value="ok">ok</option>
          <option value="throttled">throttled</option>
          <option value="error">error</option>
        </select>
      </div>
      <div class="card-body flush" id="log"></div>
    </div>
  </div>

</div>

<script>
const esc = s => String(s).replace(/[&<>"]/g, c => (
  {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function pill(text, cls) { return `<span class="pill ${cls}">${esc(text)}</span>`; }
function isi(id, html) { document.getElementById(id).innerHTML = html; }
function hm(ts) {
  if (!ts) return '-';
  return new Date(ts * 1000).toLocaleString('id-ID', {hour12: false});
}

function idle(s) {
  if (s === null || s === undefined) return '-';
  if (s < 60) return s + 'd';
  if (s < 3600) return Math.round(s / 60) + 'mnt';
  if (s < 86400) return Math.round(s / 3600) + 'jam';
  return Math.round(s / 86400) + 'hr';
}

function besar(n) {
  if (!n) return '0';
  if (n < 1000) return String(n);
  return (n / 1000).toFixed(1) + 'k';
}

// Rasio payload terkirim lawan payload penuh. Angka kecil berarti thread
// persisten bekerja: riwayat tidak dikirim ulang, hanya delta pesan baru.
function rasio(sent, full) {
  if (!full) return '-';
  const pct = Math.round(100 * sent / full);
  const cls = pct < 10 ? 'ok' : (pct < 40 ? 'warn' : 'bad');
  return `<span class="pill ${cls}">${pct}%</span>`;
}

function renderSesi(o) {
  const rows = (o.sessions || []).map(s => {
    const aktif = s.active ? pill('terbuka', 'ok') : pill('tertutup', 'off');
    return `<tr>
      <td class="acc">
        <div class="name">${esc(s.title || s.thread)}</div>
        <div class="path" title="${esc(s.thread)}">${esc(s.thread)}</div>
      </td>
      <td><b>${esc(s.owner || '-')}</b></td>
      <td class="sub">${esc(s.account || '-')}</td>
      <td class="num">${s.turns}</td>
      <td>${aktif}</td>
      <td class="sub">${idle(s.idle_seconds)} lalu</td>
      <td class="num"><span class="sub">${besar(s.sent_chars)}/${besar(s.full_chars)}</span></td>
      <td>${rasio(s.sent_chars, s.full_chars)}</td>
      <td><code>${esc((s.chat_session_id || '-').slice(0, 10))}</code></td>
    </tr>`;
  }).join('');
  isi('sesi', rows
    ? `<table><thead><tr>
        <th>Sesi</th><th>Key</th><th>Akun</th><th class="num">Turn</th>
        <th>Status</th><th>Aktivitas</th><th class="num">Payload</th>
        <th>Rasio</th><th>Chat ID</th>
      </tr></thead><tbody>${rows}</tbody></table>`
    : '<div class="empty">Belum ada sesi percakapan. Sesi muncul di sini ' +
      'setelah request pertama melalui bridge.</div>');
  isi('sesinote', o.count + ' percakapan, ' +
    (o.sessions || []).filter(s => s.active).length + ' sedang terbuka');
}


// Batang proporsional supaya key yang paling banyak memakai token terlihat
// sekali lihat, tanpa harus membaca angka satu per satu.
function barPct(pct) {
  const cls = pct > 60 ? 'hot' : '';
  return `<div class="bar"><i class="${cls}" style="width:${Math.min(100, pct)}%"></i></div>`;
}

function renderUsage(o) {
  const rows = o.keys || [];
  const max = Math.max(1, ...rows.map(r => r.tokens_total || 0));
  isi('usage', rows.length ? `
    <table><thead><tr>
      <th>Key</th><th class="num">Request</th><th class="num">Ditolak</th>
      <th class="num">Token Hari Ini</th><th class="num">Token Total</th>
      <th class="num">Prompt</th><th class="num">Completion</th>
      <th style="width:130px">Proporsi</th><th>Akun</th><th>Status</th>
    </tr></thead><tbody>
    ${rows.map(k => {
      const pct = Math.round(100 * (k.tokens_total || 0) / max);
      return `<tr>
        <td><b>${esc(k.name)}</b><div class="sub">${esc(k.key)}</div></td>
        <td class="num">${k.total_requests}</td>
        <td class="num">${k.total_rejected}</td>
        <td class="num">${k.tokens_today ? besar(k.tokens_today) : '-'}</td>
        <td class="num">${besar(k.tokens_total || 0)}</td>
        <td class="num">${besar(k.prompt_tokens_total || 0)}</td>
        <td class="num">${besar(k.completion_tokens_total || 0)}</td>
        <td>${barPct(pct)}<span class="sub">${pct}%</span></td>
        <td class="sub">${k.accounts ? esc(k.accounts.join(', ')) : 'semua'}</td>
        <td>${k.enabled ? pill('aktif', 'ok') : pill('mati', 'off')}</td>
      </tr>`;
    }).join('')}
    </tbody></table>` : '<div class="empty">Belum ada API key.</div>');

  const tok = rows.reduce((s, k) => s + (k.tokens_total || 0), 0);
  const tokToday = rows.reduce((s, k) => s + (k.tokens_today || 0), 0);
  const req = rows.reduce((s, k) => s + (k.total_requests || 0), 0);
  const rej = rows.reduce((s, k) => s + (k.total_rejected || 0), 0);
  isi('k_tok', besar(tok));
  isi('k_toktoday', besar(tokToday));
  isi('k_req', String(req));
  isi('k_rej', String(rej));
}

function renderSesiFiltered(all) {
  const fk = document.getElementById('f_sesi_key').value;
  const fa = document.getElementById('f_sesi_acc').value;
  const q = document.getElementById('f_sesi_q').value.trim().toLowerCase();
  const rows = (all.sessions || []).filter(s =>
    (!fk || s.owner === fk) && (!fa || s.account === fa) &&
    (!q || (s.title || '').toLowerCase().includes(q) ||
             (s.thread || '').toLowerCase().includes(q)));
  isi('sesi', rows.length ? `
    <table><thead><tr>
      <th>Sesi</th><th>Key</th><th>Akun</th><th class="num">Turn</th>
      <th>Status</th><th>Aktivitas</th><th class="num">Payload</th>
      <th>Rasio</th><th>Chat ID</th>
    </tr></thead><tbody>
    ${rows.map(s => {
      const aktif = s.active ? pill('terbuka', 'ok') : pill('tertutup', 'off');
      return `<tr class="klik" onclick="lihatPercakapan('${esc(s.thread)}')"
                title="klik untuk membaca isi percakapan">
        <td class="wrap">
          <div class="name">${esc(s.title || s.thread)}</div>
          <div class="path" title="${esc(s.thread)}">${esc(s.thread)}</div>
        </td>
        <td><b>${esc(s.owner || '-')}</b></td>
        <td class="sub">${esc(s.account || '-')}</td>
        <td class="num">${s.turns}</td>
        <td>${aktif}</td>
        <td class="sub">${idle(s.idle_seconds)} lalu</td>
        <td class="num"><span class="sub">${besar(s.sent_chars)}/${besar(s.full_chars)}</span></td>
        <td>${rasio(s.sent_chars, s.full_chars)}</td>
        <td class="mono">${esc((s.chat_session_id || '-').slice(0, 12))}</td>
      </tr>`;
    }).join('')}
    </tbody></table>` : '<div class="empty">Tidak ada sesi yang cocok dengan filter.</div>');
  isi('sesinote', rows.length + ' dari ' + (all.sessions || []).length + ' percakapan, ' +
    rows.filter(s => s.active).length + ' sedang terbuka');
}

// Muat isi percakapan satu sesi dari SQLite. Dipakai saat baris sesi
// diklik, supaya percakapan bisa dibaca tanpa membuka web DeepSeek.
async function lihatPercakapan(threadKey) {
  const kotak = document.getElementById('percakapan');
  if (!kotak) return;
  kotak.innerHTML = '<div class="empty">memuat percakapan...</div>';
  try {
    const r = await fetch('/api/db/session/' + encodeURIComponent(threadKey));
    const d = await r.json();
    if (d.error) {
      kotak.innerHTML = '<div class="empty">' + esc(d.error) + '</div>';
      return;
    }
    const s = d.session || {};
    kotak.innerHTML = `
      <div class="card">
        <div class="row">
          <div>
            <div class="name">${esc(s.title || threadKey)}</div>
            <div class="path">${esc(threadKey)}</div>
          </div>
          <div class="sub">${d.message_count} pesan, ${besar(d.tokens)} token,
            ${d.request_count} request</div>
        </div>
        <div class="chat">
        ${(d.messages || []).map(m => `
          <div class="msg ${m.role === 'user' ? 'u' : 'a'}">
            <div class="who">${m.role === 'user' ? 'Anda' : 'DeepSeek'}</div>
            <div class="body">${esc(m.content)}</div>
          </div>`).join('') || '<div class="empty">Belum ada isi percakapan.</div>'}
        </div>
      </div>`;
  } catch (e) {
    kotak.innerHTML = '<div class="empty">gagal memuat percakapan</div>';
  }
}

let LOG_ALL = {items: []};

function renderLogFiltered(all) {
  const fa = document.getElementById('f_log_acc').value;
  const fs = document.getElementById('f_log_status').value;
  const rows = (all.items || []).filter(e =>
    (!fa || e.account === fa) && (!fs || e.status === fs));
  isi('log', rows.length ? `
    <table><thead><tr><th>Waktu</th><th>Key</th><th>Akun</th><th>Model</th>
      <th>Status</th><th class="num">Latensi</th><th>Sesi</th>
      <th class="num">Token</th><th>Detail</th></tr></thead><tbody>
    ${rows.map(e => {
        const cls = e.status === 'ok' ? 'ok' : (e.status === 'throttled' ? 'warn' : 'bad');
        const ts = e.ts || e.created_at;
        const k = e.key || e.key_name || '-';
        const th = e.thread || e.thread_key || '-';
        const pin = Number(e.prompt_tokens || 0);
        const pout = Number(e.completion_tokens || 0);
        const ptot = Number(e.total_tokens !== undefined ? e.total_tokens : (pin + pout));
        const detParts = [];
        if (e.detail) detParts.push(e.detail);
        if (e.error) detParts.push(e.error);
        if (e.thinking) detParts.push('think=1');
        if (e.search) detParts.push('search=1');
        if (e.reused !== undefined) detParts.push(e.reused ? 'reused=1' : 'reused=0');
        if (e.message_status) detParts.push(e.message_status);
        const detStr = detParts.join(', ');
        return `<tr>
          <td class="sub">${hm(ts)}</td>
          <td><b>${esc(k)}</b></td>
          <td>${esc(e.account || '-')}</td>
          <td>${esc(e.model || '-')}</td>
          <td>${pill(e.status, cls)}</td>
          <td class="num">${Math.round(e.latency_ms)} ms</td>
          <td class="sub" title="${esc(th)}">${esc(th.length > 28 ? '...' + th.slice(-25) : th)}</td>
          <td class="num" title="Prompt: ${pin}, Completion: ${pout}"><b>${besar(ptot)}</b> <span class="sub">(${besar(pin)}/${besar(pout)})</span></td>
          <td class="sub">${esc(detStr)}</td>
        </tr>`;
      }).join('')}
    </tbody></table>` : '<div class="empty">Belum ada request tercatat.</div>');
  isi('lognote', rows.length + ' dari ' + (all.items || []).length + ' baris');
}

async function loadTelemetry(from, to) {
  const acc = document.getElementById('f_log_acc').value;
  const st = document.getElementById('f_log_status').value;
  const q = new URLSearchParams();
  if (from) q.set('date_from', from);
  if (to) q.set('date_to', to);
  if (acc) q.set('account', acc);
  if (st) q.set('status', st);
  const r = await fetch('/api/db/telemetry?' + q.toString());
  const d = await r.json();
  LOG_ALL = {items: d.requests || []};
  const t = d.totals || {};
  isi('k_tok', besar(t.tokens || 0));
  isi('k_req', String(t.requests || 0));
  isi('k_rej', String(t.rejected || 0));
  renderLogFiltered(LOG_ALL);
  const rng = d.range || {};
  isi('lognote', (d.requests || []).length + ' baris, rentang ' +
      (rng.from || '?') + ' sampai ' + (rng.to || '?'));
}

function initLogControls() {
  const today = new Date().toISOString().slice(0, 10);
  const weekAgo = new Date(Date.now() - 6 * 864e5).toISOString().slice(0, 10);
  document.getElementById('f_log_from').value = weekAgo;
  document.getElementById('f_log_to').value = today;
  document.getElementById('f_log_go').addEventListener('click', () => {
    loadTelemetry(document.getElementById('f_log_from').value,
                  document.getElementById('f_log_to').value);
  });
  document.getElementById('f_log_all').addEventListener('click', () => {
    document.getElementById('f_log_from').value = '';
    document.getElementById('f_log_to').value = '';
    loadTelemetry('', '');
  });
}

function fillSelect(id, values) {
  const el = document.getElementById(id);
  const now = el.value;
  el.innerHTML = '<option value="">semua</option>' +
    values.map(v => `<option${v === now ? ' selected' : ''}>${esc(v)}</option>`).join('');
}

async function load() {
  try {
    const [usage, sesi, log] = await Promise.all([
      fetch('/api/usage').then(r => r.json()),
      fetch('/api/sessions').then(r => r.json()),
      fetch('/api/log').then(r => r.json()),
    ]);
    document.getElementById('clock').textContent =
      'refresh ' + new Date().toLocaleTimeString('id-ID', {hour12: false});
    document.getElementById('health').style.background = 'var(--ok)';

    renderUsage(usage);

    fillSelect('f_sesi_key', [...new Set((sesi.sessions || []).map(s => s.owner))].filter(Boolean));
    fillSelect('f_sesi_acc', [...new Set((sesi.sessions || []).map(s => s.account))].filter(Boolean));
    renderSesiFiltered(sesi);

    fillSelect('f_log_acc', [...new Set((log.items || []).map(e => e.account))].filter(Boolean));
    renderLogFiltered(log);

    isi('k_sesi', String((sesi.sessions || []).length));
  } catch (e) {
    document.getElementById('health').style.background = 'var(--bad)';
    console.error(e);
  }
}

initLogControls();
loadTelemetry('', '');
load();

document.querySelectorAll('.tab').forEach(t => {
  t.onclick = () => {
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('on'));
    document.querySelectorAll('.pane').forEach(x => x.classList.remove('on'));
    t.classList.add('on');
    document.getElementById(t.dataset.pane).classList.add('on');
  };
});
['f_sesi_key', 'f_sesi_acc', 'f_sesi_q', 'f_log_key', 'f_log_status'].forEach(id => {
  const el = document.getElementById(id);
  el.addEventListener(el.tagName === 'INPUT' ? 'input' : 'change', load);
});

load();
setInterval(load, 5000);
</script>
</body>
</html>
"""