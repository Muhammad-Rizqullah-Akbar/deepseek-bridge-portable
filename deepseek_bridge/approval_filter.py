"""Filter prompt approval di sisi bridge.

Masalah yang dipecahkan: Hermes punya mekanisme approval yang meminta model
auxiliary menilai sebuah perintah berisiko. Prompt penilaian itu berbunyi
"The following command was flagged as: ..." dan diakhiri "Respond with exactly
one word: APPROVE, DENY, atau ESCALATE". Kalau provider utama diarahkan ke
bridge, prompt itu ikut terkirim ke DeepSeek, model DeepSeek yang menjawab
APPROVE, dan keputusan keamanan jadi diambil oleh model yang sama dengan yang
menjalankan perintah. Prompt itu juga tercatat di sesi chat DeepSeek.

Filter ini mengenali prompt semacam itu dan menahannya di bridge, jadi tidak
pernah sampai ke DeepSeek. Yang dikirim balik ke Hermes adalah jawaban
ESCALATE, yang membuat Hermes menanyakan approval ke pemilik sesuai perilaku
bawaannya.

Filter sengaja hanya mengenali prompt approval Hermes, bukan semua perintah
berisiko. Perintah yang sah tetap jalan normal.
"""

from __future__ import annotations

import re
from typing import List, Optional

# Penanda pembuka prompt approval Hermes. Diambil dari
# hermes-agent/tools/approval_smart.py baris 101.
_APPROVAL_HEAD = "the following command was flagged as:"

# Penanda penutup yang meminta jawaban satu kata. Dibandingkan setelah teks
# dinormalkan, jadi bentuknya tanpa newline.
#
# Hermes memakai "or ESCALATE", bukan "atau escalate". Ini terbukti dari
# hermes-agent/tools/approval_smart.py baris 107. Versi lama hanya mencari
# "atau escalate", jadi prompt approval Hermes yang asli TIDAK pernah
# dikenali dan tetap terkirim ke DeepSeek. Akibatnya DeepSeek yang
# memutuskan approval, bukan pemilik.
_APPROVAL_TAIL = "respond with exactly one word: approve, deny, or escalate"

# Blok perintah yang dibungkus pembuka dan penutup.
_COMMAND_BLOCK = re.compile(
    r"<command>\s*(?P<cmd>.*?)\s*</command>", re.DOTALL | re.IGNORECASE
)


def _norm(text: str) -> str:
    return " ".join(str(text or "").split()).lower()


def is_approval_prompt(messages: List) -> Optional[str]:
    """Kembalikan deskripsi approval kalau payload adalah prompt approval.

    None berarti payload biasa dan harus diteruskan seperti biasa. String
    berarti prompt approval, dan isinya adalah deskripsi perintah yang
    ditandai, dipakai untuk log.
    """
    if not messages:
        return None

    # Prompt approval selalu berupa pesan user terakhir yang berdiri sendiri,
    # bukan lanjutan percakapan. Kalau payload membawa riwayat assistant,
    # ini percakapan biasa yang kebetulan membahas perintah.
    for m in messages:
        if getattr(m, "role", None) == "assistant":
            return None

    last = None
    for m in messages:
        if getattr(m, "role", None) == "user":
            last = m
    if last is None:
        return None

    text = _norm(getattr(last, "content", "") or "")
    if not text:
        return None

    if _APPROVAL_HEAD not in text:
        return None
    if _APPROVAL_TAIL not in text:
        return None

    # Ambil deskripsi antara penanda pembuka dan blok perintah. Kalau tidak
    # ada blok perintah, potong sebelum penanda penutup supaya deskripsi
    # tidak menyerap instruksi jawaban.
    desc = text.split(_APPROVAL_HEAD, 1)[1]
    desc = desc.split("<command>", 1)[0]
    desc = desc.split(_APPROVAL_TAIL, 1)[0]
    desc = desc.strip()
    return desc or "command flagged"


def extract_flagged_command(messages: List) -> str:
    """Ambil teks perintah di dalam blok <command>, untuk log dan audit."""
    for m in messages:
        if getattr(m, "role", None) != "user":
            continue
        raw = str(getattr(m, "content", "") or "")
        hit = _COMMAND_BLOCK.search(raw)
        if hit:
            return hit.group("cmd").strip()
    return ""
