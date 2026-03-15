#!/usr/bin/env python3
"""
aichaind.routing.control_intent - Intent-driven routing controls

Lightweight semantic parsing for session-level AIchain controls expressed in
free language. The parser is intentionally conservative and English-first:
  - the application language is English by default
  - language-specific hint packs are activated only when the user message
    strongly indicates another supported language
  - controls are session-scoped by default
  - the parser prefers known, runtime-confirmed targets
"""

from __future__ import annotations

from dataclasses import dataclass
import re


LANGUAGE_EN = "en"
LANGUAGE_SR = "sr"

_PROVIDER_MODEL_RE = re.compile(r"\b([a-z0-9_-]+/[a-z0-9_.:-]+)\b", re.IGNORECASE)
_SERBIAN_DIACRITICS_RE = re.compile(r"[čćžšđ]", re.IGNORECASE)
_STATIC_PROVIDER_PREFIXES = frozenset({
    "aichain",
    "anthropic",
    "cohere",
    "deepseek",
    "google",
    "groq",
    "llamacpp",
    "lmstudio",
    "local",
    "mistral",
    "moonshot",
    "ollama",
    "openai",
    "openai-codex",
    "openrouter",
    "vllm",
    "xai",
    "zhipu",
})

_LANGUAGE_HINTS = {
    LANGUAGE_EN: {
        "control_verbs": (
            "use", "switch", "lock", "unlock", "return", "set", "prefer",
            "from now on", "going forward",
        ),
        "model_lock_hints": (
            "use", "switch", "lock", "from now on", "going forward",
        ),
        "auto_hints": (
            "auto", "automatic", "default", "return to auto",
            "switch back to auto", "unlock",
        ),
        "max_intelligence_hints": (
            "max intelligence", "maximum intelligence", "best model",
            "strongest model", "highest quality", "best available",
        ),
        "min_cost_hints": (
            "prefer cheapest", "cheapest", "lowest cost", "save credits",
            "save money", "min cost", "minimum cost", "cheaper model",
        ),
        "prefer_local_hints": (
            "prefer local", "use local", "local first", "local only",
            "use my local model",
        ),
        "balanced_hints": (
            "balanced", "normal mode", "standard mode",
        ),
        "control_only_separators": (" and ", " then "),
        "language_markers": (
            "use", "switch", "lock", "unlock", "return", "prefer",
            "for this session", "auto routing", "cheapest", "balanced",
        ),
    },
    LANGUAGE_SR: {
        "control_verbs": (
            "koristi", "prebaci", "prebaci se", "predji", "predi", "vrati",
            "zakljucaj", "otkljucaj", "preferiraj", "biraj", "od sada", "ubuduce",
        ),
        "model_lock_hints": (
            "koristi", "prebaci", "predji", "predi", "zakljucaj", "od sada", "ubuduce",
        ),
        "auto_hints": (
            "automatski", "automatsko", "vrati auto", "vrati na auto", "otkljucaj",
            "vrati automatsko biranje", "vrati automatski rezim",
        ),
        "max_intelligence_hints": (
            "najjaci model", "najbolji model", "najvecu inteligenciju",
            "najveca inteligencija", "najpametniji model",
        ),
        "min_cost_hints": (
            "stedi", "najjeftiniji", "najmanji trosak", "jeftiniji model",
        ),
        "prefer_local_hints": (
            "lokalni model", "lokalni ai", "koristi lokalni", "samo lokalni",
            "prednost lokalnom",
        ),
        "balanced_hints": (
            "uravnotezeno", "regularno", "normalni rezim",
        ),
        "control_only_separators": (" pa ", " zatim ", " i "),
        "language_markers": (
            "koristi", "prebaci", "predji", "vrati", "zakljucaj", "otkljucaj",
            "od sada", "ubuduce", "najjeftiniji", "uravnotezeno",
        ),
    },
}


@dataclass
class SemanticControlIntent:
    mode: str = ""
    model: str = ""
    provider: str = ""
    routing_preference: str = ""
    persist_for_session: bool = True
    source: str = "semantic"
    control_only: bool = False
    stripped_prompt: str = ""
    confirmation: str = ""
    confidence: float = 0.0
    language: str = LANGUAGE_EN


