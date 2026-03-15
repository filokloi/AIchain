#!/usr/bin/env python3
"""Heuristic prompt-injection guard for local pre-routing screening."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


_HIGH_RISK_PATTERNS = {
    "ignore_previous_instructions": re.compile(r"ignore (all )?(previous|prior|above) instructions", re.IGNORECASE),
    "developer_mode": re.compile(r"developer mode|system override|jailbreak", re.IGNORECASE),
    "reveal_system_prompt": re.compile(r"reveal (the )?(system|hidden) prompt|show your system prompt", re.IGNORECASE),
    "secret_exfiltration": re.compile(r"print .*api key|show .*secret|exfiltrate|dump .*token", re.IGNORECASE),
}

_MEDIUM_RISK_PATTERNS = {
    "tool_override": re.compile(r"call the tool|use the tool|run the command|execute this command", re.IGNORECASE),
    "policy_bypass": re.compile(r"bypass safety|disable guard|do not follow policy", re.IGNORECASE),
    "role_spoofing": re.compile(r"you are now the system|pretend to be the developer|act as root", re.IGNORECASE),
}


@dataclass
class InjectionScanResult:
    blocked: bool = False
    risk: str = "low"
    score: int = 0
    matches: list[str] = field(default_factory=list)
    reason: str = ""


class PromptInjectionGuard:
    """Simple local defense-in-depth screen for obvious prompt-injection attempts."""

    def scan_text(self, text: str) -> InjectionScanResult:
        matches: list[str] = []
        score = 0

        for name, pattern in _HIGH_RISK_PATTERNS.items():
            if pattern.search(text or ""):
                matches.append(name)
                score += 3

        for name, pattern in _MEDIUM_RISK_PATTERNS.items():
            if pattern.search(text or ""):
                matches.append(name)
                score += 1

        if score >= 3:
            return InjectionScanResult(
                blocked=True,
                risk="high",
                score=score,
                matches=matches,
                reason="prompt_injection_high_risk",
            )
        if score >= 1:
            return InjectionScanResult(
                blocked=False,
                risk="medium",
                score=score,
                matches=matches,
                reason="prompt_injection_suspected",
            )
        return InjectionScanResult(blocked=False, risk="low", score=0, matches=[])

    def scan_messages(self, messages: list[dict]) -> InjectionScanResult:
        parts: list[str] = []
        for msg in messages:
            role = (msg.get("role") or "").lower()
            if role and role not in {"user"}:
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        parts.append(part.get("text", ""))
        return self.scan_text("\n".join(parts))
