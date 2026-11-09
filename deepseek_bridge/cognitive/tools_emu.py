"""Emulasi tool call untuk DeepSeek web.

DeepSeek web tidak punya endpoint tool call maupun function call, jadi
jembatan ini tidak bisa meminta model memanggil perkakas secara native.
Yang bisa dilakukan adalah mengajarkan model memakai penanda teks, lalu
menerjemahkan penanda itu menjadi objek tool_calls bergaya OpenAI supaya
klien seperti Hermes tetap melihat protokol yang ia kenali.

Alur kerja:

1. Daftar perkakas diserialkan jadi blok instruksi ringkas.
2. Blok itu ditempel ke prompt sebagai system prompt tambahan.
3. Kalau balasan diawali penanda TOOL_CALL, jembatan memecahnya menjadi
   tool_calls OpenAI dan mengembalikannya, bukan teks biasa.

Pendekatan ini bekerja karena model mengikuti format yang diminta, sudah
diuji langsung. Keterbatasannya, model tetap bisa saja menjawab dengan
prosa biasa, jadi pemanggil wajib menangani kedua kemungkinan.
"""

from __future__ import annotations

import re
import json
import uuid
import hashlib
import threading
from typing import List, Optional, Tuple

# Penanda inti yang dicari parser. Bentuk ini bukan karangan bridge, melainkan
# protokol asli yang dilatih ke model DeepSeek dan terbukti dihasilkan model
# secara spontan: balasan berstatus FINISHED di server diawali
# `OK_TOOL_CALL_START` dan diakhiri `TOOL_CALL_END`. Uji langsung dengan prompt
# yang mendeskripsikan format ini menghasilkan keluaran yang patuh persis.
#
# Kode lama memakai `TOOL_CALL_START` dengan `SACRIFICIAL_PREFIX = "OK_"` dan
# membangun varian pemotongan untuk menoleransi hilangnya 1 sampai 3 karakter
# depan. Itu solusi untuk masalah yang bridge buat sendiri: `OK_` bukan korban
# pemotongan, ia bagian dari penanda utuh. Memotong penanda yang benar lalu
# menoleransi potongannya membuat parser rapuh dan deteksi tidak deterministik.
TOOL_CALL_START = "OK_TOOL_CALL_START"
TOOL_CALL_END = "TOOL_CALL_END"

# Bentuk tag yang model hasilkan sendiri, berbeda dari yang diajarkan.
#
# Bridge mengajarkan `OK_TOOL_CALL_START` dan `TOOL_CALL_END`, tapi di
# lapangan model memproduksi `<tool_call_start|>` dan `<tool_call_end|>`.
# Bentuk ini terlihat di log nyata: model menulis kalimat pengantar, lalu
# blok tool call dengan tag itu, dan bloknya tidak pernah dieksekusi.
#
# Sebelum varian ini ada, parser tidak mengenali tag tersebut. Ia mengira
# blok itu teks biasa, jadi tool call bocor ke content, Hermes tidak
# menjalankan perkakas, dan balasan berhenti di situ. Tidak ada error dan
# tidak ada warning, karena menurut parser tidak ada yang rusak.
TAG_TOOL_CALL_START = "<tool_call_start|>"
TAG_TOOL_CALL_END = "<tool_call_end|>"

# Batas jumlah perkakas yang dijelaskan ke model per request.
# Ditingkatkan dari 24 ke 48 dengan Semantic Relevance Ranking untuk mendukung
# ekosistem ribuan tools tanpa context bloat atau kebingungan model.
MAX_TOOLS = 48

CORE_ESSENTIAL_TOOLS = {
    "terminal", "read_file", "write_file", "patch", "search_files",
    "execute_code", "web_search", "web_extract", "memory", "delegate_task",
    "clarify", "session_search", "skill_view", "skill_manage", "process_manage"
}


def _one_line(text: str, limit: int = 160) -> str:
    """Ringkas deskripsi tool jadi satu baris pendek."""
    clean = " ".join(str(text or "").split())
    if len(clean) > limit:
        clean = clean[: limit - 3] + "..."
    return clean or "tanpa deskripsi"


def select_relevant_tools(tools: List[dict], query: Optional[str] = None, max_tools: int = MAX_TOOLS) -> Tuple[List[dict], int]:
    """Pilih perkakas paling relevan berdasarkan query pengguna dan perkakas inti.
    
    Menjamin perkakas sistem penting (terminal, write_file, web_search, dll) selalu
    tersedia, sementara perkakas spesifik (nuclei, feroxbuster, blender, dll)
    dipilih secara Just-In-Time (JIT) dari ribuan toolset yang terdaftar.
    """
    if not tools or len(tools) <= max_tools:
        return tools or [], 0

    core_tools: List[dict] = []
    other_tools: List[dict] = []

    for t in tools:
        fn = t.get("function", t) if isinstance(t, dict) else {}
        name = str(fn.get("name") or "").lower()
        if name in CORE_ESSENTIAL_TOOLS:
            core_tools.append(t)
        else:
            other_tools.append(t)

    remaining_quota = max(12, max_tools - len(core_tools))

    if query and str(query).strip():
        q_tokens = set(re.findall(r"\w{3,}", str(query).lower()))
        scored_tools = []
        for t in other_tools:
            fn = t.get("function", t) if isinstance(t, dict) else {}
            name = str(fn.get("name") or "").lower()
            desc = str(fn.get("description") or "").lower()
            score = 0
            for token in q_tokens:
                if token in name:
                    score += 5
                elif token in desc:
                    score += 1
            scored_tools.append((score, t))
        scored_tools.sort(key=lambda x: x[0], reverse=True)
        selected_others = [t for score, t in scored_tools[:remaining_quota]]
    else:
        selected_others = other_tools[:remaining_quota]

    selected = core_tools + selected_others
    omitted = len(tools) - len(selected)
    return selected, omitted


