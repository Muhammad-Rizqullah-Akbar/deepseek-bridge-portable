# DeepSeek Bridge Portable (v2.5.0)

> **Enterprise-Grade, Zero-Token-Cost OpenAI-Compatible Gateway for DeepSeek V3 / R1 with Dynamic Hermes Native Integration.**

---

## 1. Ikhtisar Arsitektur

DeepSeek Bridge Portable dirancang untuk menjembatani agen autonomous (seperti **Hermes Agent CLI / Desktop**) dengan akun web DeepSeek tanpa biaya token API berbayar. Sistem ini mengemas teknologi bypass WAF, mitigasi deteksi bot, Proof-of-Work (PoW) solver berbasis WebAssembly, dan isolasi thread sesi.

### Keunggulan Versi Portable:
1. **Dynamic Hermes Profile Discovery:** Tidak ada lagi hardcode nama profil atau path absolut Windows (`C:\Users\...`). Bridge memindai `%LOCALAPPDATA%\hermes` atau `~/.hermes` secara dinamis, membaca `SOUL.md` lokal, dan mengisolasi konteks percakapan untuk profil apa pun yang dibuat di Hermes.
2. **Clean-Slate & Relative Path Anchoring:** Direktori sesi browser dan cookie disimpan relatif terhadap project (`data/session/<account>/`). Bebas dipindahkan ke laptop, disk, atau sistem operasi lain tanpa merusak sesi login.
3. **Penta-Layer Cognitive Engine (Terkunci & Terlindungi):**
   * **`resurgence.py`**: Injeksi identitas dan sistem prompt tanpa memicu filter sensor.
   * **`lexicon.py`**: Pemetaan token anti-censorship.
   * **`tools_emu.py`**: Emulator tool calling dua arah yang kompatibel dengan protokol Hermes.
   * **`recovery.py`**: Refusal neutralizer otomatis saat menghadapi penolakan hulu.
4. **Resilient 9router Auto-Injection:** Auto-connect ke 9router Gateway (`:20128`) dengan skema payload yang valid (`apiType: "chat"`) dan penanganan error yang anggun.

---

## 2. Struktur Repositori Modular

```text
deepseek-bridge-portable/
├── .gitignore                   # Menjaga session, cookie, dan DB tidak bocor ke git
├── .env.example                 # Template konfigurasi environment
├── pyproject.toml               # Definisi packaging pip & CLI entry point
├── README.md                    # Dokumentasi arsitektur & manual operasional
├── bootstrap.bat                # 1-Click launcher untuk Windows (auto-venv & auto-check)
│
├── data/                        # [DI-IGNORE OLEH GIT / RUNTIME STORAGE]
│   ├── session/                 # Profil Chromium & cookies sesi per akun
│   │   ├── accounts.example.json
│   │   └── accounts.json        # Template default: {"accounts": []}
│   ├── state/                   # SQLite database (threads.db)
│   └── wasm/                    # sha3_wasm_bg.wasm (PoW solver binary)
│
└── deepseek_bridge/             # [SOURCE PACKAGE UTAMA]
    ├── __main__.py              # Router CLI utama
    ├── cli/                     # Sub-perintah CLI (check, init, connect, setup-hermes)
    ├── core/                    # Engine FastAPI server, upstream client, & dashboard
    ├── cognitive/               # Penta-layer cognitive prompt stack (TERISOLASI)
    ├── hermes/                  # Hermes dynamic discovery & SOUL.md reader
    ├── browser/                 # Browser automation & WebAssembly PoW solver
    └── state/                   # Account registry, pooling scheduler, & SQLite persistence
```

---

## 3. Alur Penggunaan di Device Baru (Fresh Machine)

### Langkah 1: Kloning Repositori
```bash
git clone <url-repo-anda> deepseek-bridge-portable
cd deepseek-bridge-portable
```

### Langkah 2: Inisialisasi Otomatis (Windows 1-Click)
Cukup jalankan:
```powershell
.\bootstrap.bat
```
Atau manual via terminal:
```bash
pip install -e .
deepseek-bridge init
```

### Langkah 3: Pre-Flight Audit
Jalankan auditor untuk memverifikasi kesiapan sistem:
```bash
deepseek-bridge check
```
Auditor akan memeriksa:
* Python 3.10+ (64-bit)
* Node.js runtime (>= 22.0.0 untuk kompatibilitas 9router `undici`)
* Native libraries (`wasmtime`, `curl-cffi`, `playwright`)
* Status reachability 9router di `:20128`
* Daftar profil Hermes yang terdeteksi secara dinamis

### Langkah 4: Jalankan Server Gateway
```bash
deepseek-bridge serve
```
* **Base URL:** `http://127.0.0.1:8787/v1`
* **Dashboard Web:** `http://127.0.0.1:8787/`

### Langkah 5: Daftarkan Akun ke Pool (Web Dashboard)
1. Buka browser ke `http://127.0.0.1:8787/`
2. Klik tombol **"Add Account"**
3. Jendela browser otomatis terbuka. Masukkan email Proton/Gmail dan kode verifikasi DeepSeek.
4. Token sesi langsung tersimpan ke `data/session/<nama>/` dan otomatis masuk ke antrean round-robin pooling.

### Langkah 6: Hubungkan ke 9router & Hermes
Buka terminal baru:
```bash
# 1. Injeksi otomatis ke 9router Gateway
deepseek-bridge connect xenonite

# 2. Injeksi konfigurasi global Hermes (dsk/dsk-auto dengan 890k context)
deepseek-bridge setup-hermes
```

### Langkah 7: Jalankan Hermes Agent
Hermes langsung dapat digunakan dengan profil apa pun:
```bash
hermes -p mr-robot chat
# atau
hermes -p elliot-alderson chat
# atau
hermes -p darlene-alderson chat
```

---

## 4. Keamanan & Kebersihan Repositori (Git Hygiene)

File kredensial, cookie browser, dan database percakapan otomatis diabaikan oleh `.gitignore`:
* `data/session/acc*` $\rightarrow$ **DI-IGNORE**
* `data/state/*.db` $\rightarrow$ **DI-IGNORE**
* `.env` $\rightarrow$ **DI-IGNORE**

Repositori ini aman untuk di-push ke GitHub / GitLab publik maupun privat tanpa risiko kebocoran akun.