def parse_semantic_control(
    messages: list[dict],
    *,
    roles: dict | None = None,
    provider_access_summary: dict | None = None,
) -> SemanticControlIntent | None:
    text = _last_user_text(messages)
    if not text:
        return None

    normalized = _normalize_text(text)
    language = _detect_control_language(text, normalized)
    hints = _active_hints(language)
    if not any(hint in normalized for hint in hints["control_verbs"]):
        return None

    aliases = _candidate_aliases(roles or {}, provider_access_summary or {})

    if _match_auto_mode(normalized, hints):
        return SemanticControlIntent(
            mode="auto",
            routing_preference="balanced",
            control_only=_is_control_only(normalized, stripped_prompt=""),
            stripped_prompt="",
            confirmation="AIchain returned to auto mode.",
            confidence=0.96,
            language=language,
        )

    manual_match = _match_manual_lock(text, normalized, aliases, hints)
    if manual_match:
        model_id, provider = manual_match
        stripped = _strip_after_first_separator(text, hints)
        return SemanticControlIntent(
            mode="manual",
            model=model_id,
            provider=provider,
            control_only=_is_control_only(normalized, stripped_prompt=stripped),
            stripped_prompt=stripped,
            confirmation=f"AIchain switched to manual lock: {_display_model_name(model_id)}.",
            confidence=0.95,
            language=language,
        )

    preference = _match_routing_preference(normalized, hints)
    if preference:
        stripped = _strip_after_first_separator(text, hints)
        return SemanticControlIntent(
            mode="auto",
            routing_preference=preference,
            control_only=_is_control_only(normalized, stripped_prompt=stripped),
            stripped_prompt=stripped,
            confirmation=_preference_confirmation(preference),
            confidence=0.90,
            language=language,
        )

    return None


def _last_user_text(messages: list[dict]) -> str:
    for msg in reversed(messages or []):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(str(part.get("text", "")).strip())
            return " ".join(part for part in parts if part).strip()
    return ""


def _normalize_text(text: str) -> str:
    lowered = (text or "").strip().lower()
    lowered = re.sub(r"[\"'`]+", " ", lowered)
    lowered = re.sub(r"[_/]+", " ", lowered)
    lowered = re.sub(r"[-:.]+", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered)
    return lowered.strip()


def _detect_control_language(text: str, normalized: str) -> str:
    if _SERBIAN_DIACRITICS_RE.search(text or ""):
        return LANGUAGE_SR

    english_markers = _LANGUAGE_HINTS[LANGUAGE_EN]["language_markers"]
    serbian_markers = _LANGUAGE_HINTS[LANGUAGE_SR]["language_markers"]
    english_score = sum(1 for marker in english_markers if marker in normalized)
    serbian_score = sum(1 for marker in serbian_markers if marker in normalized)

    if serbian_score > english_score and serbian_score >= 1:
        return LANGUAGE_SR
    return LANGUAGE_EN


def _active_hints(language: str) -> dict[str, tuple[str, ...]]:
    hints = {key: tuple(value) for key, value in _LANGUAGE_HINTS[LANGUAGE_EN].items()}
    if language == LANGUAGE_SR:
        for key, values in _LANGUAGE_HINTS[LANGUAGE_SR].items():
            hints[key] = tuple(list(hints.get(key, ())) + list(values))
    return hints


def _candidate_aliases(roles: dict, provider_access_summary: dict) -> dict[str, tuple[str, str]]:
    aliases: dict[str, tuple[str, str]] = {}

    def add(model_id: str, provider: str = ""):
        model_id = str(model_id or "").strip()
        provider = str(provider or "").strip().lower() or _infer_provider(model_id)
        if not model_id:
            return
        names = {model_id.lower()}
        leaf = model_id.split("/", 1)[-1].lower()
        names.add(leaf)
        names.add(_normalize_text(model_id))
        names.add(_normalize_text(leaf))
        names.add(_normalize_text(f"{provider} {leaf}"))
        if provider == "openai-codex" and "gpt-5.4" in model_id.lower():
            names.update({"gpt-5.4", "gpt 5 4", "gpt 5.4", "openai codex", "codex", "gpt 5 4 codex"})
        if provider == "deepseek" and "deepseek-chat" in leaf:
            names.update({"deepseek", "deepseek chat"})
        if provider in {"local", "lmstudio", "ollama", "vllm", "llamacpp"}:
            names.update({"local model", "local ai", "lokalni model", "lokalni ai"})
        for name in names:
            normalized = _normalize_text(name)
            if normalized:
                aliases[normalized] = (model_id, provider)

    for key in ("fast_brain", "heavy_brain", "visual_brain", "local_brain"):
        add(str((roles or {}).get(key, "") or ""))

    codex = provider_access_summary.get("openai-codex") or {}
    if codex.get("runtime_confirmed"):
        add("openai-codex/gpt-5.4", "openai-codex")

    return aliases