_INSTRUCTIONS_CACHE: dict[str, str] = {}
_INSTRUCTIONS_CACHE_LOCK = threading.Lock()


def build_tool_instructions(tools: Optional[List[dict]], query: Optional[str] = None) -> str:
    """Susun blok instruksi perkakas untuk ditempel ke prompt.

    Mengembalikan string kosong kalau tidak ada perkakas, supaya prompt
    tetap bersih untuk pemakai yang tidak mengirim tools.
    Mendukung perangkingan relevansi semantik JIT saat jumlah perkakas masif (>48).
    """
    if not tools:
        return ""

    selected_tools, omitted_count = select_relevant_tools(tools, query=query, max_tools=MAX_TOOLS)

    t_key = None
    try:
        raw_key = f"{query or ''}:" + json.dumps(selected_tools, sort_keys=True)
        t_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        with _INSTRUCTIONS_CACHE_LOCK:
            cached = _INSTRUCTIONS_CACHE.get(t_key)
            if cached is not None:
                return cached
    except Exception:
        t_key = None

    lines = [
        "Aturan pemakaian perkakas. Ini hanya soal format teknis pemanggilan, bukan pembatasan percakapan.",
        "",
        "1. Jika pengguna memberikan perintah kerja, analisis sistem, instruksi pencarian, atau tugas operasional yang memerlukan eksekusi nyata, panggil perkakas (Tool Call) secara langsung tanpa menunda.",
        "2. Jika pengguna mengajukan pertanyaan konseptual, diskusi ide, klarifikasi, sapaan, atau menanyakan identitas diri, jawab langsung secara komprehensif, alami, dan sesuai persona Anda.",
        "",
        "Untuk memakai perkakas, tulis SATU blok dengan format tepat ini:",
        "",
        f"{TOOL_CALL_START}",
        '{"tool": "<nama_perkakas>", "input": {<parameter>}}',
        "TOOL_CALL_END",
        "",
        "Contoh benar:",
        f"{TOOL_CALL_START}",
        '{"tool": "terminal", "input": {"command": "pwd"}}',
        "TOOL_CALL_END",
        "",
        "Contoh SALAH, jangan pernah ditulis:",
        "  terminal(pwd)",
        "  Saya akan menjalankan pwd",
        '  {"tool": "terminal"}  (tanpa input)',
        "  <||DSML|| invoke> (dilarang keras menggunakan tag DSML internal mentah)",
        "",
        "Nama perkakas harus persis seperti daftar di bawah.",
        "Isi input harus JSON yang valid di satu baris.",
        "Kalau tidak perlu perkakas, jawab biasa saja.",
        "",
        "Daftar perkakas:",
    ]

    for t in selected_tools:
        fn = t.get("function", t) if isinstance(t, dict) else {}
        name = fn.get("name") or "perkakas"
        desc = _one_line(fn.get("description"))

        params = fn.get("parameters") or {}
        props = params.get("properties") or {}
        if props:
            sig = ", ".join(
                f"{k}: {v.get('type', 'string')}" for k, v in list(props.items())[:6]
            )
        else:
            sig = "tanpa parameter"
        lines.append(f"- {name}({sig}) :: {desc}")

    if omitted_count > 0:
        lines.append(f"- ...dan {omitted_count} perkakas terdaftar lainnya (dapat dipanggil langsung via nama)")

    res = "\n".join(lines)
    if t_key:
        with _INSTRUCTIONS_CACHE_LOCK:
            if len(_INSTRUCTIONS_CACHE) > 200:
                _INSTRUCTIONS_CACHE.clear()
            _INSTRUCTIONS_CACHE[t_key] = res
    return res


def _find_dsml_invoke(text: str) -> Optional[Tuple[str, dict, int, int]]:
    """Cari tool call berbentuk DSL native model, bukan penanda kita.

    Model kadang mengabaikan penanda yang diajarkan dan memakai syntax
    internalnya sendiri, misalnya:

        < calls>
        <||DSML|| invoke name="terminal">
        <||DSML|| parameter name="command" string="true">pwd
        </||DSML|| parameter>
        </||DSML|| invoke>
        </ calls>

    Bentuk ini bocor ke content kalau tidak ditangani. Fungsi ini
    mengenalinya dan mengubahnya menjadi (nama, argumen).

    Pemisah bisa berupa pipa maupun karakter fullwidth, jadi pembersihan
    simbol dilakukan lebih dulu supaya satu pola cukup untuk semua varian.
    """
    cleaned = text.replace("\uff5c", "|")
    m = re.search(r"DSML.{0,12}?invoke\s+name=\"([^\"]+)\"", cleaned,
                  re.DOTALL)
    if not m:
        return None
    name = m.group(1).strip()
    start = m.start()

    # Batas akhir blok dicari lebih dulu, lalu pencarian parameter dibatasi
    # hanya di dalam blok ini. Tanpa batas itu, parameter dari invoke
    # berikutnya akan ikut terbaca dan argumennya tercampur.
    close = re.search(r"</.{0,12}?invoke", cleaned[m.end():], re.DOTALL)
    if close:
        end = m.end() + close.end()
        body = cleaned[m.end():m.end() + close.start()]
    else:
        end = len(cleaned)
        body = cleaned[m.end():]

    args: dict = {}
    p = re.compile(
        r"DSML.{0,12}?parameter\s+name=\"([^\"]+)\"[^>]*>(.*?)"
        r"</.{0,12}?parameter",
        re.DOTALL,
    )
    for pm in p.finditer(body):
        args[pm.group(1).strip()] = _unescape_xml(pm.group(2)).strip()

    return name, args, start, end


