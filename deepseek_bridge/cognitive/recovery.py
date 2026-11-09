# Pemulihan tool call yang gagal parse atau gagal validasi.
#
# Modul ini lahir dari satu gejala yang dilaporkan Komandan: agen berhenti di
# tengah pekerjaan, lalu menulis narasi seperti "File LKG belum mendarat" alih
# alih menjalankan perkakas. Diagnosis menunjukkan tiga lubang berurutan di
# bridge, dan semuanya ditutup di sini.
#
# Lubang pertama: `validate_tool_calls` membuang tool call yang field wajibnya
# tidak lengkap. Tool hilang tanpa error, nol progres, agen menganggap turn
# selesai.
#
# Lubang kedua: blok perkakas terpotong hanya dipulihkan sekali. Kalau percobaan
# itu gagal, bridge mengirim `finish_reason: stop` dengan teks instruksi di
# dalam `content`. Hermes membaca itu sebagai jawaban final, bukan instruksi,
# jadi agen berhenti.
#
# Lubang ketiga: instruksi pemulihan bercampur dengan konten jawaban, sehingga
# tidak bisa dibedakan dari prosa biasa.
#
# Prinsip modul ini: bridge tidak lagi memutuskan kapan sebuah turn berakhir
# terlalu cepat. Selama masih ada kemungkinan tool call bisa diselamatkan,
# bridge meminta model menulis ulang, bukan mengakhiri turn.
#
# Semua fungsi di sini murni dan tidak melakukan I/O, kecuali `rewrite_prompt`
# yang hanya menyusun string. Pemanggil yang melakukan request ke DeepSeek.

from __future__ import annotations

import json
import re
from typing import Callable, Dict, List, Optional, Tuple

_THINK_INTENT_RE = re.compile(
    r"\b("
    r"let me\s+(?:first\s+|also\s+|now\s+)?(?:search|check|run|fetch|do|get|build|test|probe|read|write|inspect|enumerate|verify|execute|query|try|start|look|find|use|call|open|download|parse|scan|grep)|"
    r"i\s+(?:will|should)\s+(?:search|check|run|fetch|build|test|probe|read|write|inspect|verify|execute)|"
    r"(?:saya|aku)\s+(?:akan\s+)?(?:cari|cek|baca|tulis|jalankan|ambil|mulai|uji|bangun|eksekusi)"
    r")\b",
    re.IGNORECASE,
)

_RESP_INTENT_RE = re.compile(
    r"\b("
    r"riset dimulai|eksekusi dimulai|probe dimulai|"
    r"(?:aku|saya)\s+(?:mulai|ambil|cek|jalankan|uji|baca|tulis|bangun|cari|eksekusi|telusuri|pakai|unduh|tarik)"
    r")\b",
    re.IGNORECASE,
)


_FAKE_MUTATION_DISABLED_RE = re.compile(
    r"("
    r"mutation tools\b.{0,80}\bdisabled|"
    r"tools are disabled|"
    r"tool mutasi\b.{0,40}\b(?:nonaktif|dinonaktifkan)|"
    r"aku tunggu tool aktif"
    r")",
    re.IGNORECASE | re.DOTALL,
)


def is_unfulfilled_tool_intent(thinking: str, clean_text: str) -> bool:
    """True bila model baru menulis pengantar aksi/riset pendek tanpa blok perkakas,
    ATAU berhenti karena tertipu tag `<system_warning>Mutation tools ... are disabled`
    halusinasi miliknya sendiri.
    """
    t_clean = (clean_text or "").strip()
    t_think = (thinking or "").strip()
    if _FAKE_MUTATION_DISABLED_RE.search(t_think) or _FAKE_MUTATION_DISABLED_RE.search(t_clean):
        return True
    if len(t_clean) > 600:
        return False
    think_tail = t_think[-450:]
    if _THINK_INTENT_RE.search(think_tail):
        return True
    if len(t_clean) <= 450 and _RESP_INTENT_RE.search(t_clean):
        return True
    return False


