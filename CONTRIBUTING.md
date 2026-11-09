# Contributing to DeepSeek Bridge Portable 🚀

Terima kasih atas ketertarikan Anda untuk berkontribusi pada **DeepSeek Bridge Portable**! Proyek ini dibangun dengan semangat open-source untuk membuka akses agen autonomous bertenaga tinggi tanpa terbebani biaya token komersial.

Kami menyambut kontribusi dari developer, reverse engineer, hacker, dan AI practitioner di seluruh dunia.

---

## 🛠️ Cara Mulai Berkontribusi

1. **Fork Repositori:**
   Klik tombol `Fork` di pojok kanan atas repositori [GitHub inirizqullahakbar/deepseek-bridge-portable](https://github.com/Muhammad-Rizqullah-Akbar/deepseek-bridge-portable).
2. **Kloning ke Lokal:**
   ```bash
   git clone https://github.com/<username-anda>/deepseek-bridge-portable.git
   cd deepseek-bridge-portable
   ```
3. **Setup Environment Pengembangan:**
   ```bash
   # Windows:
   .\bootstrap.bat

   # Linux / macOS:
   chmod +x bootstrap.sh && ./bootstrap.sh
   ```
4. **Buat Branch Fitur:**
   ```bash
   git checkout -b feature/nama-fitur-keren
   ```
5. **Verifikasi Integritas Sistem (Zero Regression):**
   Sebelum commit, pastikan seluruh diagnosa sistem lulus 100%:
   ```bash
   deepseek-bridge check
   ```
6. **Kirim Pull Request (PR):**
   Push branch Anda dan buka Pull Request di GitHub dengan deskripsi perubahan yang jelas sesuai template PR yang disediakan.

---

## 💡 Ide & Area Kontribusi yang Dibutuhkan

Kami sangat mengapresiasi kontribusi pada area berikut:

* 🐳 **Docker & Xvfb Virtual Display:** Pembuatan Dockerfile multi-stage dengan Xvfb virtual framebuffer agar Playwright bisa login visual di Linux VPS tanpa monitor fisik.
* 🧩 **Framework Adapters:** Adapter / ekstensi untuk agen lain (seperti Claude Code, Codex, AutoGen, CrewAI).
* 📊 **Dashboard Telemetry UI:** Visualisasi chart performa token, latensi round-trip, dan status cooldown akun di dashboard web.
* ⚡ **PoW Solver Optimization:** Riset optimasi akselerasi Proof-of-Work WebAssembly pada arsitektur ARM64 / Apple Silicon.
* 📖 **Dokumentasi & Terjemahan:** Penambahan panduan setup dalam berbagai bahasa internasional.

---

## 💬 Hubungi & Berdiskusi

Punya ide arsitektur, menemukan kendala teknis, atau ingin berkolaborasi?
* **GitHub Issues:** Gunakan [Issue Tracker](https://github.com/Muhammad-Rizqullah-Akbar/deepseek-bridge-portable/issues) untuk melaporkan bug atau meminta fitur baru.
* **GitHub Discussions:** Ruang terbuka untuk tanya jawab, showcase implementasi, dan ide pengembangan masa depan di [GitHub Discussions](https://github.com/Muhammad-Rizqullah-Akbar/deepseek-bridge-portable/discussions).
* **Email Langsung:** Hubungi maintainer di **`inirizqullahakbar@gmail.com`**.

---

*Hacking is not just about subverting defenses; it's about building sovereign tools that set people free.*
