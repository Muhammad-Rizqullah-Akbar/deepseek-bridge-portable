## Deskripsi Perubahan
Ringkasan singkat mengenai apa yang diperbaiki, dioptimasi, atau ditambahkan pada Pull Request ini.

## Motivasi & Konteks
Mengapa perubahan ini dibutuhkan? Apakah menyelesaikan Issue tertentu? (Contoh: `Fixes #123`)

## Tipe Perubahan
- [ ] 🐛 Bug fix (perubahan non-breaking yang memperbaiki masalah)
- [ ] ✨ New feature (perubahan non-breaking yang menambahkan fungsionalitas)
- [ ] ⚡ Performance optimization (peningkatan latensi streaming / perataan beban pool)
- [ ] 🛡️ Security patch (mitigasi refusal neutralizer atau proteksi WAF)
- [ ] 📚 Documentation update (perbaikan / penambahan panduan)

## Checklist Validasi Kontributor
- [ ] Saya telah menjalankan `deepseek-bridge check` lokal dan seluruh diagnosa berstatus `[OK]`.
- [ ] Saya memastikan **TIDAK ADA** token sesi, cookie Chromium, atau path direktori absolut Windows yang ter-commit.
- [ ] Kode mengikuti arsitektur modular domain-driven package (`core/`, `cognitive/`, `hermes/`, `browser/`, `state/`, `cli/`).
- [ ] Dokumentasi terkait telah diperbarui jika ada penambahan flag CLI atau environment variables baru.
