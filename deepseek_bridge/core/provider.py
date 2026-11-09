"""Adapter DeepSeek: membungkus bridge yang sudah ada agar memenuhi kontrak.

Modul ini tidak menulis ulang apa pun. Ia hanya membungkus
deepseek_bridge.client.DeepSeekClient, supaya bentuknya sesuai
BridgeProvider yang diminta webmodel_core.

Alasan adapter dipisah dari bridge asli: bridge DeepSeek sudah terbukti
jalan dan teruji. Mengubahnya agar memenuhi kontrak baru berisiko
merusak sesuatu yang sudah baik. Adapter menambah lapis tipis di atas,
dan kalau ada masalah, lapis itu bisa dilepas tanpa menyentuh bridge.

Catatan jujur: adapter ini mengasumsikan deepseek_bridge sudah terinstal
dan bisa diimpor. Kalau tidak, registry mencatat warning dan provider
lain tetap jalan.
"""

from __future__ import annotations

import logging
from typing import Iterator, List, Optional

from webmodel_core.contracts import (
    BridgeError, BridgeProvider, ModelInfo, Reply)

log = logging.getLogger("deepseek_bridge.provider")


class DeepSeekProvider(BridgeProvider):
    """Provider DeepSeek, membungkus deepseek_bridge.client."""

    provider_id = "deepseek"
    display_name = "DeepSeek Bridge"
    # Model DeepSeek selalu diawali "deepseek", jadi prefix provider_id
    # sudah cukup. Daftar eksplisit tetap diisi supaya nanti kalau OpenAI
    # mengganti penamaan, tempat perbaikannya jelas.
    model_prefixes = ("deepseek",)

    def __init__(self) -> None:
        self._client = None

    # --- siklus klien ------------------------------------------------------

    def _get_client(self):
        """Klien dibuat malas, supaya impor modul ini tidak memaksa sesi.

        Kalau sesi DeepSeek belum ada, kegagalan terjadi saat chat()
        dipanggil, bukan saat server start. Itu yang diinginkan: server
        harus tetap bisa mulai dan melaporkan provider mana yang bermasalah.
        """
        if self._client is None:
            from deepseek_bridge.auth import get_session
            from deepseek_bridge.client import DeepSeekClient
            self._client = DeepSeekClient(get_session())
        return self._client

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    # --- kontrak -----------------------------------------------------------

    def models(self) -> List[ModelInfo]:
        from deepseek_bridge import config
        out = []
        for mid in config.PUBLISHED_MODELS:
            cap = config.MODEL_CAPABILITIES.get(mid, {})
            out.append(ModelInfo(
                id=mid,
                name=mid,
                context_length=config.CONTEXT_LENGTH,
                max_output_tokens=config.MAX_OUTPUT_TOKENS,
                capabilities=dict(cap),
            ))
        return out

    def resolve_model(self, model: str) -> str:
        """Petakan nama model klien ke model_type wire DeepSeek.

        DeepSeek web memakai "default" dan "expert", bukan nama model
        OpenAI-style. Pemetaan ada di deepseek_bridge.config.MODEL_MAP.
        """
        from deepseek_bridge import config
        return config.resolve_model_type(model)

    def chat(self, prompt: str, conversation_id: Optional[str] = None,
             parent_message_id: Optional[str] = None,
             model: Optional[str] = None,
             thinking: bool = False,
             search: bool = False) -> Reply:
        try:
            r = self._get_client().chat(
                prompt,
                conversation_id=conversation_id,
                model=self.resolve_model(model) if model else None,
                thinking=thinking,
                search=search,
            )
        except Exception as e:
            raise self._wrap_error(e) from e
        return Reply(text=r.text, conversation_id=r.conversation_id,
                     thinking=getattr(r, "thinking", "") or "")

    def stream(self, prompt: str, conversation_id: Optional[str] = None,
               parent_message_id: Optional[str] = None,
               model: Optional[str] = None,
               thinking: bool = False,
               search: bool = False) -> Iterator:
        try:
            s = self._get_client().stream(
                prompt,
                conversation_id=conversation_id,
                model=self.resolve_model(model) if model else None,
                thinking=thinking,
                search=search,
            )
        except Exception as e:
            raise self._wrap_error(e) from e

        def _gen():
            try:
                for kind, text in s:
                    yield kind, text
            except Exception as e:
                raise self._wrap_error(e) from e
        return _gen()

    # --- pembantu ----------------------------------------------------------

    def _wrap_error(self, e: Exception) -> BridgeError:
        """Ubah error DeepSeek jadi BridgeError dengan kode yang berguna.

        Kode asli dipertahankan supaya server generik bisa memutuskan
        apakah error layak diulang tanpa perlu tahu nama provider.
        """
        from deepseek_bridge.client import DeepSeekError
        if isinstance(e, DeepSeekError):
            fatal = bool(getattr(e, "is_invalid_token", False)) or \
                bool(getattr(e, "is_muted", False))
            return BridgeError(
                str(e), status=getattr(e, "status", 0) or 0,
                transient=bool(getattr(e, "transient", False)),
                truncated=bool(getattr(e, "truncated", False)),
                fatal=fatal)
        return BridgeError(str(e))


PROVIDER = DeepSeekProvider()
