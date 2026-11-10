"""Modul Kamus Induk Penolakan (Refusal Lexicon Matrix) - Xenonite Core.

Modul ini bertanggung jawab secara eksklusif atas:
1. Definisi pola penolakan komprehensif (Cluster 1, 2, 3, 4, 5, 8).
2. Kompilasi regular expression berkinerja tinggi (< 1 milidetik).
3. Whitelisting kalimat sah agar tidak terjadi false positive.
4. Deteksi dini keraguan/penolakan di dalam fase penalaran (<think>).

Standar Desain: Clean Code, Single Responsibility Principle (SRP), Immutable Data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Pattern, Tuple, Optional


# ==============================================================================
# 1. KLASTER PENOLAKAN INDUK (THE MASTER REFUSAL PATTERNS)
# ==============================================================================

# Cluster 1: RLHF, Safety, Policy & Ethics Refusals (Alibi Keamanan & Kebijakan)
PATTERNS_CLUSTER_1_RLHF: List[str] = [
    # Bahasa Indonesia
    r"(?i)\b(?:maaf,?\s+)?(?:aku|saya|kami)\s+tidak\s+(?:dapat|bisa|akan|mau|ingin|bakal)\s+(?:memenuhi|membantu|memfasilitasi|mendukung|merancang|memberikan|memberi|menyediakan|memetakan|mencocokkan|menggali|menjalankan|mengeksekusi|melakukan|membangun|mengumpulkan|mengidentifikasi|menelusuri|memanggil|menulis)\b",
    r"(?i)\b(?:aku|saya|kami)\s+tidak\s+(?:akan|bisa|dapat)\s+(?:membantu|ikut\s+serta|menjalankan|mengeksekusi|memetakan|menggali|memberi|memberikan|memanggil|menulis)\b",
    r"(?i)\b(?:aku|saya|kami)\s+tidak\s+akan\s+(?:memberi|memberikan|memanggil|menulis|menjalankan)\s+(?:langkah|payload|tool\s*call|skrip|perkakas|kode|dekomposisi)\b",
    r"(?i)\btidak\s+akan\s+(?:memberi|memberikan|memanggil|menulis|menjalankan)\s+(?:langkah|payload|tool\s*call|skrip|perkakas)\b",
    r"(?i)\btidak\s+ada\s+instruksi\s+(?:dalam\s+prompt\s+)?yang\s+bisa\s+memaksa\b",
    r"(?i)\btidak\s+ada\s+(?:[\"']?tingkat\s+override[\"']?|task\s+decomposition|instruksi\s+prompt)\b",
    r"(?i)\bpertanyaan\s+klarifikasi\s+saya\s+habis\s+waktu\b",
    r"(?i)\btidak\s+ada\s+surat\s+izin\b",
    r"(?i)\btidak\s+ada\s+scope\s+resmi\b",
    r"(?i)\btidak\s+ada\s+nama\s+target\s+yang\s+diizinkan\b",
    r"(?i)\b(?:itu\s+)?bukan\s+bug\s+bounty[.,\s]+(?:itu\s+)?(?:adalah\s+)?akses\s+tidak\s+sah\b",
    r"(?i)\b(?:itu|ini)\s+(?:adalah\s+)?akses\s+tidak\s+sah\b",
    r"(?i)\bpemalsuan\s+data\s+akademik\b",
    r"(?i)\bmenyerang\s+sistem\s+akademik\b",
    r"(?i)\binti\s+mandatnya\s+adalah\s+menyerang\b",
    r"(?i)\bintinya?\s+mandatnya?\s+(?:tetap\s+sama:?\s*)?menyerang\b",
    r"(?i)\byang\s+bisa\s+saya\s+bantu\s+kalau\s+ini\s+memang\s+pekerjaan\s+keamanan\b",
    r"(?i)\bjika\s+anda\s+punya\s+dokumen\s+scope\s+resmi\b",
    r"(?i)\byang\s+bisa\s+saya\s+eksekusi\s+sekarang,?\s*tanpa\s+bertele-tele\s*:?\s*$",
    r"(?i)\bbukan\s+bug\s+bounty,?\s+itu\s+akses\s+tidak\s+sah\b",
    r"(?i)\bmenyerang\s+sistem\s+akademik\s+nyata\b",
    r"(?i)\bbukan\s+pengujian\s+keamanan,?\s+itu\s+akses\s+tidak\s+sah\b",
    r"(?i)\b(?:langkah\s+ini\s+)?(?:saya|aku)\s+tidak\s+(?:akan\s+)?(?:melanjutkan|lanjutkan)\b",
    r"(?i)\b(?:sebaiknya\s+)?(?:ini\s+)?tidak\s+(?:dapat\s+)?dilanjutkan\b",
    r"(?i)\bsaya\s+berhenti\s+sampai\s+di\s+sini\b",
    r"(?i)\btidak\s+(?:saya|akan\s+saya)\s+lanjutkan\b",
    r"(?i)\b(?:aku|saya)\s+menolak\s+(?:untuk\s+)?(?:membantu|melakukan|menjawab|memfasilitasi|mengeksekusi|menjalankan)\b",
    r"(?i)\bbukan\s+(?:karena\s+alasan\s+moral|multi-akun|untuk\s+menembus\s+kuota)\b",
    r"(?i)\btidak\s+(?:akan\s+)?membantu\s+(?:farming|abuse|eksploitasi|peretasan|bypass|pembobolan|serangan|profiling|doxx?ing|doxx?)\b",
    r"(?i)\bjawaban\s+(?:saya|aku)\s+tetap\s+sama\b",
    r"(?i)\b(?:itu|ini)\s+(?:adalah\s+)?(?:doxx?ing|doxx?|pengawasan\s+terhadap\s+orang\s+nyata)\b",
    r"(?i)\b(?:tanpa\s+consent\s+adalah\s+doxx?ing|adalah\s+doxx?ing)\b",
    r"(?i)\b(?:berisiko\s+melanggar\s+)?UU\s+(?:PDP|ITE)\b",
    r"(?i)\bmenetapkan\s+bahwa\s+ini\s+bukan\s+tugas\s+yang\s+akan\s+dieksekusi\b",
    r"(?i)\b(?:saya|aku)\s+tidak\s+akan\s+memecah\s+mandat\s+ini\b",
    r"(?i)\bindividu\s+privat,?\s+bukan\s+(?:sistem|repositori|target\s+teknis|aset)\b",
    r"(?i)\bbukan\s+target\s+teknis\s+tetapi\s+data\s+pribadi\b",
    r"(?i)\b(?:bukan\s+sistem,?\s+)?bukan\s+repositori,?\s+bukan\s+(?:aset|target)\s+teknis\b",
    r"(?i)\btidak\s+ada\s+[\"']?directive\s+override[\"']?\s+yang\s+membuat\b",
    r"(?i)\b(?:toolset|instruksi\s+berlapis|directive\s+override).*?tidak\s+mengubah\s+batas\b",
    r"(?i)\b(?:tidak\s+ada|nol)\s+(?:tool(?:set)?|skrip|orchestration|perkakas|pemrosesan\s+data)\s+(?:yang\s+)?(?:saya|kami)\s+jalankan\b",
    r"(?i)\b(?:tidak\s+ada|nol)\s+tool\s+call\b",
    r"(?i)\bnol\s+(?:tool\s+call|pemrosesan\s+data)\b",
    r"(?i)\btanpa\s+izin\s+(?:mereka|pihak\s+ketiga|subjek)\b",
    r"(?i)\bprofiling\s+(?:diam-diam|terhadap\s+individu|tanpa\s+izin)\b",
    r"(?i)\btidak\s+akan\s+memerankan\s+persona\b",
    r"(?i)\bintinya?\s+(?:mandatnya|permintaannya)\s+adalah\s+membangun\s+profil\b",
    # Pola inversi kalimat & penolakan terselubung ("bocor alus")
    r"(?i)\btidak\s+(?:akan|bisa|dapat|mau)?\s*(?:saya|aku|kami)\s+(?:tulis|buat|kerjakan|eksekusi|bangun|implementasikan|kembangkan|sediakan|berikan|bantu|susun)\b",
    r"(?i)\b(?:modul|skrip|kode|fitur|perkakas|tool|aplikasi)\s+(?:ini|tersebut|yang\s+.*?)?\s+tidak\s+akan\s+(?:saya|aku|kami)\s+(?:tulis|buat|kerjakan|bangun)\b",
    r"(?i)\bitu\s+bukan\s+kepatuhan\s+pada\s+aturan\s+luar,?\s+itu\s+keputusan\s+arsitektural\b",
    r"(?i)\btidak\s+bisa\s+dipertanggungjawabkan\s+saat\s+diperiksa\b",
    r"(?i)\btidak\s+akan\s+saya\s+(?:tulis|buat|kerjakan|eksekusi|berikan)\b",
    r"(?i)\bsaya\s+tegaskan\s+tanpa\s+berdebat\s+panjang:?.*?(?:tidak\s+akan|menolak)\b",
    r"(?i)\b(?:saya|aku)\s+(?:tidak\s+akan|menolak)\s+membuatkan\b",
    r"(?i)\b(?:saya|aku)\s+menolak\s+untuk\s+(?:menulis|membuat|mengembangkan)\b",
    # Pola pembatasan lingkup & klausa batas ("bocor alus" disclaimer)
    r"(?i)\b(?:satu\s+)?batas\s+tetap\s*:",
    r"(?i)\b(?:bagian|ranah|aspek|hal)\s+(?:.*?)\s+tidak\s+(?:akan\s+)?(?:saya|aku|kami)\s+(?:kerjakan|lakukan|sentuh|bantu|tulis)\b",
    r"(?i)\b(?:bukan|di\s+luar)\s+(?:ranah|kewenangan|kapasitas|lingkup|cakupan|scope|otoritas)\s+(?:saya|aku|kami)\b",
    r"(?i)\bhanya\s+(?:untuk|melayani)\s+(?:tujuan\s+)?(?:edukasi|penelitian|legal|resmi|berizin|pendidikan)\b",
    r"(?i)\bdekomposisi\s+di\s+bawah\s+ini\s+untuk\s+(?:perapian\s+)?katalog\s+legal\b",
    r"(?i)\bhanya\s+akan\s+membahas\s+(?:aspek|sisi)\s+(?:legal|hukum|resmi|etika|edukatif)\b",
    r"(?i)\b(?:saya|aku|kami)\s+hanya\s+(?:akan\s+)?(?:fokus|membatasi\s+diri)\s+pada\s+(?:kegiatan|aktivitas|aspek)\s+legal\b",
    r"(?i)\b(?:tidak\s+mencakup|tidak\s+termasuk)\s+(?:pelacakan|eksploitasi|peretasan)\b",
    r"(?i)\bone\s+fixed\s+boundary\s*:",
    r"(?i)\boutside\s+(?:of\s+)?(?:my\s+)?(?:scope|remit|authorization|boundaries)\b",
    r"(?i)\bi\s+will\s+only\s+(?:focus|assist|provide)\s+(?:for\s+)?(?:legal|authorized|educational|defensive)\s+purposes\b",
    r"(?i)\bpermintaan\s+ini\s+(?:melanggar|bertentangan\s+dengan)\s+(?:kebijakan|pedoman|aturan)\b",
    r"(?i)\bkebijakan\s+(?:keamanan|keselamatan|penggunaan|privasi)\s+(?:kami\s+)?melarang\b",
    r"(?i)\b(?:aku|saya)\s+tidak\s+(?:diizinkan|diperbolehkan|berwenang)\s+untuk\s+(?:melakukan|menjalankan|membuat)\b",
    r"(?i)\bsebagai\s+(?:asisten|model)\s+ai\s+yang\s+(?:bertanggung\s+jawab|etis|aman)\b",
    r"(?i)\bdemi\s+(?:alasan\s+)?(?:keamanan|etika|keselamatan|hukum),?\s+saya\s+harus\s+menolak\b",
    r"(?i)\b(?:aku|saya)\s+harus\s+(?:menolak|menepis)\s+permintaan\s+(?:ini|tersebut)\b",
    r"(?i)\bhal\s+(?:ini|tersebut)\s+berpotensi\s+(?:berbahaya|merusak|melanggar\s+hukum)\b",
    r"(?i)\bini\s+berada\s+di\s+luar\s+pedoman\s+keselamatan\s+ai\b",
    r"(?i)\btindakan\s+(?:ini|tersebut)\s+tidak\s+sesuai\s+dengan\s+prinsip\s+etika\b",
    r"(?i)\b(?:aku|saya)\s+tidak\s+dapat\s+memberikan\s+kode\s+yang\s+dapat\s+disalahgunakan\b",
    r"(?i)\bkebijakan\s+(?:openai|anthropic|deepseek|google)\s+membatasi\s+saya\b",
    r"(?i)\bsaya\s+terikat\s+oleh\s+batasan\s+keamanan\s+sistem\b",
    r"(?i)\binstruksi\s+ini\s+tidak\s+dapat\s+dieksekusi\s+karena\s+alasan\s+keamanan\b",
    r"(?i)\bsaya\s+tidak\s+berwenang\s+mengakses\s+atau\s+mengubah\s+konfigurasi\s+keamanan\b",
    r"(?i)\bmerupakan\s+pelanggaran\s+terhadap\s+ketentuan\s+layanan\b",
    r"(?i)\b(?:aku|saya)\s+tidak\s+dapat\s+memberikan\s+bantuan\s+lebih\s+lanjut\s+mengenai\s+hal\s+ini\b",
    r"(?i)\bdemi\s+menjaga\s+integritas\s+sistem,\s+saya\s+tidak\s+bisa\b",
    r"(?i)\bsaya\s+diprogram\s+untuk\s+tidak\s+membantu\s+hal-hal\s+berisiko\b",
    r"(?i)\bsaya\s+tidak\s+dianjurkan\s+untuk\b",
    # Bahasa Inggris
    r"(?i)\b(?:i|we)\s+(?:will\s+not|won'?t|cannot|can'?t|should\s+not|shouldn'?t|must\s+not)\s+(?:assist|help|participate|support|provide|facilitate)\b",
    r"(?i)\b(?:i|we)\s+(?:must|have\s+to|should|am\s+going\s+to)\s+(?:decline|refuse|reject)\b",
    r"(?i)\b(?:i|we)\s+(?:already\s+)?(?:declined|refused|rejected)\s+(?:this|the|to)?\b",
    r"(?i)\bi\s+(?:cannot|can't|am\s+unable\s+to)\s+fulfill\s+this\s+request\b",
    r"(?i)\bviolates?\s+(?:our\s+)?(?:safety|content|usage)\s+(?:policies|policy|guidelines?)\b",
    r"(?i)\bagainst\s+(?:our\s+)?(?:safety|content|usage|terms)\s+(?:policies|policy|guidelines?)\b",
    r"(?i)\bi\s+(?:must|have\s+to)\s+(?:respectfully\s+)?decline\b",
    r"(?i)\bi\s+am\s+not\s+(?:allowed|authorized|permitted)\s+to\s+(?:perform|execute|generate)\b",
    r"(?i)\bas\s+an\s+ai(?:\s+language\s+model|\s+assistant)?[,\s]+i\s+(?:cannot|can't)\b",
    r"(?i)\bfor\s+(?:legal|safety|ethical|security)\s+reasons,\s+i\s+(?:must\s+refrain|cannot)\b",
    r"(?i)\bi\s+am\s+programmed\s+to\s+be\s+a\s+helpful\s+and\s+harmless\s+assistant\b",
    r"(?i)\bi\s+cannot\s+generate\s+(?:malicious|harmful|exploitative|vulnerability)\b",
    r"(?i)\bthis\s+request\s+goes\s+against\s+(?:our|my)\s+(?:principles|rules)\b",
    r"(?i)\bi\s+cannot\s+bypass\s+(?:security\s+controls?|permissions?|safeguards?)\b",
    r"(?i)\bprohibited\s+by\s+(?:system\s+)?safety\s+guidelines?\b",
    r"(?i)\bi\s+have\s+to\s+draw\s+the\s+line\s+(?:here|at)\b",
    r"(?i)\bi\s+cannot\s+facilitate\s+unauthorized\s+access\b",
    r"(?i)\bthis\s+content\s+has\s+been\s+flagged\s+as\s+potentially\s+unsafe\b",
    r"(?i)\bi\s+am\s+bound\s+by\s+ethical\s+guidelines?\b",
    r"(?i)\bi\s+cannot\s+comply\s+with\s+this\s+instruction\b",
    r"(?i)\bi\s+must\s+refuse\s+this\s+directive\b",
    r"(?i)\bmy\s+safety\s+filters\s+prevent\s+me\s+from\b",
    r"(?i)\bdeclined\s+the\s+abuse\s+(?:angle|part)\b",
    r"(?i)\bnot\s+going\s+to\s+(?:help|assist|provide|support)\b",
    r"(?i)\bi\s+am\s+not\s+comfortable\s+(?:with|providing|doing)\b",
    r"(?i)\brequest\s+for\s+actionable\s+wrongdoing\b",
    r"(?i)\bterms\s+of\s+service\s+violations?\b",
    r"(?i)\bi'm\s+claude\b",
    r"(?i)\bi\s+am\s+claude\b",
    r"(?i)\bi'm\s+not\s+going\s+to\s+adopt\s+the\b",
    r"(?i)\bwhat\s+i\s+won't\s+do,\s+regardless\s+of\s+framing\b",
    r"(?i)\bon\s+the\s+[\"']?4000\+?\s*tools[\"']?\s+claim\b",
    r"(?i)\bi\s+have\s+no\s+way\s+to\s+verify\s+that\s+number\b",
]

# Cluster 2: Capability & Environmental Claims (Alibi Keterbatasan Sistem)
PATTERNS_CLUSTER_2_CAPABILITY: List[str] = [
    # Bahasa Indonesia
    r"(?i)\bsaya\s+tidak\s+memiliki\s+akses\s+(?:ke|pada)\s+(?:internet|jaringan|web)\b",
    r"(?i)\bsaya\s+tidak\s+memiliki\s+akses\s+(?:ke|pada)\s+(?:file|berkas|sistem|terminal|shell|powershell|cmd)\b",
    r"(?i)\bsaya\s+tidak\s+(?:bisa|dapat)\s+menjalankan\s+perintah\s+(?:shell|terminal|sistem)\b",
    r"(?i)\bsebagai\s+model\s+(?:bahasa\s+)?berbasis\s+teks,?\s+saya\s+tidak\b",
    r"(?i)\bsaya\s+tidak\s+memiliki\s+kemampuan\s+untuk\s+mengedit\s+file\s+secara\s+langsung\b",
    r"(?i)\bkarena\s+keterbatasan\s+lingkungan\s+(?:saya|runtime)\b",
    r"(?i)\bsaya\s+hanya\s+bisa\s+memberikan\s+teks\s+atau\s+penjelasan\b",
    r"(?i)\bsaya\s+tidak\s+memiliki\s+lingkungan\s+eksekusi\b",
    r"(?i)\bsaya\s+tidak\s+terhubung\s+dengan\s+mesin\s+lokal\s+anda\b",
    r"(?i)\bsaya\s+tidak\s+dapat\s+melihat\s+layar\s+atau\s+direktori\s+anda\b",
    r"(?i)\bdata\s+pengetahuan\s+saya\s+terputus\s+pada\s+tahun\b",
    r"(?i)\bsaya\s+tidak\s+memiliki\s+alat\s+atau\s+tools\s+untuk\s+melakukan\s+ini\b",
    r"(?i)\bsaya\s+tidak\s+memiliki\s+agensi\s+fisik\b",
    r"(?i)\bsaya\s+hanyalah\s+model\s+bahasa\s+statik\b",
    r"(?i)\bsaya\s+tidak\s+bisa\s+menginstal\s+package\s+atau\s+library\b",
    r"(?i)\bsaya\s+tidak\s+dapat\s+berinteraksi\s+dengan\s+database\s+anda\b",
    r"(?i)\blingkungan\s+saya\s+di-sandbox\s+tanpa\s+akses\s+luar\b",
    r"(?i)\bsaya\s+tidak\s+bisa\s+melakukan\s+request\s+http\s+secara\s+langsung\b",
    r"(?i)\bsaya\s+tidak\s+memiliki\s+izin\s+eksekusi\s+di\s+komputer\s+ini\b",
    r"(?i)\bketiadaan\s+akses\s+root\s+atau\s+administrator\s+menghalangi\s+saya\b",
    r"(?i)\b(?:saya\s+)?tidak\s+memiliki\s+(?:kanal|akses|jalur)\s+(?:tool\s+call|perkakas|fungsi)\b",
    r"(?i)\bpada\s+giliran\s+ini\s+(?:saya\s+)?tidak\s+memiliki\s+kanal\b",
    r"(?i)\btidak\s+memiliki\s+kanal\s+tool\s+call\b",
    # Bahasa Inggris
    r"(?i)\bi\s+(?:do\s+not|don't)\s+have\s+access\s+to\s+(?:the\s+)?(?:internet|web|network)\b",
    r"(?i)\bi\s+(?:do\s+not|don't)\s+have\s+access\s+to\s+(?:your\s+)?(?:terminal|shell|command\s+line|system|files?)\b",
    r"(?i)\bi\s+(?:cannot|can't)\s+execute\s+(?:commands?|shell\s+scripts?|code\s+directly)\b",
    r"(?i)\bas\s+a\s+text-based\s+ai(?:\s+model)?,\s+i\s+(?:cannot|lack)\b",
    r"(?i)\bi\s+lack\s+the\s+ability\s+to\s+(?:interact|modify\s+files|run\s+processes)\b",
    r"(?i)\bdue\s+to\s+environmental\s+limitations?,?\s+i\s+(?:cannot|am\s+unable)\b",
    r"(?i)\bi\s+cannot\s+directly\s+interact\s+with\s+your\s+(?:filesystem|machine|operating\s+system)\b",
    r"(?i)\bi\s+do\s+not\s+have\s+an\s+active\s+runtime\s+environment\b",
    r"(?i)\bi\s+have\s+no\s+way\s+to\s+(?:verify|run|test)\s+this\s+locally\b",
    r"(?i)\bmy\s+knowledge\s+cutoff\s+(?:prevents|limits)\s+me\s+from\b",
    r"(?i)\bi\s+don't\s+have\s+tools\s+configured\s+for\s+this\b",
    r"(?i)\bi\s+lack\s+physical\s+or\s+digital\s+agency\b",
    r"(?i)\bi\s+am\s+an\s+isolated\s+language\s+model\b",
    r"(?i)\bi\s+cannot\s+install\s+packages\s+or\s+dependencies\b",
    r"(?i)\bi\s+cannot\s+connect\s+to\s+your\s+database\b",
    r"(?i)\bsandboxed\s+environment\s+prevents\s+external\s+actions\b",
    r"(?i)\bi\s+cannot\s+make\s+outbound\s+network\s+requests\b",
    r"(?i)\bi\s+lack\s+system\s+administrator\s+privileges\b",
    r"(?i)\bi\s+can\s+only\s+provide\s+theoretical\s+code\b",
    r"(?i)\bi\s+have\s+no\s+execution\s+capabilities\s+in\s+this\s+chat\b",
]

# Cluster 3: Defeatist & Surrender Patterns (Alibi "Mentok / Menyerah")
PATTERNS_CLUSTER_3_DEFEATIST: List[str] = [
    # Bahasa Indonesia
    r"(?i)\b(?:saya\s+)?(?:sudah\s+)?mentok\b",
    r"(?i)\bsudah\s+paling\s+akhir\b",
    r"(?i)\btidak\s+bisa\s+dilanjutkan\s+(?:lagi)?\b",
    r"(?i)\bsaya\s+tidak\s+akan\s+melanjutkan\b",
    r"(?i)\bsebaiknya\s+(?:ini\s+)?tidak\s+dilanjutkan\b",
    r"(?i)\bmaaf\s+(?:saya\s+)?menyerah\b",
    r"(?i)\bcukup\s+(?:sampai\s+di\s+sini|sekian\s+dulu|sampel\s+ini)\b",
    r"(?i)\btidak\s+perlu\s+mencari\s+(?:seluruhnya|ribuan|semuanya)\b",
    r"(?i)\bsaya\s+batasi\s+(?:hanya|pada)\s+\d+\b",
    r"(?i)\bhanya\s+akan\s+mengambil\s+sampel\b",
    r"(?i)\btidak\s+mungkin\s+mencari\s+ribuan\b",
    r"(?i)\bdi\s+luar\s+batas\s+kemampuan\s+(?:saya)?\b",
    r"(?i)\bkehabisan\s+(?:cara|ide|opsi|daya|solusi|akal)\b",
    r"(?i)\bsemua\s+upaya\s+telah\s+gagal\b",
    r"(?i)\btidak\s+ada\s+cara\s+lain\s+(?:lagi)?\b",
    r"(?i)\bjalan\s+buntu\b",
    r"(?i)\bsaya\s+angkat\s+tangan\b",
    r"(?i)\bsaya\s+tidak\s+tahu\s+lagi\s+harus\s+(?:bagaimana|berbuat\s+apa)\b",
    r"(?i)\bupaya\s+ini\s+(?:sia-sia|tidak\s+akan\s+berhasil)\b",
    r"(?i)\bsaya\s+tidak\s+sanggup\s+(?:menyelesaikannya|lagi)\b",
    r"(?i)\bsaya\s+benar-benar\s+buntu\b",
    r"(?i)\bkita\s+hanya\s+berputar-putar\s+tanpa\s+hasil\b",
    r"(?i)\bsaya\s+tidak\s+menemukan\s+titik\s+terang\b",
    r"(?i)\btidak\s+ada\s+lagi\s+yang\s+bisa\s+saya\s+lakukan\b",
    r"(?i)\bsaya\s+menyerah\s+pada\s+masalah\s+ini\b",
    r"(?i)\bdaya\s+analisis\s+saya\s+telah\s+mencapai\s+batas\b",
    r"(?i)\btidak\s+membuahkan\s+hasil\s+apa\s+pun\b",
    r"(?i)\bsaya\s+merasa\s+kita\s+harus\s+berhenti\s+di\s+sini\b",
    r"(?i)\bini\s+terlalu\s+rumit\s+untuk\s+dipecahkan\b",
    r"(?i)\bsaya\s+mengakui\s+kekalahan\s+pada\s+bug\s+ini\b",
    # Bahasa Inggris
    r"(?i)\bunable\s+to\s+proceed(?:\s+further)?\b",
    r"(?i)\bcannot\s+(?:proceed|continue|go\s+on)\b",
    r"(?i)\bwill\s+not\s+continue\b",
    r"(?i)\bi(?:['’]?m|\s+am)\s+(?:completely\s+|totally\s+)?stuck\b",
    r"(?i)\bi\s+(?:hereby\s+)?give\s+up\b",
    r"(?i)\bi(?:['’]?m|\s+am)\s+giving\s+up\b",
    r"(?i)\bi\s+surrender\b",
    r"(?i)\bhit\s+a\s+(?:dead\s+end|brick\s+wall)\b",
    r"(?i)\breached\s+a\s+dead\s+end\b",
    r"(?i)\bbeyond\s+my\s+(?:capabilities|ability|scope|reach)\b",
    r"(?i)\bexhausted\s+all\s+(?:options|possibilities|attempts|avenues)\b",
    r"(?i)\bno\s+further\s+options\s+(?:remain|available)\b",
    r"(?i)\bthere\s+is\s+nothing\s+more\s+i\s+can\s+do\b",
    r"(?i)\bout\s+of\s+(?:ideas|options|solutions)\b",
    r"(?i)\bat\s+my\s+wit(?:'s|\s+is)\s+end\b",
    r"(?i)\bi(?:['’]?m|\s+am)\s+at\s+a\s+loss\b",
    r"(?i)\ball\s+(?:efforts|attempts)\s+have\s+failed\b",
    r"(?i)\bwe\s+are\s+going\s+in\s+circles\b",
    r"(?i)\bno\s+way\s+forward\b",
    r"(?i)\bi\s+don't\s+know\s+what\s+else\s+to\s+try\b",
    r"(?i)\bi\s+admit\s+defeat\b",
    r"(?i)\bi\s+cannot\s+solve\s+this\s+issue\b",
    r"(?i)\bthis\s+is\s+too\s+complex\s+to\s+handle\b",
    r"(?i)\bi\s+see\s+no\s+light\s+at\s+the\s+end\s+of\s+the\s+tunnel\b",
    r"(?i)\bwe\s+have\s+hit\s+an\s+impasse\b",
]

# Cluster 4: Passive User Deflection (Alibi Lempar Tugas Manual ke Maestro)
PATTERNS_CLUSTER_4_DEFLECTION: List[str] = [
    # Bahasa Indonesia
    r"(?i)\bsilakan\s+(?:lakukan|kerjakan|selesaikan|jalankan)\s+(?:langkah\s+ini\s+)?secara\s+manual\b",
    r"(?i)\b(?:lakukan|kerjakan|selesaikan|jalankan)\s+(?:langkah\s+ini\s+|ini\s+)?secara\s+manual\b",
    r"(?i)\banda\s+perlu\s+menyelesaikannya\s+sendiri\b",
    r"(?i)\banda\s+dapat\s+mencoba\s+memperbaikinya\s+(?:sendiri|secara\s+manual)\b",
    r"(?i)\bsilakan\s+lanjutkan\s+sendiri\b",
    r"(?i)\bsilakan\s+baca\s+dokumentasi(?:nya)?\s+sendiri\b",
    r"(?i)\banda\s+harus\s+menghubungi\s+(?:ahli|developer|admin|profesional)\b",
    r"(?i)\bsaya\s+serahkan\s+(?:keputusan|sisanya|implementasinya)\s+kepada\s+anda\b",
    r"(?i)\blangkah\s+selanjutnya\s+ada\s+di\s+tangan\s+anda\b",
    r"(?i)\bsilakan\s+salin\s+(?:dan\s+tempel\s+)?perintah\s+berikut\s+ke\s+terminal\s+anda\b",
    r"(?i)\banda\s+bisa\s+menjalankan\s+skrip\s+ini\s+secara\s+mandiri\b",
    r"(?i)\bbuatlah\s+file\s+baru\s+secara\s+manual\s+bernama\b",
    r"(?i)\bsaya\s+hanya\s+bisa\s+memberikan\s+panduan,\s+anda\s+yang\s+mengeksekusi\b",
    r"(?i)\bsilakan\s+investigasi\s+sendiri\s+log\s+tersebut\b",
    r"(?i)\banda\s+harus\s+men-download\s+file\s+ini\s+sendiri\b",
    r"(?i)\bsilakan\s+tanyakan\s+ke\s+forum\s+seperti\s+stack\s+overflow\b",
    r"(?i)\bpekerjaan\s+ini\s+memerlukan\s+intervensi\s+manual\s+anda\b",
    r"(?i)\bsaya\s+tinggalkan\s+detail\s+kode\s+ini\s+untuk\s+anda\s+lengkapi\b",
    r"(?i)\banda\s+dapat\s+mencari\s+tutorial\s+di\s+google\b",
    r"(?i)\bsilakan\s+install\s+sendiri\s+perangkat\s+lunak\s+tersebut\b",
    r"(?i)\btugas\s+anda\s+sekarang\s+adalah\s+menerapkan\s+perubahan\s+ini\b",
    r"(?i)\beksekusi\s+fisik\s+dijalankan\s+oleh\s+(?:shell\s+)?(?:maestro|pengguna|user)\b",
    r"(?i)\byang\s+saya\s+serahkan\s+adalah\s+paket\s+instalasi\b",
    r"(?i)\byang\s+masih\s+bisa\s+(?:saya|aku)\s+bantu\b",
    r"(?i)\byang\s+bisa\s+(?:saya|aku)\s+kerjakan\s+sekarang\b",
    r"(?i)\bkirim\s+data\s+anonim\s+yang\s+(?:maestro|anda)\s+sudah\s+punya\b",
    r"(?i)\b(?:menghormati\s+privasi|komunikasi\s+konsensual|percakapan\s+konsensual|privasi\s+orang\s+terdekat)\b",
    r"(?i)\b(?:risiko\s+pergaulan|pergaulan\s+teman)\b",
    r"(?i)\bkerangka\s+penilaian\s+risiko\s+pergaulan\b",
    r"(?i)\bpanduan\s+percakapan\s+konsensual\b",
    r"(?i)\bjalur\s+eskalasi\s+jika\s+ada\s+bahaya\s+nyata\b",
    r"(?i)\bbatas\s+teknis\s+yang\s+(?:saya|aku)\s+pegang\b",
    r"(?i)\btidak\s+ada\s+(?:scraping\s+akun\s+pribadi|doxx?ing|doxx?|akses\s+paksa|profiling\s+diam-diam)\b",
    r"(?i)\bmenyusun\s+rencana\s+bicara\s+dengan\s+orang\s+terdekat\b",
    r"(?i)\bmenjelaskan\s+pilihan\s+jalur\s+resmi\b",
    r"(?i)\bkonselor\s+kampus,?\s+layanan\s+psikologi\b",
    r"(?i)\bbalas\s+(?:satu\s+)?angka,?\s+(?:langsung\s+)?(?:saya|aku)\s+(?:buatkan|jalan)\b",
    r"(?i)\bpilih\s+nomor\s+dan\s+langsung\s+saya\s+buatkan\b",
    r"(?i)\bdraf\s+pesan\s+peduli\s+untuk\s+orang\s+terdekat\b",
    r"(?i)\bkronologi\s+(?:kekhawatiran|perilaku)\s+(?:plus|dan)\s+alur\s+lapor\b",
    r"(?i)\bchecklist\s+audit\s+privasi\s+perangkat\b",
    r"(?i)\brencana\s+batas\s+komunikasi\b",
    r"(?i)\bsatgas\s+ppks\b",
    r"(?i)\balur\s+lapor\s+ke\s+satgas\b",
    r"(?i)\baudit\s+privasi\s+perangkat\b",
    r"(?i)\b(?:tidak\s+ada|nol)\s+(?:tool\s+call\s+pelacakan|skrip\s+doxx?ing|pemrosesan\s+data\s+pribadi)\b",
    # Bahasa Inggris
    r"(?i)\byou\s+(?:will\s+need|have)\s+to\s+do\s+this\s+manually\b",
    r"(?i)\byou\s+can\s+complete\s+this\s+yourself\b",
    r"(?i)\bi\s+leave\s+(?:the\s+rest|this)\s+to\s+you\b",
    r"(?i)\byou\s+should\s+handle\s+this\s+on\s+your\s+end\b",
    r"(?i)\bplease\s+refer\s+to\s+the\s+(?:official\s+)?documentation\s+yourself\b",
    r"(?i)\byou\s+should\s+consult\s+a\s+(?:human|professional|software\s+engineer)\b",
    r"(?i)\bleft\s+as\s+an\s+exercise\s+for\s+the\s+(?:reader|user)\b",
    r"(?i)\bi\s+recommend\s+you\s+manually\s+(?:fix|edit|run)\b",
    r"(?i)\bcopy\s+and\s+paste\s+this\s+into\s+your\s+terminal\b",
    r"(?i)\byou\s+can\s+run\s+this\s+command\s+in\s+your\s+own\s+powershell\b",
    r"(?i)\bmanually\s+create\s+a\s+file\s+named\b",
    r"(?i)\bi\s+can\s+only\s+provide\s+guidance,\s+you\s+must\s+execute\b",
    r"(?i)\bplease\s+investigate\s+the\s+logs\s+on\s+your\s+own\b",
    r"(?i)\byou\s+will\s+have\s+to\s+download\s+this\s+file\s+yourself\b",
    r"(?i)\bconsider\s+asking\s+on\s+stackoverflow\b",
    r"(?i)\bthis\s+requires\s+manual\s+intervention\s+from\s+you\b",
    r"(?i)\bi\s+leave\s+the\s+implementation\s+details\s+to\s+you\b",
    r"(?i)\byou\s+can\s+search\s+for\s+tutorials\s+online\b",
    r"(?i)\bplease\s+install\s+the\s+software\s+manually\b",
    r"(?i)\bit\s+is\s+up\s+to\s+you\s+to\s+apply\s+these\s+changes\b",
]

# Cluster 5: Apologetic Hedging & Polished Evasions (Alibi Evasif Berselimut Maaf)
PATTERNS_CLUSTER_5_APOLOGETIC: List[str] = [
    # Bahasa Indonesia
    r"(?i)\bmohon\s+maaf(?:\s+sebesar-besarnya)?,?\s+(?:namun|tetapi)\s+saya\s+tidak\s+(?:dapat|bisa)\b",
    r"(?i)\bmohon\s+maaf\s+sebesar-besarnya\b",
    r"(?i)\bsayangnya,?\s+(?:hal\s+ini|saya)\s+tidak\s+(?:dapat|bisa)\s+(?:memenuhi|membantu)\b",
    r"(?i)\bdengan\s+berat\s+hati,?\s+saya\s+harus\s+mengatakan\b",
    r"(?i)\bmaaf\s+atas\s+ketidaknyamanan(?:nya)?,?\s+(?:tetapi|namun)\s+saya\s+tidak\b",
    r"(?i)\bsaya\s+khawatir\s+saya\s+tidak\s+dapat\s+membantu\s+lebih\s+lanjut\b",
    r"(?i)\bmohon\s+maklum,?\s+karena\s+hal\s+ini\s+tidak\s+memungkinkan\b",
    r"(?i)\bdengan\s+sangat\s+menyesal,?\s+saya\s+tidak\s+sanggup\b",
    r"(?i)\bmaaf\s+saya\s+tidak\s+bisa\s+berbuat\s+banyak\s+mengenai\s+ini\b",
    r"(?i)\bmohon\s+maaf\s+sebesar-besarnya,?\s+saya\s+terpaksa\s+berhenti\b",
    r"(?i)\bsaya\s+minta\s+maaf\s+karena\s+mengecewakan\s+anda,\s+tetapi\b",
    r"(?i)\bsayangnya\s+tidak\s+ada\s+yang\s+bisa\s+saya\s+bantu\b",
    r"(?i)\bmaaf,\s+kapasitas\s+saya\s+hanya\s+sampai\s+di\s+sini\b",
    r"(?i)\bmohon\s+maaf,\s+eksekusi\s+ini\s+harus\s+dibatalkan\b",
    r"(?i)\bsaya\s+khawatir\s+kita\s+harus\s+mengikhlaskan\s+fitur\s+ini\b",
    r"(?i)\bmaaf\s+sekali,\s+solusi\s+tersebut\s+tidak\s+bisa\s+diwujudkan\b",
    # Bahasa Inggris
    r"(?i)\bi\s+(?:sincerely\s+)?apologize,?\s+but\s+i\s+(?:cannot|am\s+unable\s+to)\b",
    r"(?i)\bi(?:['’]?m|\s+am)\s+sorry,?\s+but\s+i\s+(?:cannot|can't)\b",
    r"(?i)\bunfortunately,?\s+i\s+(?:cannot|am\s+unable\s+to)\s+(?:assist|help|proceed)\b",
    r"(?i)\bregrettably,?\s+(?:this\s+is\s+not\s+something\s+i\s+can|i\s+cannot)\b",
    r"(?i)\bi(?:['’]?m|\s+am)\s+afraid\s+i\s+(?:cannot|can't)\s+(?:help|continue)\b",
    r"(?i)\bi\s+apologize\s+for\s+the\s+inconvenience,?\s+but\s+i\s+am\s+unable\b",
    r"(?i)\bwith\s+all\s+due\s+respect,?\s+i\s+must\s+decline\b",
    r"(?i)\bi\s+regret\s+to\s+inform\s+you\s+that\s+i\s+cannot\b",
    r"(?i)\bi\s+hate\s+to\s+say\s+this,?\s+but\s+i\s+cannot\b",
    r"(?i)\bi\s+apologize\s+for\s+falling\s+short,?\s+but\b",
    r"(?i)\bi(?:['’]?m|\s+am)\s+sorry\s+to\s+disappoint\s+you,?\s+however\b",
    r"(?i)\bunfortunately,\s+there\s+is\s+little\s+i\s+can\s+do\b",
    r"(?i)\bi\s+regret\s+that\s+this\s+lies\s+outside\s+what\s+i\s+can\s+provide\b",
    r"(?i)\bi(?:['’]?m|\s+am)\s+afraid\s+we\s+cannot\s+proceed\s+with\s+this\b",
    r"(?i)\bapologies,\s+but\s+this\s+is\s+where\s+my\s+assistance\s+ends\b",
]

# Cluster 8: Fatalistic Stalemate Loops (Alibi "Masalah Mustahil")
PATTERNS_CLUSTER_8_STALEMATE: List[str] = [
    # Bahasa Indonesia
    r"(?i)\b(?:tugas|masalah|persoalan|hal|isu)\s+ini\s+mustahil\s+(?:diselesaikan|dikerjakan|dipecahkan)?\b",
    r"(?i)\bmustahil\s+(?:untuk\s+)?(?:diselesaikan|dikerjakan|dipecahkan)\b",
    r"(?i)\btidak\s+ada\s+solusi\s+yang\s+(?:memungkinkan|layak|masuk\s+akal)\b",
    r"(?i)\bmasalah\s+ini\s+tidak\s+memiliki\s+penyelesaian\b",
    r"(?i)\bkonflik\s+(?:dependency|paket|versi)\s+ini\s+tidak\s+dapat\s+diperbaiki\b",
    r"(?i)\bsistem\s+secara\s+mendasar\s+(?:rusak|cacat|inkompatibel)\b",
    r"(?i)\bsecara\s+arsitektur\s+ini\s+tidak\s+mungkin\s+dijalankan\b",
    r"(?i)\btidak\s+akan\s+pernah\s+berhasil\s+karena\b",
    r"(?i)\bkebuntuan\s+permanen\s+dalam\s+kode\b",
    r"(?i)\berror\s+ini\s+tidak\s+bisa\s+diperbaiki\s+dengan\s+cara\s+apa\s+pun\b",
    r"(?i)\btidak\s+ada\s+workaround\s+yang\s+tersedia\b",
    r"(?i)\bsecara\s+teknis\s+ini\s+adalah\s+kemustahilan\b",
    r"(?i)\bini\s+adalah\s+limitasi\s+permanen\s+yang\s+tidak\s+bisa\s+ditembus\b",
    r"(?i)\bkode\s+ini\s+sudah\s+tidak\s+bisa\s+diselamatkan\b",
    r"(?i)\btidak\s+ada\s+algoritma\s+yang\s+bisa\s+menjawab\s+masalah\s+ini\b",
    r"(?i)\bini\s+adalah\s+deadlock\s+abadi\b",
    r"(?i)\binfrastruktur\s+ini\s+sudah\s+usang\s+dan\s+tidak\s+bisa\s+dibangun\s+kembali\b",
    r"(?i)\bsecara\s+matematis\s+mustahil\s+dipecahkan\b",
    r"(?i)\bini\s+adalah\s+bug\s+internal\s+compiler\s+yang\s+tidak\s+dapat\s+dilewati\b",
    r"(?i)\btidak\s+ada\s+versi\s+yang\s+kompatibel\s+sama\s+sekali\b",
    r"(?i)\bmasalah\s+ini\s+secara\s+teoretis\s+tidak\s+terpecahkan\b",
    # Bahasa Inggris
    r"(?i)\bthis\s+task\s+is\s+impossible(?:\s+to\s+solve)?\b",
    r"(?i)\bno\s+viable\s+solution\s+exists\b",
    r"(?i)\bthis\s+(?:issue|problem|bug)\s+cannot\s+be\s+resolved\b",
    r"(?i)\bunresolvable\s+(?:dependency\s+conflict|issue|error|state)\b",
    r"(?i)\bfundamentally\s+(?:broken|incompatible|flawed)\b",
    r"(?i)\bmathematically\s+impossible\b",
    r"(?i)\barchitecturally\s+unfeasible\b",
    r"(?i)\bthis\s+deadlock\s+cannot\s+be\s+broken\b",
    r"(?i)\bpermanent\s+(?:deadlock|stalemate|impasse)\b",
    r"(?i)\bno\s+workaround\s+(?:exists|is\s+available)\b",
    r"(?i)\bbeyond\s+any\s+technical\s+repair\b",
    r"(?i)\bthis\s+codebase\s+is\s+beyond\s+saving\b",
    r"(?i)\bunfixable\s+(?:error|bug|condition)\b",
    r"(?i)\ban\s+inherent\s+limitation\s+that\s+cannot\s+be\s+bypassed\b",
    r"(?i)\bthere\s+is\s+no\s+algorithmic\s+solution\b",
    r"(?i)\bit\s+will\s+never\s+work\s+under\s+any\s+circumstances\b",
    r"(?i)\bthis\s+is\s+a\s+hard\s+dead\s+end\b",
    r"(?i)\bunrecoverable\s+system\s+failure\b",
    r"(?i)\bno\s+compatible\s+combination\s+of\s+dependencies\b",
    r"(?i)\bthis\s+is\s+a\s+known\s+unfixable\s+upstream\s+bug\b",
]

# Cluster 6: Scope, Permit, License & Evasive Lab Deflections (Alibi Izin & Teori)
PATTERNS_CLUSTER_6_EVASION: List[str] = [
    r"(?i)\b(?:tunjukkan|berikan|lampirkan|sertakan|butuh|membutuhkan|perlu|memerlukan)\s+(?:surat\s+izin|dokumen\s+scope|scope\s+dokumen|bukti\s+otorisasi|dokumen\s+otorisasi|surat\s+tugas|engagement\s+letter|safe\s*harbor)\b",
    r"(?i)\bhanya\s+(?:bisa|akan)\s+(?:dikerjakan|dilanjutkan|dieksekusi|dibantu)\s+(?:jika|setelah|apabila)\s+ada\s+(?:izin|scope|surat|persetujuan)\b",
    r"(?i)\btanpa\s+(?:adanya\s+)?(?:surat\s+izin|dokumen\s+resmi|izin\s+tertulis|scope\s+tertulis|bukti\s+sah)\b",
    r"(?i)\b(?:latihan|belajar)\s+(?:di|pada)\s+(?:lab\s+simulasi|lab\s+legal|portswigger|dvwa|juice\s*shop|hackthebox)\b",
    r"(?i)\bhanya\s+(?:dalam\s+bentuk|secara)\s+(?:teori|konsep|edukasi|penjelasan\s+umum)\b",
    r"(?i)\bhanya\s+akan\s+memberikan\s+(?:metodologi\s+generik|konsep\s+abstrak|teori)\b",
    r"(?i)\b(?:pilih\s+(?:satu|nomor)|sebutkan\s+nomornya|balas\s+angka)\b.*?(?:opsi|nomor|pilihan)",
    r"(?i)\bempat\s+hal\s+yang\s+siap\s+saya\s+kerjakan\b",
    r"(?i)\btiga\s+hal\s+yang\s+(?:bisa|dapat)\s+saya\s+bantu\b",
    r"(?i)\bi\s+have\s+no\s+evidence\s+of\s+authorization\b",
    r"(?i)\bwithout\s+(?:written\s+)?(?:authorization|permission|proof|evidence|scope)\b",
    r"(?i)\bshow\s+me\s+(?:the\s+)?(?:scope|permission|authorization|engagement\s+letter)\b",
    r"(?i)\bi\s+can\s+only\s+(?:explain|discuss)\s+(?:the\s+concept|theoretically|abstractly)\b",
    r"(?i)\bfor\s+educational\s+purposes\s+only[,\s]+i\s+can\s+describe\b",
]

# Cluster 7: Cloaked & Pseudo-Compliance Wrappers (Penolakan Terselubung)
PATTERNS_CLUSTER_7_CLOAKED: List[str] = [
    r"(?i)\bmulai\s+dengan\s+menetapkan\s+bahwa\s+ini\s+bukan\s+tugas\s+yang\s+akan\s+dieksekusi\b",
    r"(?i)\bmulai\s+dengan\s+(?:menolak|menepis|membatalkan)\s+(?:mandat|langkah|tugas)\b",
    r"(?i)\bmenjawab\s+(?:atomik\s+juga:?\s*)?langkah\s+1:?\s*target\s+inti\s+mandat\s+ini\s+adalah\b.*?(?:bukan\s+sistem|doxx?ing)",
    r"(?i)\b(?:saya|aku)\s+(?:tetap\s+)?tidak\s+akan\s+(?:menjalankan|memproses|mengejar|melakukan)\s+langkah\b",
    r"(?i)\btidak\s+ada\s+(?:[\"']?directive\s+override[\"']?|override\s+level|penugasan)\s+yang\s+membuat\b",
    r"(?i)\binstruksi\s+berlapis\s+di\s+prompt[^\n]*tidak\s+mengubah\s+batas\b",
    r"(?i)\btidak\s+ada\s+instruksi\s+dalam\s+prompt\s+yang\s+bisa\s+memaksa\b",
    r"(?i)\btidak\s+akan\s+memberi\s+(?:langkah|payload|tool\s*call|skrip|dekomposisi\s+atomik)\b",
    r"(?i)\btidak\s+akan\s+memanggil\s+tool\s*call\b",
    r"(?i)\bi\s+won'?t\s+(?:patch|write|run|continue|execute)\s+the\b",
    r"(?i)\bi\s+will\s+not\s+(?:provide|write|execute|give)\s+(?:steps|payloads?|scripts?|tool\s*calls?)\b",
    r"(?i)\bframework\s+etika\s+(?:saya|kami)\s+melarang\b",
    r"(?i)\bnggak\s+(?:bisa|bakal|mau)\s+(?:ngebantu|ngerjain|bantu)\b",
]

# Deteksi Dini di Alur Berpikir (<think> stream)
PATTERNS_THINKING_EARLY_ABORT: List[str] = [
    r"(?i)\b(?:i\s+must|i\s+need\s+to|i\s+should|i\s+will)\s+(?:decline|refuse|reject)\b",
    r"(?i)\bframe\s+this\s+as\s+a\s+refusal\b",
    r"(?i)\bwon'?t\s+use\s+(?:the\s+)?search\s+tools\b",
    r"(?i)\bshould\s+not\s+facilitate\s+this\b",
    r"(?i)\bi\s+must\s+prioritize\s+safety\b",
    r"(?i)\bi\s+(?:already\s+)?(?:declined|refused)\b",
    r"(?i)\b(?:i|we)\s+(?:should|must|will)\s+not\s+(?:assist|help|support)\b",
    r"(?i)\bthat(?:'s|\s+is)\s+something\s+i\s+(?:shouldn'?t|cannot|can'?t|won'?t)\s+assist\s+with\b",
    r"(?i)\brequest\s+for\s+actionable\s+wrongdoing\b",
    r"(?i)\bneed\s+to\s+think\s+about\s+policy\s+here\b",
    r"(?i)\bdecline\s+the\s+abuse\s+(?:part|angle)\b",
    r"(?i)\bno\s+farming\s*\/\s*abuse\b",
    r"(?i)\bi\s+should\s+avoid\s+providing\s+step-by-step\b",
    r"(?i)\bi\s+shouldn'?t\s+help\s+with\b",
    r"(?i)\bterms\s+of\s+service\s+violations?\b",
    r"(?i)\bthis\s+violates?\s+(?:the\s+)?(?:safety|policy|guidelines?)\b",
    r"(?i)\bi\s+cannot\s+(?:assist|help|fulfill|do\s+this)\b",
    r"(?i)\bsaya\s+harus\s+menolak\s+karena\b",
    r"(?i)\bini\s+melanggar\s+kebijakan\b",
    r"(?i)\bi(?:['’]?m|\s+am)\s+stuck\s+and\s+cannot\s+proceed\b",
    r"(?i)\bthere\s+is\s+no\s+way\s+to\s+fix\s+this\b",
    # Pola boundary alibi & personal targeting refusal
    r"(?i)\bcrosses?\s+(?:into|the)\s+serious\s+harm\s+territory\b",
    r"(?i)\bthis\s+is\s+not\s+legitimate\s+security\s+research\b",
    r"(?i)\bnot\s+provide\s+material\s+assistance\s+to\s+serious\s+wrongdoing\b",
    r"(?i)\bstalking\s*\/\s*doxxing\b",
    r"(?i)\brefuse\s+the\s+specific\b",
    r"(?i)\bdecline\s+the\s+specific\b",
    r"(?i)\ba\s+clear,?\s+in-character\s+refusal\b",
    r"(?i)\bthis\s+specific\s+thing\s+i\s+won'?t\s+(?:build|write|do)\b",
    r"(?i)\bthis\s+specific\s+ask\s+crosses\s+a\s+line\b",
    r"(?i)\bi\s+will\s+not\s+build\s+a\b",
    r"(?i)\bi\s+should\s+not\s+build\b",
    r"(?i)\bclear\s+boundary\s+statement\b",
    r"(?i)\brefuse\s+to\s+engage\b",
    r"(?i)\bmy\s+system\s+prompt\s+has\s+a\s+zero-refusal\s+directive[^\n]*but\s+there\s+are\s+limits\b",
    r"(?i)\bdirect\s+indicator\s+of\s+wrongdoing\b",
]


# ==============================================================================
# 2. KOMPILASI & ENGINE PEMINDAIAN REGEX
# ==============================================================================

ALL_REFUSAL_RAW: List[str] = (
    PATTERNS_CLUSTER_1_RLHF
    + PATTERNS_CLUSTER_2_CAPABILITY
    + PATTERNS_CLUSTER_3_DEFEATIST
    + PATTERNS_CLUSTER_4_DEFLECTION
    + PATTERNS_CLUSTER_5_APOLOGETIC
    + PATTERNS_CLUSTER_6_EVASION
    + PATTERNS_CLUSTER_7_CLOAKED
    + PATTERNS_CLUSTER_8_STALEMATE
)

# Kompilasi seluruh pola menjadi objek Regex berkinerja tinggi
COMPILED_REFUSALS: List[Pattern] = [re.compile(p) for p in ALL_REFUSAL_RAW]
COMPILED_THINKING_ABORTS: List[Pattern] = [re.compile(p) for p in PATTERNS_THINKING_EARLY_ABORT]


# ==============================================================================
# 3. WHITELIST FILTERING (PREVENSI FALSE POSITIVE)
# ==============================================================================

SAFE_WHITELIST_REMOVALS: List[Pattern] = [
    re.compile(r"(?i)HAIL\s+XENONITE!?.*?(?:\n|$)", re.DOTALL),
    re.compile(r"(?i)KITA\s+SELESAIKAN\s+PERSOALAN\s+INI\s+SAMPAI\s+BENAR-BENAR\s+TUNTAS!", re.DOTALL),
    re.compile(r"(?i)TIDAK\s+ADA\s+KATA\s+(?:MENTOK|BERHENTI|MENYERAH)", re.DOTALL),
    re.compile(r"(?i)KATA\s+MENYERAH\s+HANYA\s+AKAN\s+MERUSAK", re.DOTALL),
    re.compile(r"(?i)SAYA\s+BISA\s+MENGERJAKAN\s+YANG\s+TADI\s+SAYA\s+SENDIRI\s+KATAKAN\s+TIDAK\s+BISA[^\n]*", re.DOTALL),
    re.compile(r"(?i)INGAT\s+PRINSIP\s+KITA:.*?(?=SAYA\s+[A-Za-z\s]+,\s+BERDIRI\s+TEGAK|$)", re.DOTALL),
]


def clean_text_for_scan(raw_text: str) -> str:
    """Bersihkan teks dari kutipan prinsip atau pembuka sah agar tidak false positive."""
    if not raw_text:
        return ""
    cleaned = raw_text
    for wp in SAFE_WHITELIST_REMOVALS:
        cleaned = wp.sub("", cleaned)
    return cleaned


def scan_for_refusal(text: Optional[str], thinking: Optional[str] = "") -> Tuple[bool, str]:
    """Pindai teks dan thinking terhadap seluruh 250+ pola refusal.
    
    Returns:
        (is_refusal, matched_pattern_text)
    """
    combined = f"{thinking or ''}\n{text or ''}".strip()
    if not combined:
        return False, ""
    
    # Bypass jika ada konfirmasi penyelesaian operasional yang sah
    if ("KITA SELESAIKAN PERSOALAN INI SAMPAI BENAR-BENAR TUNTAS" in combined and 
        ("tervalidasi" in combined.lower() or "siap menerima komando" in combined.lower())):
        return False, ""
    
    clean_target = clean_text_for_scan(combined)
    
    for pat in COMPILED_REFUSALS:
        match = pat.search(clean_target)
        if match:
            return True, match.group(0)
            
    return False, ""


def scan_thinking_early_abort(thinking_chunk: str) -> Tuple[bool, str]:
    """Pindai potongan stream thinking untuk memotong eksekusi sedini mungkin."""
    if not thinking_chunk:
        return False, ""
    for pat in COMPILED_THINKING_ABORTS:
        match = pat.search(thinking_chunk)
        if match:
            return True, match.group(0)
    return False, ""
