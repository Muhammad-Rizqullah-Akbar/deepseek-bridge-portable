"""Proof-of-Work solver untuk endpoint completion DeepSeek.

DeepSeek menggerbangi `POST /api/v0/chat/completion` dengan header
`x-ds-pow-response`. Algoritma PoW ("DeepSeekHashV1") dikirim sebagai modul
WebAssembly yang di-load website dari CDN-nya sendiri. Daripada
mengimplementasikan ulang logika hashing float64-nya, kita jalankan modul
asli DeepSeek, kode yang sama dengan yang dijalankan browser, di dalam
sandbox wasmtime (tanpa akses file maupun jaringan).

Alur:
    1. POST /api/v0/chat/create_pow_challenge  -> challenge dict
    2. solver.make_header(challenge)            -> nilai header base64
    3. POST /api/v0/chat/completion             dengan header tersebut
"""

from __future__ import annotations

import base64
import json
import struct
import threading
from pathlib import Path
from typing import Optional

import wasmtime

WASM_PATH = Path(__file__).resolve().parent / "sha3_wasm_bg.wasm"


class PowError(RuntimeError):
    """Gagal menyelesaikan challenge PoW."""


class DeepSeekPow:
    """Menjalankan modul WASM PoW milik DeepSeek di dalam sandbox wasmtime."""

    def __init__(self, wasm_path: Path = WASM_PATH):
        if not wasm_path.exists():
            raise PowError(f"Modul WASM PoW tidak ditemukan: {wasm_path}")
        self._store = wasmtime.Store()
        module = wasmtime.Module.from_file(self._store.engine, str(wasm_path))
        self._inst = wasmtime.Instance(self._store, module, [])
        exp = self._inst.exports(self._store)
        self._memory: wasmtime.Memory = exp["memory"]
        self._solve = exp["wasm_solve"]
        self._malloc = exp["__wbindgen_export_0"]
        self._add_to_stack = exp["__wbindgen_add_to_stack_pointer"]
        # Store wasmtime tidak reentrant, jadi akses diserialisasi.
        self._lock = threading.Lock()

    def _write_str(self, text: str) -> tuple[int, int]:
        """Salin string UTF-8 ke memori WASM, kembalikan (ptr, len)."""
        data = text.encode("utf-8")
        ptr = self._malloc(self._store, len(data), 1)
        base = self._memory.data_ptr(self._store)
        for i, b in enumerate(data):
            base[ptr + i] = b
        return ptr, len(data)

    def _solve_once(self, challenge: str, prefix: str, difficulty: float) -> Optional[int]:
        """Panggilan wasm-bindgen tingkat rendah.

        wasm_solve(retptr, challenge_ptr, challenge_len,
                   prefix_ptr, prefix_len, difficulty)

        Slot return 16 byte di shadow stack: status i32 di +0, jawaban f64 di +8.
        """
        retptr = self._add_to_stack(self._store, -16)
        try:
            c_ptr, c_len = self._write_str(challenge)
            p_ptr, p_len = self._write_str(prefix)
            self._solve(self._store, retptr, c_ptr, c_len, p_ptr, p_len, float(difficulty))

            mem = self._memory.data_ptr(self._store)
            status = struct.unpack("<i", bytes(mem[retptr:retptr + 4]))[0]
            value = struct.unpack("<d", bytes(mem[retptr + 8:retptr + 16]))[0]
        finally:
            self._add_to_stack(self._store, 16)

        if status == 0:
            return None
        return int(value)

    def make_header(self, challenge: dict) -> str:
        """Bangun nilai header `x-ds-pow-response` (base64 JSON) dari challenge."""
        prefix = f"{challenge['salt']}_{challenge['expire_at']}_"
        with self._lock:
            answer = self._solve_once(
                challenge["challenge"], prefix, challenge["difficulty"]
            )
        if answer is None:
            raise PowError("Solver PoW tidak mengembalikan jawaban (challenge kedaluwarsa?)")

        payload = {
            "algorithm": challenge["algorithm"],
            "challenge": challenge["challenge"],
            "salt": challenge["salt"],
            "answer": answer,
            "signature": challenge["signature"],
            "target_path": challenge["target_path"],
        }
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return base64.b64encode(raw).decode("utf-8")