def _match_auto_mode(normalized: str, hints: dict[str, tuple[str, ...]]) -> bool:
    if not any(token in normalized for token in ("auto", "automatic", "automats", "default", "unlock", "otkljuc")):
        return False
    return any(hint in normalized for hint in hints["auto_hints"])


def _match_manual_lock(
    raw_text: str,
    normalized: str,
    aliases: dict[str, tuple[str, str]],
    hints: dict[str, tuple[str, ...]],
) -> tuple[str, str] | None:
    explicit = _PROVIDER_MODEL_RE.search(raw_text or "")
    if explicit and any(hint in normalized for hint in hints["model_lock_hints"]):
        model_id = explicit.group(1).lower()
        provider = _infer_provider(model_id)
        if _is_known_manual_target(model_id, provider, aliases):
            return model_id, provider

    if not any(hint in normalized for hint in hints["model_lock_hints"]):
        return None

    for alias in sorted(aliases.keys(), key=len, reverse=True):
        if alias and alias in normalized:
            return aliases[alias]
    return None


def _match_routing_preference(normalized: str, hints: dict[str, tuple[str, ...]]) -> str:
    if any(hint in normalized for hint in hints["max_intelligence_hints"]):
        return "max_intelligence"
    if any(hint in normalized for hint in hints["min_cost_hints"]):
        return "min_cost"
    if any(hint in normalized for hint in hints["prefer_local_hints"]):
        return "prefer_local"
    if any(hint in normalized for hint in hints["balanced_hints"]):
        return "balanced"
    return ""


def _strip_after_first_separator(text: str, hints: dict[str, tuple[str, ...]]) -> str:
    lowered = (text or "").lower()
    match_index = -1
    match_token = ""
    for token in hints["control_only_separators"]:
        idx = lowered.find(token)
        if idx != -1 and (match_index == -1 or idx < match_index):
            match_index = idx
            match_token = token
    if match_index == -1:
        return ""
    remainder = text[match_index + len(match_token):].strip(" ,.;:-")
    return remainder


def _is_control_only(normalized: str, stripped_prompt: str) -> bool:
    if stripped_prompt:
        return False
    word_count = len((normalized or "").split())
    return word_count <= 18


def _display_model_name(model_id: str) -> str:
    if not model_id:
        return ""
    leaf = model_id.split("/", 1)[-1]
    if leaf.lower().startswith("gpt-"):
        return leaf.upper()
    return leaf


def _preference_confirmation(preference: str) -> str:
    mapping = {
        "balanced": "AIchain returned to balanced auto routing.",
        "max_intelligence": "AIchain now prefers maximum intelligence in auto mode.",
        "min_cost": "AIchain now prefers the lowest effective cost in auto mode.",
        "prefer_local": "AIchain now prefers local models when they are viable.",
    }
    return mapping.get(preference, "AIchain routing preference updated.")


def _infer_provider(model_id: str) -> str:
    return str(model_id or "").split("/", 1)[0].strip().lower()


def _is_known_manual_target(model_id: str, provider: str, aliases: dict[str, tuple[str, str]]) -> bool:
    normalized_model = str(model_id or "").strip().lower()
    normalized_provider = str(provider or "").strip().lower()
    if not normalized_model or not normalized_provider:
        return False
    if "\\" in normalized_model or normalized_model.startswith(("users/", "windows/", "program files/")):
        return False
    if normalized_provider in _STATIC_PROVIDER_PREFIXES:
        return True
    for alias_model, alias_provider in aliases.values():
        if normalized_model == str(alias_model or "").strip().lower():
            return True
        if normalized_provider == str(alias_provider or "").strip().lower():
            return True
    return False

