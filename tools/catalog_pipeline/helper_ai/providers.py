from __future__ import annotations

import json
from typing import Any

import requests

from ..constants import (
    DEFAULT_GEMINI_HELPER_MODEL,
    DEFAULT_GROQ_HELPER_MODEL,
    DEFAULT_HELPER_TIMEOUT_SECONDS,
)
from ..types import HelperCallResult


class BaseHelperProvider:
    name = "base"

    def __init__(self, api_key: str | None) -> None:
        self.api_key = api_key or ""

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def call_json(self, prompt: str) -> HelperCallResult:
        raise NotImplementedError

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any] | list[Any]:
        cleaned = text.strip().replace("```json", "").replace("```", "").strip()
        return json.loads(cleaned)


class GeminiHelperProvider(BaseHelperProvider):
    name = "gemini"

    def call_json(self, prompt: str) -> HelperCallResult:
        if not self.available:
            return HelperCallResult(provider=self.name, ok=False, error="missing api key")
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{DEFAULT_GEMINI_HELPER_MODEL}:generateContent?key={self.api_key}"
        )
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        try:
            response = requests.post(url, json=payload, timeout=DEFAULT_HELPER_TIMEOUT_SECONDS)
            response.raise_for_status()
            data = response.json()
            text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "{}")
            return HelperCallResult(provider=self.name, ok=True, payload=self._parse_json(text))
        except Exception as exc:  # pragma: no cover - network behavior mocked in tests
            return HelperCallResult(provider=self.name, ok=False, error=str(exc))


class GroqHelperProvider(BaseHelperProvider):
    name = "groq"

    def call_json(self, prompt: str) -> HelperCallResult:
        if not self.available:
            return HelperCallResult(provider=self.name, ok=False, error="missing api key")
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": DEFAULT_GROQ_HELPER_MODEL,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "Return valid JSON only."},
                {"role": "user", "content": prompt},
            ],
        }
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=DEFAULT_HELPER_TIMEOUT_SECONDS)
            response.raise_for_status()
            data = response.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "{}")
            return HelperCallResult(provider=self.name, ok=True, payload=self._parse_json(text))
        except Exception as exc:  # pragma: no cover - network behavior mocked in tests
            return HelperCallResult(provider=self.name, ok=False, error=str(exc))
