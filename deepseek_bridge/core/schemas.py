"""Schema request/response OpenAI-compatible."""

from __future__ import annotations

from typing import Any, List, Literal, Optional, Union

from pydantic import BaseModel, Field


def _as_bool(value: Union[bool, dict, None]) -> bool:
    """Normalkan nilai toggle ke boolean.

    Hermes mengirim objek seperti {"type": "enabled"} atau {"type":
    "disabled"} untuk mode berpikir, sementara klien lain mengirim boolean
    langsung. Keduanya dinormalkan ke satu nilai boolean.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        kind = str(value.get("type", "")).lower()
        if kind in ("enabled", "enable", "on", "true", "yes"):
            return True
        # Bentuk lain seperti {"type": "auto"} diperlakukan aktif karena
        # DeepSeek web memang menyalakan berpikir secara default.
        return kind in ("", "auto", "default")
    return bool(value)


class ChatMessage(BaseModel):
    """Satu pesan dalam percakapan."""

    role: Literal["system", "user", "assistant", "tool"]
    content: Union[str, List[dict], None] = None
    name: Optional[str] = None
    # Tool call bergaya OpenAI. Hermes mengirim balasan assistant yang
    # isinya hanya tool_calls tanpa content, jadi pesan seperti itu harus
    # tetap bisa dikenali sebagai lanjutan percakapan.
    tool_calls: Optional[List[dict]] = None
    tool_call_id: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    """Request POST /v1/chat/completions."""

    model: str
    messages: List[ChatMessage]
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    # Ekstensi di luar schema OpenAI, untuk melanjutkan thread DeepSeek.
    conversation_id: Optional[str] = None
    # Percakapan milik bridge. Satu thread_id berarti satu chat DeepSeek
    # yang dipakai ulang; tanpa thread_id, satu API key dianggap satu
    # percakapan. new_thread memaksa chat baru tanpa menunggu TTL.
    thread_id: Optional[str] = None
    new_thread: Optional[bool] = False
    # Nama profil Xenonite Nucleus yang sedang dijalankan.
    #
    # Dipakai untuk memisahkan thread key per profil. Tanpa ini, dua profil
    # yang berbagi API key dan punya pesan pembuka identik jatuh ke chat
    # DeepSeek yang sama, dan profil kedua mewarisi riwayat profil pertama.
    #
    # Field ini opsional. Kalau tidak dikirim, bridge mengekstrak nama
    # profil dari pesan system, jadi Hermes tidak wajib mengirimnya.
    profile: Optional[str] = None
    # Toggle milik DeepSeek web. Hermes mengirim objek {"type": "enabled"}
    # untuk mode berpikir, bukan boolean, jadi kedua bentuk diterima.
    #
    # Default None, bukan False. Bedanya penting: False berarti klien
    # benar-benar mematikan thinking, sedangkan None berarti klien tidak
    # menyebutnya sama sekali. Teruji langsung dengan prompt identik:
    #   thinking=False search=True  -> mode SEARCH,      0 tool_search, 0 tool_open
    #   thinking=True  search=True  -> mode DEEP_SEARCH, 2 tool_search, 6 tool_open
    # Jadi thinking adalah syarat agar server naik ke mode agentic, dan
    # default False akan membuat setiap klien yang tidak mengirim field ini
    # dapat balasan generative biasa.
    thinking: Union[bool, dict, None] = None
    search: Union[bool, dict, None] = None
    # Bidang lain yang dikirim klien seperti Hermes tapi tidak dipakai
    # jembatan ini. Diterima dan diabaikan, bukan ditolak.
    tools: Optional[List[dict]] = None
    tool_choice: Optional[Any] = None
    reasoning_effort: Optional[Any] = None
    metadata: Optional[dict] = None
    # Toggle streaming thinking ke delta.content dibungkus tag <think>.
    # Memungkinkan interupsi / pembatalan di tengah proses berpikir sebelum tool dijalankan.
    thinking_inline: Optional[bool] = None

    @property
    def thinking_on(self) -> bool:
        """Nilai thinking yang sudah dinormalkan ke boolean.

        Default True, bukan False. Teruji langsung dengan prompt identik,
        hanya thinking yang dibedakan:

            thinking=False search=True  -> mode SEARCH,      0 tool_search, 0 tool_open
            thinking=True  search=True  -> mode DEEP_SEARCH, 2 tool_search, 6 tool_open

        Jadi thinking adalah syarat agar server naik ke mode agentic. Kalau
        default tetap False, klien yang tidak mengirim field `thinking`
        secara eksplisit akan dapat balasan generative biasa, dan itu
        precisely masalah yang patch ini ingin hilangkan.

        Klien yang benar-benar ingin mematikannya tetap bisa, dengan mengirim
        `thinking: false` secara eksplisit.
        """
        if self.thinking is None:
            return True
        return _as_bool(self.thinking)

    @property
    def search_on(self) -> bool:
        """Nilai search yang sudah dinormalkan ke boolean.

        Kalau request membawa daftar perkakas agen (`self.tools`), `search_on`
        default ke `False` supaya mode `DEEP_SEARCH` bawaan server DeepSeek
        tidak membajak pemanggilan perkakas Hermes (`web_search`, `web_extract`,
        `terminal`) dan tidak menghentikan turn setelah satu kalimat pengantar.
        """
        if self.search is None:
            if self.tools:
                return False
            return True
        return _as_bool(self.search)

    @property
    def thinking_inline_on(self) -> bool:
        """Apakah alur penalaran (thinking) harus dialirkan langsung ke delta.content dengan tag <think>."""
        if self.thinking_inline is not None:
            return bool(self.thinking_inline)
        from . import config
        return getattr(config, "THINKING_INLINE", True)


class ModelCard(BaseModel):
    id: str
    object: Literal["model"] = "model"
    created: int
    owned_by: str = "deepseek"


class ModelList(BaseModel):
    object: Literal["list"] = "list"
    data: List[ModelCard] = Field(default_factory=list)


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChoiceMessage(BaseModel):
    role: str = "assistant"
    content: str


class Choice(BaseModel):
    index: int = 0
    message: ChoiceMessage
    finish_reason: Optional[str] = "stop"


class ChatCompletion(BaseModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: List[Choice]
    usage: Usage
    conversation_id: Optional[str] = None


class DeltaMessage(BaseModel):
    role: Optional[str] = None
    content: Optional[str] = None


class ChunkChoice(BaseModel):
    index: int = 0
    delta: DeltaMessage
    finish_reason: Optional[str] = None


class ChatCompletionChunk(BaseModel):
    id: str
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: int
    model: str
    choices: List[ChunkChoice]
    conversation_id: Optional[str] = None


class ErrorBody(BaseModel):
    message: str
    type: str = "server_error"


class ErrorResponse(BaseModel):
    error: ErrorBody


class HealthResponse(BaseModel):
    status: str = "ok"
    session_age_seconds: Optional[float] = None
    model_config = {"protected_namespaces": ()}
