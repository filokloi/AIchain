#!/usr/bin/env python3
"""
aichaind.compression.lingua — LLMLingua-2 prompt compression (OPTIMIZATIONS §2).

Task-agnostic token-classification compression, running locally (small
encoder). OFF by default; requires the optional `llmlingua` package. Without
it the compressor reports unavailable and every call is a no-op — the
existing structured summarizer remains the fallback path.

Insertion points (all optional):
  1. handoff summaries on model switch (DYNAMIC_AUTO §3.3),
  2. economy-mode long chats (compress turns older than the last k),
  3. fitting a better model whose context window is slightly too small.
This module implements the guardrailed message-level compressor; call sites
choose when to invoke it.

Guardrails (never compressed): system prompts, tool schemas / tool turns,
messages containing code fences, the last `keep_last_turns` turns, and any
request carrying the `@aichain nocompress` override.

MIT License.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

log = logging.getLogger("aichaind.compression.lingua")

NOCOMPRESS_MARKER = "@aichain nocompress"


class LinguaCompressor:
    def __init__(self, cfg: dict | None = None,
                 _compress_fn: Optional[Callable[[str, float], str]] = None):
        cfg = cfg or {}
        self.enabled = bool(cfg.get("enabled", False))       # OFF by default
        self.ratio = float(cfg.get("ratio", 0.5))             # conservative 2x
        self.keep_last_turns = int(cfg.get("keep_last_turns", 4))
        self.min_chars = int(cfg.get("min_chars", 600))       # not worth it below
        self._compress_fn = _compress_fn                      # test injection
        self._model = None
        self._import_failed = False

    # ---------------------------------------------------------------- infra

    @property
    def available(self) -> bool:
        if self._compress_fn is not None:
            return True
        if self._import_failed:
            return False
        try:
            import llmlingua  # noqa: F401
            return True
        except ImportError:
            self._import_failed = True
            return False

    def _compress_text(self, text: str) -> str:
        if self._compress_fn is not None:
            return self._compress_fn(text, self.ratio)
        if self._model is None:
            from llmlingua import PromptCompressor
            self._model = PromptCompressor(
                model_name="microsoft/llmlingua-2-xlm-roberta-large-meetingbank",
                use_llmlingua2=True,
            )
        result = self._model.compress_prompt(text, rate=self.ratio)
        return result.get("compressed_prompt", text)

    # ---------------------------------------------------------------- policy

    @staticmethod
    def _protected(msg: dict) -> bool:
        if msg.get("role") in ("system", "tool"):
            return True
        if msg.get("tool_calls") or msg.get("tool_call_id"):
            return True
        content = msg.get("content")
        if not isinstance(content, str):
            return True   # multi-part (images etc.) — leave alone
        if "```" in content:
            return True   # never compress code
        return False

    def compress_messages(self, messages: list[dict]) -> tuple[list[dict], dict]:
        """Compress old, unprotected turns. Returns (messages, meta)."""
        meta = {"lingua_compressed": False, "chars_saved": 0, "ratio": self.ratio,
                "turns_compressed": 0, "skipped_reason": ""}
        if not self.enabled:
            meta["skipped_reason"] = "disabled"
            return messages, meta
        if not self.available:
            meta["skipped_reason"] = "llmlingua_not_installed"
            return messages, meta
        if any(isinstance(m.get("content"), str) and NOCOMPRESS_MARKER in m["content"].lower()
               for m in messages):
            meta["skipped_reason"] = "nocompress_override"
            return messages, meta

        cutoff = max(0, len(messages) - self.keep_last_turns)
        out, saved, compressed_turns = [], 0, 0
        for i, msg in enumerate(messages):
            if i >= cutoff or self._protected(msg) or len(msg.get("content") or "") < self.min_chars:
                out.append(msg)
                continue
            try:
                short = self._compress_text(msg["content"])
            except Exception as e:
                log.warning(f"lingua compression failed, keeping verbatim: {e}")
                out.append(msg)
                continue
            if short and len(short) < len(msg["content"]):
                saved += len(msg["content"]) - len(short)
                compressed_turns += 1
                out.append({**msg, "content": short})
            else:
                out.append(msg)

        meta.update({"lingua_compressed": compressed_turns > 0,
                     "chars_saved": saved, "turns_compressed": compressed_turns})
        return out, meta

    def can_fit_by_compression(self, transcript_tokens: int, context_window: int,
                               overhead_tokens: int = 2048) -> bool:
        """Insertion point 3: would compressing old turns plausibly fit this
        transcript into `context_window`? Conservative: assumes only the
        compressible fraction (~70% of transcript) shrinks by `ratio`."""
        if not (self.enabled and self.available):
            return False
        compressible = transcript_tokens * 0.7
        projected = transcript_tokens - compressible * (1.0 - self.ratio)
        return projected + overhead_tokens <= context_window