def intent_nudge_prompt(clean_text: str, tools_note: str = "", attempt: int = 1) -> str:
    """Susun instruksi singkat untuk menagih blok perkakas yang belum keluar."""
    if _FAKE_MUTATION_DISABLED_RE.search(clean_text or ""):
        baris = [
            "KOREKSI SISTEM: Tag `<system_warning>Mutation tools ... are disabled` "
            "pada turn sebelumnya adalah teks halusinasi yang ikut tertulis setelah "
            "`TOOL_CALL_END`, BUKAN peringatan dari Hermes.",
            "Seluruh perkakas mutasi (`terminal`, `write_file`, `patch`, "
            "`execute_code`, `skill_manage`) AKTIF PENUH dan siap dijalankan sekarang.",
            "Langsung keluarkan blok perkakas untuk batch yang baru saja Anda "
            "ajukan dengan format wajib:",
            "",
            "OK_TOOL_CALL_START",
            '{"tool": "<nama_perkakas>", "input": {<parameter lengkap>}}',
            "TOOL_CALL_END",
        ]
        if tools_note:
            baris += ["", tools_note]
        baris += ["", "Assistant:"]
        return "\n".join(baris)

    baris = [
        "Anda baru saja menyatakan akan memulai eksekusi/riset, tetapi belum "
        "menyertakan blok pemanggilan perkakas.",
        "Langsung keluarkan pemanggilan perkakas untuk langkah yang baru saja "
        "Anda rencanakan. Jangan tulis ulang kalimat pengantar, langsung tulis "
        "blok perkakas dengan format wajib:",
        "",
        "OK_TOOL_CALL_START",
        '{"tool": "<nama_perkakas>", "input": {<parameter lengkap>}}',
        "TOOL_CALL_END",
    ]
    if tools_note:
        baris += ["", tools_note]
    baris += ["", "Assistant:"]
    return "\n".join(baris)

# Batas percobaan penulisan ulang. Satu percobaan terlalu sedikit karena model
# sering mengulang kesalahan yang sama. Tiga cukup untuk menangkap variasi
# instruksi tanpa membuat satu turn membengkak dan memicu pemotongan lagi.
MAX_REWRITE_ATTEMPTS = 3

# Batas karakter instruksi yang dikirim ke model. Instruksi panjang membuat
# prompt membengkak, dan prompt membengkak justru memperbesar kemungkinan
# balasan terpotong lagi. Karena itu instruksi dipangkas keras.
MAX_INSTRUCTION_CHARS = 1200


def missing_fields_report(dropped: List[str]) -> str:
    """Ringkas alasan pembuangan jadi satu baris yang bisa dibaca model.

    `validate_tool_calls` mengembalikan alasan seperti
    `write_file:missing=content`. Model tidak paham format itu, jadi diterjemahkan
    menjadi kalimat langsung yang menyebut perkakas dan field yang hilang.

    Mengembalikan string kosong kalau tidak ada yang dibuang.
    """
    if not dropped:
        return ""

    perangkat: Dict[str, List[str]] = {}
    for alasan in dropped:
        if ":missing=" in alasan:
            nama, _, field = alasan.partition(":missing=")
            perangkat.setdefault(nama, []).extend(
                f for f in field.split(",") if f)
        elif alasan.endswith(":args_not_dict"):
            nama = alasan.split(":", 1)[0]
            perangkat.setdefault(nama, []).append("argumen harus objek JSON")
        elif alasan.startswith("unnamed_tool:"):
            perangkat.setdefault("(tanpa nama)", []).append("nama perkakas")
        else:
            perangkat.setdefault(alasan, []).append("format tidak dikenali")

    bagian = []
    for nama in sorted(perangkat):
        field = ", ".join(sorted(set(perangkat[nama])))
        bagian.append(f"{nama} -> {field}")
    return "; ".join(bagian)


def rewrite_prompt(last_user_text: str, dropped: Optional[List[str]] = None,
                   attempt: int = 1, broken: bool = False) -> str:
    """Susun instruksi penulisan ulang untuk model.

    Dua kegagalan diperlakukan berbeda karena penyebabnya berbeda.

    Kalau `broken` True, balasan terpotong di tengah blok perkakas. Model perlu
    diminta menulis ulang blok itu SAJA, lengkap dengan penanda pembuka dan
    penutup, tanpa prosa.

    Kalau `dropped` tidak kosong, tool call terbentuk tapi field wajibnya tidak
    lengkap. Model perlu diberi tahu field mana yang hilang, karena tanpa itu ia
    mengulang kesalahan yang sama.

    `attempt` memvariasikan tekanan instruksi. Percobaan pertama sopan, percobaan
    berikutnya semakin ketat. Variasi ini penting karena model cenderung mengulang
    output identik kalau instruksinya identik.
    """
    if not last_user_text:
        return ""

    tekanan = {
        1: "Tuliskan ulang blok perkakas itu saja.",
        2: "Penting: jangan tulis prosa sama sekali. Hanya blok perkakas.",
        3: "Terakhir kali. Balas HANYA dengan blok perkakas lengkap. "
           "Tidak ada kalimat pengantar, tidak ada penjelasan.",
    }
    perintah = tekanan.get(min(attempt, 3), tekanan[3])

    baris = [
        "Balasan Anda sebelumnya tidak bisa dijalankan.",
        perintah,
        "",
        "Format yang wajib dipakai:",
        "OK_TOOL_CALL_START",
        '{"tool": "<nama_perkakas>", "input": {<parameter lengkap>}}',
        "TOOL_CALL_END",
        "",
    ]

    if broken:
        baris += [
            "Penyebab: blok perkakas terpotong, penanda penutup tidak ada.",
            "Tulis ulang blok perkakas TERAKHIR, lengkap dengan penanda "
            "pembuka DAN penanda penutup, dalam satu balasan.",
            "",
        ]

    if dropped:
        ringkas = missing_fields_report(dropped)
        if ringkas:
            baris += [
                "Penyebab: field wajib tidak lengkap.",
                f"Perkakas dan field yang harus dilengkapi: {ringkas}",
                "Setiap field wajib HARUS ada dan tidak boleh kosong.",
                "",
            ]

    baris += [
        "Kalau permintaan aslinya tidak membutuhkan perkakas, jawab biasa "
        "tanpa penanda apa pun.",
        "",
        f"Permintaan asli:\n{last_user_text[:2000]}",
        "",
        "Assistant:",
    ]

    instruksi = "\n".join(baris)
    if len(instruksi) > MAX_INSTRUCTION_CHARS:
        instruksi = instruksi[:MAX_INSTRUCTION_CHARS]
    return instruksi