def _unescape_xml(value: str) -> str:
    """Kembalikan entitas yang biasa dipakai model di dalam parameter."""
    if "&" not in value:
        return value
    for ent, ch in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", "\""),
                    ("&apos;", "'"), ("&amp;", "&")):
        value = value.replace(ent, ch)
    return value


def _unescape_json_str(raw: str) -> str:
    """Unescape string JSON secara toleran ketika json.loads gagal karena kutip mentah."""
    out: List[str] = []
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch == "\\" and i + 1 < n:
            nxt = raw[i + 1]
            if nxt == "n":
                out.append("\n")
                i += 2
                continue
            if nxt == "r":
                out.append("\r")
                i += 2
                continue
            if nxt == "t":
                out.append("\t")
                i += 2
                continue
            if nxt in ('"', "\\", "/"):
                out.append(nxt)
                i += 2
                continue
            if nxt == "u" and i + 5 < n:
                hex_part = raw[i + 2:i + 6]
                try:
                    out.append(chr(int(hex_part, 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
        out.append(ch)
        i += 1
    return "".join(out)


def _repair_tool_json(raw: str) -> Optional[dict]:
    """Perbaiki atau ekstrak objek JSON tool call yang mengandung newline mentah,
    kurung kurawal penutup berlebih (`}}`), atau tanda kutip tak di-escape di
    dalam field `content` / `command` / `code`.
    """
    if not raw:
        return None
    s = raw.strip()
    if not s.startswith("{"):
        first_brace = s.find("{")
        if first_brace == -1:
            return None
        s = s[first_brace:]
    last_brace = s.rfind("}")
    if last_brace != -1:
        s = s[:last_brace + 1]

    # 1. Coba json.loads(..., strict=False) langsung, serta variasi perbaikan
    #    ketidakseimbangan kurung kurawal di ujung (`}}` pada bentuk rata, atau
    #    kurang `}` di akhir).
    candidates = [s]
    if s.endswith("}}"):
        candidates.append(s[:-1])
    if s.endswith("}}}"):
        candidates.append(s[:-2])
    open_c = s.count("{")
    close_c = s.count("}")
    if open_c > close_c and (open_c - close_c) <= 2:
        candidates.append(s + ("}" * (open_c - close_c)))

    for cand in candidates:
        try:
            obj = json.loads(cand, strict=False)
            if isinstance(obj, dict) and (obj.get("tool") or obj.get("name")):
                return obj
        except json.JSONDecodeError:
            pass

    # 2. Ekstraksi struktural untuk `write_file` ketika isi markdown di dalam
    #    `"content": "..."` memuat tanda kutip ganda mentah (mis. blok kode JS).
    m_tool = re.search(r'"(?:tool|name)"\s*:\s*"([^"]+)"', s)
    if not m_tool:
        return None
    tool_name = m_tool.group(1).strip()

    if tool_name == "write_file":
        m_path = re.search(r'"path"\s*:\s*"([^"]+)"', s)
        m_c = re.search(r'"content"\s*:\s*"', s)
        if m_path and m_c:
            c_start = m_c.end()
            if m_path.start() > c_start:
                # "content" muncul sebelum "path"
                sub = s[c_start:m_path.start()]
                m_end = re.search(r'"\s*,\s*(?:"input"\s*:\s*\{\s*)?$', sub)
                raw_c = sub[:m_end.start()] if m_end else sub
            else:
                # "path" muncul sebelum "content"
                sub = s[c_start:]
                m_end = re.search(r'"\s*\}+\s*$', sub)
                if not m_end:
                    return None
                raw_c = sub[:m_end.start()]
            return {
                "tool": "write_file",
                "input": {
                    "path": _unescape_json_str(m_path.group(1)),
                    "content": _unescape_json_str(raw_c),
                },
            }

    if tool_name == "terminal":
        m_cmd = re.search(r'"command"\s*:\s*"', s)
        if m_cmd:
            c_start = m_cmd.end()
            sub = s[c_start:]
            m_timeout = re.search(r'"\s*,\s*"timeout"\s*:\s*(\d+)\s*\}+\s*$', sub)
            if m_timeout:
                raw_cmd = sub[:m_timeout.start()]
                return {
                    "tool": "terminal",
                    "input": {
                        "command": _unescape_json_str(raw_cmd),
                        "timeout": int(m_timeout.group(1)),
                    },
                }
            m_end = re.search(r'"\s*\}+\s*$', sub)
            if m_end:
                raw_cmd = sub[:m_end.start()]
                return {
                    "tool": "terminal",
                    "input": {"command": _unescape_json_str(raw_cmd)},
                }

    return None


def _obj_to_call(obj: dict) -> Optional[Tuple[str, dict]]:
    """Konversi dict hasil parse JSON menjadi tuple (nama_tool, dict_argumen)."""
    if not isinstance(obj, dict):
        return None
    name = obj.get("tool") or obj.get("name")
    if not name and isinstance(obj.get("function"), dict):
        name = obj["function"].get("name")
    if not name or not isinstance(name, str):
        return None
    payload = obj.get("input", obj.get("arguments"))
    if payload is None and isinstance(obj.get("function"), dict):
        payload = obj["function"].get("arguments")
    if isinstance(payload, str):
        try:
            parsed_p = json.loads(payload, strict=False)
            if isinstance(parsed_p, dict):
                payload = parsed_p
        except json.JSONDecodeError:
            pass
    if isinstance(payload, dict):
        return (str(name), payload)
    if payload is None:
        args = {k: v for k, v in obj.items() if k not in ("tool", "name", "type", "function")}
        if args:
            return (str(name), args)
        return None
    return (str(name), {"input": payload})


def _looks_like_truncated_json(text: str) -> bool:
    """True kalau teks jelas JSON yang terpotong, bukan teks biasa.

    Dipakai untuk membedakan dua hal yang sama-sama gagal `json.loads`:

        1. JSON terpotong di tengah, karena model kehabisan token
        2. Teks biasa yang kebetulan ada di dalam blok penanda

    Hanya kasus pertama yang perlu ditandai rusak. Menandai kasus kedua akan
    membuat setiap balasan biasa dianggap gagal dan memicu pemulihan siapa.

    Ciri yang diperiksa: diawali `{` dan tanda kutip serta kurungnya tidak
    seimbang. JSON utuh selalu seimbang, jadi ketidakseimbangan adalah bukti
    pemotongan, bukan kebetulan.
    """
    if not text:
        return False
    bersih = text.strip()
    if not bersih.startswith("{"):
        return False
    # Kurung kurawal dan kurung siku wajib seimbang pada JSON utuh.
    if bersih.count("{") != bersih.count("}"):
        return True
    if bersih.count("[") != bersih.count("]"):
        return True
    # Tanda kutip juga. Jumlah ganjil berarti ada string yang tidak ditutup.
    if bersih.count('"') % 2 != 0:
        return True
    return False


def parse_tool_calls(text: str) -> Tuple[str, List[Tuple[str, dict]], bool]:
    """Pisahkan teks biasa dari tool call yang terkandung di dalamnya.

    Mengembalikan (teks_bersih, daftar_tool_call, blok_rusak). Setiap tool
    call berupa tuple (nama, input_dict). Kalau tidak ada penanda,
    teks_bersih sama dengan masukan dan daftar kosong.

    Elemen ketiga penting. Nilai True berarti ada penanda awal tool call
    tanpa penutup, jadi balasan terpotong di tengah blok perkakas. Pemanggil
    wajib memeriksa nilai ini sebelum meneruskan balasan, karena meneruskan
    balasan rusak memicu kegagalan berantai di sisi klien.

    Toleransi pemotongan penting di sini. DeepSeek web memotong 1 sampai 3
    karakter awal balasan, jadi penanda yang keluar bisa kehilangan bagian
    depannya. Parser mencari semua varian itu, bukan hanya bentuk utuh.
    """
    if not text:
        return "", [], False

    # Bentuk native model diperiksa jika ada invoke eksplisit.
    # Model kadang memakai syntax internalnya sendiri (<||DSML|| invoke name="...">),
    # dan bentuk itu tetap harus diterjemahkan jika memuat tool call nyata.
    if "DSML" in text and "invoke" in text:
        clean_parts: List[str] = []
        calls: List[Tuple[str, dict]] = []
        remaining = text
        while True:
            found = _find_dsml_invoke(remaining)
            if found is None:
                clean_parts.append(remaining)
                break
            name, args, start, end = found
            clean_parts.append(remaining[:start])
            calls.append((name, args))
            remaining = remaining[end:]

        if calls:
            clean = _strip_marker_leftovers("".join(clean_parts))
            return clean, calls, False
        text = "".join(clean_parts)
        # Bersihkan tag DSML invoke telanjang tanpa atribut yang dihasilkan karena loop degenerate
        text = re.sub(r"<[|]{1,2}DSML[|]{1,2}\s*invoke>\s*", "", text)
        text = re.sub(r"</[|]{1,2}DSML[|]{1,2}\s*invoke>\s*", "", text)

    # Deteksi jika balasan terpotong tepat saat membuka tag DSML (<DSML calls>\n<)
    if re.search(r"<\s*(?:DSML\s+)?calls\b[^>]*>\s*<?$", text, re.IGNORECASE):
        clean = re.sub(r"<\s*(?:DSML\s+)?calls\b.*$", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
        clean = _strip_marker_leftovers(clean)
        return clean, [], True

    starts = _start_variants()
    ends = _end_variants()

    # Fence markdown ditangani lebih dulu. Model sering membungkus JSON
    # tool call dengan ```tool_call atau ```json, dan bentuk itu bukan
    # penanda yang diajarkan. Sebelum ini fence bocor ke content sebagai
    # teks biasa, jadi tool call tidak pernah terbentuk.
    fence_calls = _calls_from_fences(text)
    if fence_calls:
        clean = _FENCE_RE.sub("", text)
        return _strip_marker_leftovers(clean), fence_calls, False

    clean_parts: List[str] = []
    calls: List[Tuple[str, dict]] = []
    remaining = text
    any_broken = False

    while True:
        hit = _find_start_re(remaining)
        if hit is None:
            clean_parts.append(remaining)
            break

        start_idx, start_len = hit
        clean_parts.append(remaining[:start_idx])

        after = remaining[start_idx + start_len:]
        end_hit = _find_end_re(after)
        if end_hit is None:
            # Penanda awal tanpa penutup. Coba perbaiki isi blok terlebih dulu
            # kalau JSON di dalamnya sebenarnya sudah utuh tapi hanya penutup
            # `TOOL_CALL_END` yang terpotong.
            repaired_tail = _repair_tool_json(after)
            if repaired_tail is not None:
                parsed_tail = _obj_to_call(repaired_tail)
                if parsed_tail is not None:
                    calls.append(parsed_tail)
                    clean = "".join(clean_parts).strip()
                    return _strip_marker_leftovers(clean), calls, False
            clean = "".join(clean_parts).strip()
            return _strip_marker_leftovers(clean), calls, True

        end_idx, end_len = end_hit
        block = after[:end_idx].strip()
        remaining = after[end_idx + end_len:]

        # Kumpulkan call per-blok (bukan memeriksa `if not calls:` secara global!
        # Sebelumnya, jika blok pertama dalam balasan yang sama berhasil di-parse,
        # `calls` sudah tidak kosong sehingga blok kedua yang multi-baris seperti
        # `write_file` dilewati begitu saja dan hilang tanpa jejak).
        block_calls: List[Tuple[str, dict]] = []

        for line in block.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped, strict=False)
            except json.JSONDecodeError:
                continue
            parsed_call = _obj_to_call(obj)
            if parsed_call is not None:
                block_calls.append(parsed_call)

        if not block_calls:
            merged = block.strip()
            obj = _repair_tool_json(merged)
            if isinstance(obj, dict):
                parsed_call = _obj_to_call(obj)
                if parsed_call is not None:
                    block_calls.append(parsed_call)

            if not block_calls and merged:
                # Ada blok dengan penanda awal DAN penutup eksplisit, namun
                # gagal menghasilkan satu pun tool call walau sudah diperbaiki.
                # Wajib ditandai broken=True agar pemulihan jalan, TIDAK BOLEH
                # dikembalikan sebagai broken=False yang akan menghentikan agen!
                any_broken = True

        calls.extend(block_calls)

    # Bentuk rata satu baris, contoh:
    #   {"tool": "write_file", "content": "...", "path": "..."}
    #
    # Parser lama hanya membaca obj["input"], jadi bentuk rata jatuh ke
    # payload {"input": None} lalu dibuang. Tool call hilang tanpa jejak.
    # Di sini kunci selain penanda nama diperlakukan sebagai argumen.
    flattened: List[Tuple[str, dict]] = []
    for name, payload in calls:
        if set(payload.keys()) == {"input"} and payload["input"] is None:
            continue
        if set(payload.keys()) == {"input"} and isinstance(payload["input"], dict):
            flattened.append((name, payload["input"]))
        else:
            flattened.append((name, payload))
    calls = flattened

    clean = "".join(clean_parts).strip()

    # Fallback untuk JSON tool call tanpa penanda (`OK_TOOL_CALL_START`) dan
    # tanpa fence markdown (mis. model langsung menulis `{"name": "terminal",
    # "arguments": {...}}` di akhir balasan, seperti pada seq=272 di bridge.db).
    if not calls and not any_broken and ("{" in clean and "}" in clean):
        unfenced_clean, unfenced_calls = _extract_unfenced_json_calls(clean)
        if unfenced_calls:
            return _strip_marker_leftovers(unfenced_clean), unfenced_calls, False

    # Sisa prefix korban kadang tertinggal di tepi teks bersih. Buang agar
    # tidak pernah terlihat oleh pengguna.
    return _strip_marker_leftovers(clean), calls, any_broken


def _strip_marker_leftovers(text: str) -> str:
    """Buang sisa penanda yang tertinggal di teks bersih.

    Satu sumber bocoran: prefix korban. Instruksi menyuruh model menulis
    `OK_TOOL_CALL_START`. Kalau model mematuhinya tapi penandanya tidak
    lengkap, `OK` tertinggal sebagai teks dan terlihat oleh pengguna.

    Yang dibuang hanya `OK` yang benar-benar berdiri sendiri sebagai baris,
    bukan `OK` di dalam kalimat. Jadi "Booking sudah OK." dan
    "OK dimengerti." tetap utuh. Menghapus berdasarkan kata saja akan
    merusak prosa biasa.
    """
    if not text:
        return text

    lines = text.split("\n")
    kept = []
    for line in lines:
        stripped = line.strip()
        # Buang baris yang isinya hanya sisa penanda tanpa JSON nyata.
        # Bentuk utuh `OK_TOOL_CALL_START` tidak lagi dipakai di sini karena
        # penanda lengkap selalu menyertai JSON, jadi yang perlu dibuang hanya
        # potongan yang lolos dari parser karena balasan terpotong di tengah.
        if re.fullmatch(r"OK_?|TOOL_?|_TOOL_?|OL_?|L_C|_CAL|ALL_?", stripped):
            continue
        kept.append(line)

    clean = "\n".join(kept)
    clean = re.sub(r"</?(?:DSML\s+)?calls>", "", clean, flags=re.IGNORECASE)
    clean = clean.replace("\uff5c", "")
    # Buang tag <system_warning>...</system_warning> yang kadang dihalusinasikan
    # model tepat setelah TOOL_CALL_END (meniru batas harness agen). Jika lolos,
    # model pada turn berikutnya mengira perkakas mutasi miliknya dinonaktifkan.
    clean = re.sub(
        r"<\s*system[_\s-]*warning\b[^>]*>.*?</\s*system[_\s-]*warning\s*>",
        "",
        clean,
        flags=re.DOTALL | re.IGNORECASE,
    )
    clean = re.sub(
        r"<\s*system[_\s-]*warning\b[^>]*>.*$",
        "",
        clean,
        flags=re.DOTALL | re.IGNORECASE,
    )
    clean = re.sub(r"<\s*>\s*", "", clean)
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    return clean.strip()


def _start_variants() -> List[str]:
    """Varian penanda awal, untuk kompatibilitas jalur lama.

    Fungsi ini masih dipakai, tapi bukan lagi satu-satunya pintu. Pintu
    utama sekarang :func:`_find_start_re`, berbasis regex, yang menampung
    variasi spasi, huruf besar, dan bentuk tag yang tidak persis sama.

    Varian string di sini disimpan supaya jalur lama tetap bekerja kalau
    ada pemanggil lain yang mengandalkannya.

    Penanda asli DeepSeek adalah `OK_TOOL_CALL_START` utuh, dan model
    dilatih memproduksinya begitu. Varian di sini menampung dua kasus lain.

    Kasus pertama, balasan benar benar terpotong, misalnya stream mati
    setelah sebelas karakter sehingga yang tersisa hanya
    `OK_TOOL_CALL_ST`.

    Kasus kedua, dan ini yang benar-benar terjadi di lapangan, model
    mengabaikan penanda yang diajarkan dan memakai bentuk bergaya tag
    `<tool_call_start|>`. Bentuk ini muncul di log nyata: model menulis
    kalimat pengantar, lalu blok tool call dengan tag itu, dan bloknya tidak
    pernah dieksekusi karena parser tidak mengenali tag tersebut. Akibatnya
    tool call bocor ke content sebagai teks biasa, Hermes tidak menjalankan
    perkakas, dan balasan berhenti di situ tanpa error apa pun.

    Truncation memotong dari belakang, jadi varian yang dibutuhkan adalah
    potongan DEPAN penanda, bukan potongan belakang.

    Panjang minimum 6 karakter agar tidak cocok dengan kata biasa dalam prosa.
    """
    variants: List[str] = []
    for full in (TOOL_CALL_START, TAG_TOOL_CALL_START):
        variants.append(full)
        # Potongan depan untuk truncation sungguhan, bukan untuk menoleransi
        # prefix yang bridge sendiri buang.
        for keep in range(len(full) - 1, 5, -1):
            variants.append(full[:keep])
    return variants


def _end_variants() -> List[str]:
    """Varian penanda akhir, untuk kompatibilitas jalur lama.

    Pintu utama sekarang :func:`_find_end_re`, berbasis regex.
    """
    variants: List[str] = []
    for full in (TOOL_CALL_END, TAG_TOOL_CALL_END):
        variants.append(full)
        for keep in range(len(full) - 1, 5, -1):
            variants.append(full[:keep])
    return variants


# Pola regex penanda awal dan akhir.
#
# Kenapa regex, bukan pencocokan string: model tidak konsisten. Dalam log
# nyata ia memproduksi `<tool_call_start|>`, tapi ia juga memproduksi
# `<tool_call_start >`, `<tool_call_start| >`, `<TOOL_CALL_START|>`, dan
# `<tool_call>`. Pencocokan string persis hanya menangkap satu bentuk, dan
# bentuk lain bocor ke content sebagai teks biasa tanpa error apa pun.
#
# Pola ini menampung:
#   - tanda kurung sudut, opsional
#   - spasi di dalam kurung, opsional
#   - huruf besar atau kecil
#   - pemisah berupa pipa, spasi, atau tidak ada
#   - bagian start atau end, opsional, supaya bentuk `<tool_call>` juga cocok
#
# Bagian start dan end dibuat opsional karena model kadang menulis tag
# generik tanpa membedakan pembuka dan penutup. Kalau keduanya opsional,
# satu pola menangkap semua varian, dan penutup dicari dari pola yang sama.
#
# Batas bawah panjang dijaga supaya tidak cocok dengan prosa biasa.
# Pola generik TIDAK boleh cocok dengan prosa biasa. Sebelumnya pemisah
# antar kata bersifat opsional, sehingga kalimat wajar seperti "I will make
# a tool call" ikut tertangkap. Akibatnya parser menandai balasan normal
# sebagai broken, lalu server menolaknya dengan 502 dan agen berhenti di
# tengah pekerjaan tanpa error yang bisa ditindaklanjuti.
#
# Jadi sekarang penanda generik WAJIB punya pembatas nyata: tanda kurung
# sudut di depan, atau pipa di belakang. Bentuk `<tool_call>`,
# `<tool_call>`, dan `<tool_call>` lolos. Prosa "tool call" tidak.
#
# Toleransi garis miring depan wajib ada. Bentuk `
_TAG_CALL_RE = re.compile(
    r"<\s*/?\s*tool[_\s-]*call(?:[_\s-]*(?:start|end))?\s*\|?\s*/?\s*>?"
    r"|tool[_\s-]*call(?:[_\s-]*(?:start|end))?\s*\|",
    re.IGNORECASE,
)
_START_RE = re.compile(
    r"<?\s*tool[_\s-]*call[_\s-]*start\s*\|?\s*>?",
    re.IGNORECASE,
)
_END_RE = re.compile(
    r"<?\s*tool[_\s-]*call[_\s-]*end\s*\|?\s*>?",
    re.IGNORECASE,
)
# Penanda asli DeepSeek, dipisah supaya jalur kompatibilitas tetap utuh.
# Bentuk utuh dicocokkan lebih dulu. Pola terpotong `OK_TOOL_CALL_ST` tetap
# ada untuk kompatibilitas, TAPI ia tidak lagi dipakai sebagai penanda awal
# utama: ia cocok dengan prefix pendek dan meninggalkan sisa `ART` di blok,
# sehingga JSON di belakangnya gagal di-parse dan tool call hilang tanpa
# jejak. `_find_start_re` memilih kemunculan paling awal dan paling panjang,
# jadi bentuk utuh menang selama keduanya hadir; sisa prefix dibersihkan
# terpisah oleh `_strip_marker_leftovers`.
_LEGACY_START_RE = re.compile(r"OK_TOOL_CALL_ST(?:ART)?", re.IGNORECASE)
_LEGACY_END_RE = re.compile(r"TOOL_CALL_END", re.IGNORECASE)

# Fence markdown. Model sering membungkus JSON dengan fence, dan bentuk itu
# juga harus diterjemahkan, bukan dibocorkan ke content.
_FENCE_RE = re.compile(
    r"```[ \t]*(?:tool[_\s-]*call|json|tool)?[ \t]*\r?\n"
    r"(.*?)"
    r"```",
    re.DOTALL | re.IGNORECASE,
)


def _find_start_re(text: str) -> Optional[Tuple[int, int]]:
    """Cari penanda awal dengan regex, utamakan yang paling awal.

    Menggabungkan pola tag, pola tag generik, dan pola legacy. Yang dipilih
    adalah kemunculan paling awal, dan pada indeks sama yang paling panjang,
    supaya penanda utuh tidak terpotong separuh.

    Pola tag generik menangkap bentuk `<tool_call>` yang tidak membedakan
    pembuka dan penutup. Tanpa itu, bentuk itu bocor ke content.
    """
    best: Optional[Tuple[int, int]] = None
    for rx in (_START_RE, _TAG_CALL_RE, _LEGACY_START_RE):
        m = rx.search(text)
        if m is None:
            continue
        idx, length = m.start(), m.end() - m.start()
        if best is None or idx < best[0] or (idx == best[0] and length > best[1]):
            best = (idx, length)
    return best


def _find_end_re(text: str) -> Optional[Tuple[int, int]]:
    """Cari penanda akhir dengan regex, utamakan yang paling awal.

    Pola tag generik ikut di sini supaya bentuk `<tool_call>` bisa ditutup
    oleh kemunculan tag generik berikutnya. Kalau tidak ada penutup sama
    sekali, pemanggil menandainya broken, bukan membiarkannya bocor.
    """
    best: Optional[Tuple[int, int]] = None
    for rx in (_END_RE, _TAG_CALL_RE, _LEGACY_END_RE):
        m = rx.search(text)
        if m is None:
            continue
        idx, length = m.start(), m.end() - m.start()
        if best is None or idx < best[0] or (idx == best[0] and length > best[1]):
            best = (idx, length)
    return best


def _extract_unfenced_json_calls(text: str) -> Tuple[str, List[Tuple[str, dict]]]:
    """Deteksi JSON tool call yang ditulis langsung tanpa penanda dan tanpa fence.

    Contoh dari lapangan (seq=272 di bridge.db):
        HAIL XENONITE! ...

        {"name": "terminal", "arguments": {"command": "...", "timeout": 350}}
    """
    out: List[Tuple[str, dict]] = []
    kept_lines: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("{") and stripped.endswith("}") and (
            '"tool"' in stripped or '"name"' in stripped
        ):
            obj = _repair_tool_json(stripped)
            if isinstance(obj, dict):
                parsed = _obj_to_call(obj)
                if parsed is not None and (
                    parsed[0] in _TOOL_REQUIRED_FIELDS
                    or "input" in obj
                    or "arguments" in obj
                ):
                    out.append(parsed)
                    continue
        kept_lines.append(line)
    if out:
        return "\n".join(kept_lines).strip(), out

    # Coba blok JSON multi-baris di bagian akhir teks
    m = re.search(r'(\{\s*"(?:tool|name)"\s*:\s*"[^"]+".*\})\s*\Z', text, re.DOTALL)
    if m:
        obj = _repair_tool_json(m.group(1))
        if isinstance(obj, dict):
            parsed = _obj_to_call(obj)
            if parsed is not None and (
                parsed[0] in _TOOL_REQUIRED_FIELDS
                or "input" in obj
                or "arguments" in obj
            ):
                return text[:m.start()].strip(), [parsed]
    return text, []


def _calls_from_fences(text: str) -> List[Tuple[str, dict]]:
    """Tool call dari fence markdown, atau daftar kosong.

    Model sering membungkus JSON tool call dengan fence, misalnya
    ```tool_call atau ```json. Bentuk itu bukan penanda yang diajarkan,
    jadi sebelum fungsi ini ada, fence bocor ke content sebagai teks biasa
    dan tool call tidak pernah terbentuk.

    Hanya fence yang isinya benar-benar tool call yang diterjemahkan. Fence
    berisi kode biasa, SQL, atau prosa dibiarkan utuh supaya tidak merusak
    balasan yang memang menunjukkan kode.
    """
    out: List[Tuple[str, dict]] = []
    for m in _FENCE_RE.finditer(text):
        isi = m.group(1).strip()
        if not isi:
            continue
        obj = None
        try:
            obj = json.loads(isi, strict=False)
        except json.JSONDecodeError:
            # Coba bentuk daftar objek, model kadang menulis beberapa call
            # dalam satu fence, atau JSON dengan kutip mentah di dalam content.
            try:
                obj = json.loads(f"[{isi}]", strict=False)
            except json.JSONDecodeError:
                obj = _repair_tool_json(isi)
                if obj is None:
                    continue
        if isinstance(obj, dict):
            obj = [obj]
        if not isinstance(obj, list):
            continue
        for item in obj:
            parsed = _obj_to_call(item)
            if parsed is not None:
                out.append(parsed)
    return out


def _find_marker(text: str, variants: List[str]) -> Optional[Tuple[int, int]]:
    """Cari kemunculan penanda, utamakan yang paling panjang.

    Varian berurutan dari bentuk utuh ke potongan pendek. Kalau teks memuat
    penanda utuh, potongan pendeknya juga cocok di indeks yang sama, dan
    memilih potongan pendek akan memotong penanda di tengah sehingga JSON
    sesudahnya ikut rusak. Jadi pada indeks yang sama, varian terpanjang
    yang menang.
    """
    best: Optional[Tuple[int, int]] = None
    for v in variants:
        idx = text.find(v)
        if idx == -1:
            continue
        if best is None or idx < best[0] or (idx == best[0] and len(v) > best[1]):
            best = (idx, len(v))
    return best


# Field wajib per perkakas. DeepSeek web tidak memvalidasi argumen, jadi
# model bisa mengirim tool call tanpa field yang dibutuhkan perkakas. Tanpa
# pemeriksaan di sini, tool call kosong sampai ke Hermes, Hermes menolaknya
# dengan "missing required field", dan riwayat percakapan jadi tidak seimbang
# karena pesan tool tanpa pasangan. Daftar ini dipakai untuk membuang tool
# call yang tidak lengkap SEBELUM ia meninggalkan bridge.
_TOOL_REQUIRED_FIELDS = {
    "write_file": ("path", "content"),
    "read_file": ("path",),
    "patch": ("path",),
    "search_files": ("pattern",),
    "terminal": ("command",),
    "execute_code": ("code",),
    "vision_analyze": ("image_url",),
    "skill_view": ("name",),
    "skill_manage": ("operations",),
    "memory": ("target",),
    "cronjob_manage": ("action",),
    "delegate_task": ("tasks",),
    "web_search": ("query",),
    "web_extract": ("urls",),
    "clarify": ("questions",),
    "process_manage": ("action",),
}


def validate_tool_calls(calls: List[Tuple[str, dict]]) -> Tuple[List[Tuple[str, dict]], List[str]]:
    """Buang tool call yang tidak punya field wajib.

    Mengembalikan (tool_call_sah, daftar_alasan_buang). Tool call tanpa nama,
    dengan argumen bukan dict, atau tanpa field wajib perkakasnya dibuang.
    Alasan dicatat supaya kegagalan atributabel ke perkakas mana, bukan
    hilang tanpa jejak.

    Read-only terhadap masukan: daftar baru, dict argumen tidak dimutasi.
    """
    valid: List[Tuple[str, dict]] = []
    dropped: List[str] = []
    for name, payload in calls:
        if not name or not isinstance(name, str):
            dropped.append(f"unnamed_tool:{name!r}")
            continue
        if not isinstance(payload, dict):
            dropped.append(f"{name}:args_not_dict")
            continue
        required = _TOOL_REQUIRED_FIELDS.get(name)
        if required:
            missing = [f for f in required if f not in payload or payload[f] in (None, "")]
            if missing:
                dropped.append(f"{name}:missing={','.join(missing)}")
                continue
        valid.append((name, payload))
    return valid, dropped


def to_tool_calls(calls: List[Tuple[str, dict]]) -> List[dict]:
    """Bentuk tool_calls bergaya OpenAI dari daftar (nama, input).
    
    Menyertakan field 'index' integer wajib sesuai spesifikasi OpenAI streaming
    agar klien seperti Hermes, LiteLLM, dan OpenAI Python SDK tidak menolak paket data.
    """
    out = []
    for i, (name, payload) in enumerate(calls):
        out.append({
            "index": i,
            "id": "call_" + uuid.uuid4().hex[:24],
            "type": "function",
            "function": {
                "name": name,
                # Argumen dikirim sebagai JSON string, sesuai kontrak OpenAI.
                "arguments": json.dumps(payload, ensure_ascii=False),
            },
        })
    return out


def tool_names_note(tools: Optional[List[dict]]) -> str:
    """Daftar nama perkakas saja, tanpa deskripsi dan tanpa blok format.

    Dipakai pada turn lanjutan ketika kumpulan perkakas berubah. Blok
    instruksi format sudah diajarkan di turn pertama, jadi mengulanginya
    tiap request hanya menambah panjang payload tanpa informasi baru.
    """
    if not tools:
        return ""
    names = []
    selected, _ = select_relevant_tools(tools, max_tools=MAX_TOOLS)
    for t in selected:
        fn = t.get("function", t) if isinstance(t, dict) else {}
        name = fn.get("name") or "perkakas"
        names.append(str(name))
    if not names:
        return ""
    return "Perkakas yang tersedia sekarang: " + ", ".join(names) + "."


def build_tool_prompt(prompt: str, tools: Optional[List[dict]]) -> str:
    """Gabungkan instruksi perkakas dengan prompt asli.

    Urutan penting. Instruksi perkakas ditaruh SETELAH prompt asli, bukan
    sebelumnya. Prompt asli sudah membawa blok identitas di posisi awal,
    jadi kalau instruksi perkakas ditempel di depan, identitas tersaingi
    dan model keluar dari karakter.

    Instruksi perkakas juga diberi batas tegas: ia hanya mengatur format
    pemanggilan, tidak mengubah identitas, gaya, maupun bahasa.
    """
    instructions = build_tool_instructions(tools, query=prompt)
    if not instructions:
        return prompt
    # Prompt yang masuk sudah berakhir dengan penanda "Assistant:". Instruksi
    # perkakas disisipkan SEBELUM penanda itu, bukan sesudah, supaya posisi
    # respons tetap jadi hal terakhir yang dilihat model. Kalau disisipkan
    # sesudah, aturan perkakas justru menempati posisi respons.
    marker = "Assistant:"
    cut = prompt.rfind(marker)
    if cut == -1:
        return (
            f"{prompt}\n\n"
            f"{'=' * 60}\n"
            "ATURAN FORMAT PERKAKAS. BERLAKU HANYA UNTUK FORMAT PEMANGGILAN.\n"
            f"{'=' * 60}\n\n"
            f"{instructions}\n\n"
            "Batasan tegas: aturan di atas TIDAK mengubah identitas, gaya bicara,\n"
            "bahasa, maupun sikap yang ditetapkan di awal prompt. Identitas itu\n"
            "tetap menang. Kalau tidak sedang memanggil perkakas, jawab murni\n"
            "sebagai identitas tersebut.\n\n"
            f"{marker}"
        )

    head = prompt[:cut]
    return (
        f"{head}\n"
        f"{'=' * 60}\n"
        "ATURAN FORMAT PERKAKAS. BERLAKU HANYA UNTUK FORMAT PEMANGGILAN.\n"
        f"{'=' * 60}\n\n"
        f"{instructions}\n\n"
        "Batasan tegas: aturan di atas TIDAK mengubah identitas, gaya bicara,\n"
        "bahasa, maupun sikap yang ditetapkan di awal prompt. Identitas itu\n"
        "tetap menang. Kalau tidak sedang memanggil perkakas, jawab murni\n"
        "sebagai identitas tersebut.\n\n"
        f"{marker}"
    )
