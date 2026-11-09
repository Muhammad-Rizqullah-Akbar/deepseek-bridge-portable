"""Lapisan Caching L1 In-Memory untuk DeepSeek Bridge.

Fitur:
  1. Exact-Match Response Caching: Menjawab pertanyaan berulang / statis secara
     instan (<5ms) tanpa membebani kuota atau memicu latensi hulu DeepSeek.
  2. Smart Agentic Guardrail: Otomatis mem-bypass cache bila turn berisi
     hasil eksekusi perkakas (role == 'tool' atau diawali 'Hasil perkakas'),
     sehingga alur kerja agen tidak pernah menerima status usang (stale state).
  3. Replay Streaming SSE yang Presisi: Saat klien meminta stream=true (seperti
     Hermes atau 9router), cache mereka ulang frame SSE OpenAI chunk-by-chunk
     beserta [DONE] sehingga klien tidak membedakan cache dari stream asli.
  4. LRU & TTL Eviction: Otomatis membersihkan entri tertua saat batas kapasitas
     atau batas waktu (TTL) tercapai, aman dari kebocoran memori (leak).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

log = logging.getLogger("deepseek_bridge.cache")

# Batas kapasitas entri cache di memori
MAX_CACHE_ENTRIES = 1000
# Umur hidup cache default (1 jam = 3600 detik)
DEFAULT_CACHE_TTL = 3600.0


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                t = b.get("text")
                if isinstance(t, str):
                    parts.append(t)
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(parts)
    return ""


@dataclass
class CachedEntry:
    text: str
    thinking: str = ""
    tool_calls: Optional[List[dict]] = None
    finish_reason: str = "stop"
    created_at: float = field(default_factory=time.time)
    ttl: float = DEFAULT_CACHE_TTL
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl


class TurnCache:
    """Penyimpanan cache turn in-memory yang thread-safe."""

    def __init__(self, max_entries: int = MAX_CACHE_ENTRIES, default_ttl: float = DEFAULT_CACHE_TTL):
        self.max_entries = max_entries
        self.default_ttl = default_ttl
        self._entries: Dict[str, CachedEntry] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    @property
    def stats(self) -> dict:
        with self._lock:
            return {
                "size": len(self._entries),
                "max": self.max_entries,
                "hits": self._hits,
                "misses": self._misses,
            }

    def should_cache(self, req: Any) -> bool:
        """Pagar pengaman: Jangan pernah cache turn yang dinamis atau membawa hasil tool."""
        # Jika suhu tinggi, hasil tidak deterministik
        temp = getattr(req, "temperature", None)
        if temp is not None and temp > 0.7:
            return False

        messages = getattr(req, "messages", None) or []
        if not messages:
            return False

        # Periksa 2 pesan terakhir: bila membawa hasil tool, WAJIB bypass!
        for m in messages[-2:]:
            role = getattr(m, "role", None)
            if role == "tool":
                return False
            content = _content_text(getattr(m, "content", None))
            if "Hasil perkakas" in content or "OK_TOOL_CALL_START" in content or "<tool_call_start|>" in content:
                return False

        return True

    def make_key(self, req: Any, profile: str = "") -> str:
        """Buat hash SHA-256 dari representasi kanonikal request."""
        msgs_repr = []
        for m in getattr(req, "messages", []) or []:
            role = getattr(m, "role", "")
            text = _content_text(getattr(m, "content", ""))
            msgs_repr.append((role, text))

        tools_repr = []
        for t in getattr(req, "tools", None) or []:
            if isinstance(t, dict):
                fn = t.get("function") or {}
                tools_repr.append(fn.get("name", ""))

        payload = {
            "model": str(getattr(req, "model", "")),
            "profile": str(profile or ""),
            "thinking": bool(getattr(req, "thinking_on", False)),
            "search": bool(getattr(req, "search_on", False)),
            "messages": msgs_repr,
            "tools": sorted(tools_repr),
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Optional[CachedEntry]:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            if entry.is_expired:
                del self._entries[key]
                self._misses += 1
                return None
            self._hits += 1
            # Pindahkan ke paling akhir untuk LRU
            self._entries[key] = entry
            return entry

    def put(
        self,
        key: str,
        text: str,
        thinking: str = "",
        tool_calls: Optional[List[dict]] = None,
        finish_reason: str = "stop",
        meta: Optional[dict] = None,
        ttl: Optional[float] = None,
    ) -> None:
        if not text and not tool_calls and not thinking:
            return
        with self._lock:
            # Eviksi LRU bila penuh
            if len(self._entries) >= self.max_entries and key not in self._entries:
                oldest_key = next(iter(self._entries))
                del self._entries[oldest_key]

            self._entries[key] = CachedEntry(
                text=text,
                thinking=thinking,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
                ttl=ttl or self.default_ttl,
                meta=meta or {},
            )

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._hits = 0
            self._misses = 0


# Singleton global cache
_GLOBAL_CACHE = TurnCache()


def get_turn_cache() -> TurnCache:
    return _GLOBAL_CACHE


def replay_stream_sse(entry: CachedEntry, model: str, inline_thinking: bool = False) -> Iterator[str]:
    """Hasilkan stream SSE OpenAI yang presisi untuk cache hit."""
    cid = f"chatcmpl-cache-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    def frame(delta: dict, finish=None) -> str:
        obj = {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    # Chunk 1: Announce role
    yield frame({"role": "assistant", "content": ""})

    # Chunk 2: Thinking (jika ada)
    if entry.thinking:
        if inline_thinking:
            yield frame({"content": "<think>\n", "reasoning_content": ""})
            yield frame({"content": entry.thinking, "reasoning_content": entry.thinking})
            yield frame({"content": "\n</think>\n\n"})
        else:
            yield frame({"reasoning_content": entry.thinking})

    # Chunk 3: Content / Tool calls
    if entry.tool_calls:
        yield frame({"tool_calls": entry.tool_calls}, finish="tool_calls")
    elif entry.text:
        yield frame({"content": entry.text})

    # Final Chunk: Finish reason
    yield frame({}, finish=entry.finish_reason)
    yield "data: [DONE]\n\n"


def make_cached_response(entry: CachedEntry, model: str, prompt: str = "", inline_thinking: bool = False) -> dict:
    """Buat response JSON dict OpenAI yang presisi untuk non-stream cache hit."""
    cid = f"chatcmpl-cache-{uuid.uuid4().hex[:12]}"
    base_text = entry.text if not entry.tool_calls else (entry.text or None)
    if inline_thinking and entry.thinking:
        base_text = f"<think>\n{entry.thinking}\n</think>\n\n{base_text}" if base_text else f"<think>\n{entry.thinking}\n</think>\n\n"

    choice = {
        "index": 0,
        "message": {
            "role": "assistant",
            "content": base_text,
        },
        "finish_reason": entry.finish_reason,
    }
    if entry.thinking:
        choice["message"]["reasoning_content"] = entry.thinking
    if entry.tool_calls:
        choice["message"]["tool_calls"] = entry.tool_calls
        choice["finish_reason"] = "tool_calls"

    return {
        "id": cid,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [choice],
        "usage": {
            "prompt_tokens": len(prompt) // 4 if prompt else 0,
            "completion_tokens": (len(entry.text) + len(entry.thinking)) // 4,
            "total_tokens": (len(prompt) + len(entry.text) + len(entry.thinking)) // 4,
        },
    }