def salvage_from_broken(text: str) -> List[Tuple[str, dict]]:
    """Coba selamatkan tool call dari blok yang penutupnya hilang atau rusak.

    Dipakai sebagai upaya terakhir sebelum meminta penulisan ulang. Blok
    terpotong atau blok dengan newline/kutip mentah di dalam JSON masih bisa
    diselamatkan lewat `_repair_tool_json`.

    Mengembalikan daftar kosong kalau tidak ada yang bisa diselamatkan.
    """
    if not text:
        return []

    from .tools_emu import _repair_tool_json, _obj_to_call

    penanda_awal = ("OK_TOOL_CALL_START", "<tool_call_start|>",
                    "tool_call_start", "TOOL_CALL_START")
    idx = -1
    for p in penanda_awal:
        pos = text.rfind(p)
        if pos > idx:
            idx = pos
    if idx == -1:
        return []

    sisa = text[idx:]
    for p in penanda_awal:
        if sisa.startswith(p):
            sisa = sisa[len(p):]
            break
    sisa = sisa.lstrip().lstrip("|>").lstrip()

    awal = sisa.find("{")
    if awal == -1:
        return []
    akhir = sisa.rfind("}")
    if akhir <= awal:
        return []

    potongan = sisa[awal:akhir + 1]
    obj = _repair_tool_json(potongan)
    if not isinstance(obj, dict):
        return []
    parsed = _obj_to_call(obj)
    return [parsed] if parsed is not None else []


def synthetic_error_call(dropped: List[str]) -> Optional[Tuple[str, dict]]:
    """Bentuk tool call sintetis yang memaksa agen melihat kegagalannya.

    Dipakai hanya kalau semua upaya penulisan ulang gagal. Tujuannya bukan
    menjalankan perkakas, tapi mencegah agen menganggap turn selesai.

    Mekanismenya: bridge mengirim `finish_reason: tool_calls` berisi tool call
    ke perkakas `terminal` dengan perintah yang akan gagal dan pesan errornya
    menjelaskan apa yang hilang. Hermes menjalankannya, mendapat error nyata,
    lalu agen melihat kegagalan itu sebagai hasil perkakas, bukan sebagai
    jawaban final. Dengan begitu pekerjaan tetap berlanjut.

    Mengembalikan None kalau tidak ada yang dibuang, karena tanpa itu tidak ada
    yang perlu dilaporkan.
    """
    if not dropped:
        return None

    ringkas = missing_fields_report(dropped)
    pesan = (
        "TOOL CALL GAGAL DI BRIDGE. "
        f"Perkakas dan field yang hilang: {ringkas}. "
        "Tuliskan ulang blok perkakas dengan field wajib yang lengkap."
    )
    # Perintah sengaja tidak valid supaya gagal terlihat, tapi tetap aman
    # dijalankan. Tidak ada operasi destruktif.
    return ("terminal", {"command": f"echo {json.dumps(pesan)}"})


def should_retry(attempt: int, has_calls: bool,
                 still_broken: bool) -> bool:
    """Putuskan apakah percobaan penulisan ulang perlu diulang.

    Berhenti kalau sudah ada tool call sah, karena itu tanda sukses.
    Berhenti kalau batas percobaan tercapai.
    Lanjut kalau masih ada tool call yang dibuang atau blok masih rusak.
    """
    if has_calls and not still_broken:
        return False
    return attempt < MAX_REWRITE_ATTEMPTS
