#!/usr/bin/env python3
"""
aichaind.providers.balance — Provider Balance Checker

Checks credit/balance on each AI provider API.
Used by the cost optimizer to make real-time routing decisions.

Supported providers:
  - OpenRouter: GET /api/v1/credits
  - OpenAI: estimated from key/project type
  - DeepSeek: GET /user/balance
  - Anthropic: estimated from key
  - Google: free tier detection via key format
  - Groq: free tier detection
"""

import time
import logging
from dataclasses import dataclass, field

log = logging.getLogger("aichaind.providers.balance")

# Cache balance checks for 5 minutes
BALANCE_CACHE_TTL = 300


@dataclass
class ProviderBalance:
    """Balance info for a single provider."""
    provider: str = ""
    balance_usd: float = -1.0     # -1 = unknown
    has_credits: bool = True       # assume yes unless proven no
    is_free_tier: bool = False
    is_subscription: bool = False  # paid plan or already-covered access path
    rate_limited: bool = False
    error: str = ""
    checked_at: float = 0.0
    source: str = ""               # "api", "cached", "estimated", "error"


@dataclass
class BalanceReport:
    """Full balance report across all providers."""
    balances: dict[str, ProviderBalance] = field(default_factory=dict)
    total_available_usd: float = 0.0
    providers_with_credits: list[str] = field(default_factory=list)
    providers_empty: list[str] = field(default_factory=list)
    checked_at: float = 0.0


class BalanceChecker:
    """
    Checks balance/credits across all configured AI providers.
    Results are cached to avoid excessive API calls.
    """

    def __init__(self, cache_ttl: int = BALANCE_CACHE_TTL):
        self.cache_ttl = cache_ttl
        self._cache: dict[str, ProviderBalance] = {}

    def check_all(self, credentials: list) -> BalanceReport:
        """Check balances for all discovered providers."""
        report = BalanceReport(checked_at=time.time())

        for cred in credentials:
            bal = self._check_one(cred.provider, cred.api_key)
            report.balances[cred.provider] = bal

            if bal.balance_usd > 0 or bal.has_credits:
                report.providers_with_credits.append(cred.provider)
                if bal.balance_usd > 0:
                    report.total_available_usd += bal.balance_usd
            elif bal.balance_usd == 0 and not bal.is_free_tier:
                report.providers_empty.append(cred.provider)

        return report

    def _check_one(self, provider: str, api_key: str) -> ProviderBalance:
        """Check balance for a single provider."""
        cached = self._cache.get(provider)
        if cached and (time.time() - cached.checked_at) < self.cache_ttl:
            cached.source = "cached"
            return cached

        checker = self._CHECKERS.get(provider)
        try:
            if checker is None:
                result = self._check_unknown(api_key)
            else:
                result = checker(self, api_key)
            result.provider = provider
            result.checked_at = time.time()
            self._cache[provider] = result
            return result
        except Exception as e:
            log.warning(f"Balance check failed for {provider}: {e}")
            return ProviderBalance(
                provider=provider,
                has_credits=True,
                error=str(e),
                checked_at=time.time(),
                source="error",
            )

    def _check_openrouter(self, api_key: str) -> ProviderBalance:
        """OpenRouter: GET /api/v1/credits"""
        import requests

        r = requests.get(
            "https://openrouter.ai/api/v1/credits",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=5,
        )
        if r.status_code == 200:
            data = r.json().get("data", {})
            balance = float(data.get("total_credits", 0)) - float(data.get("total_usage", 0))
            return ProviderBalance(
                balance_usd=round(max(balance, 0), 4),
                has_credits=balance > 0.001,
                source="api",
            )
        return ProviderBalance(error=f"HTTP {r.status_code}", source="api")

    def _check_openai(self, api_key: str) -> ProviderBalance:
        """OpenAI: project keys often imply configured billing, but not guaranteed quota."""
        if api_key.startswith("sk-proj-"):
            return ProviderBalance(
                has_credits=True,
                is_subscription=True,
                balance_usd=-1,
                source="estimated",
            )
        return ProviderBalance(has_credits=True, source="estimated")

    def _check_deepseek(self, api_key: str) -> ProviderBalance:
        """DeepSeek: GET /user/balance"""
        import requests

        r = requests.get(
            "https://api.deepseek.com/user/balance",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=5,
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("is_available"):
                balances = data.get("balance_infos", [])
                total = sum(float(b.get("total_balance", 0)) for b in balances)
                return ProviderBalance(
                    balance_usd=round(total, 4),
                    has_credits=total > 0.001,
                    source="api",
                )
        return ProviderBalance(has_credits=True, source="estimated")

    def _check_google(self, api_key: str) -> ProviderBalance:
        """Google: Gemini API keys starting with AIza are free-tier eligible."""
        if api_key.startswith("AIza"):
            return ProviderBalance(
                has_credits=True,
                is_free_tier=True,
                is_subscription=True,
                balance_usd=-1,
                source="estimated",
            )
        return ProviderBalance(has_credits=True, source="estimated")

    def _check_groq(self, api_key: str) -> ProviderBalance:
        """Groq: free tier for supported models."""
        return ProviderBalance(
            has_credits=True,
            is_free_tier=True,
            balance_usd=-1,
            source="estimated",
        )

    def _check_anthropic(self, api_key: str) -> ProviderBalance:
        """Anthropic: no public balance API, estimate from key."""
        if api_key.startswith("sk-ant-"):
            return ProviderBalance(
                has_credits=True,
                is_subscription=True,
                balance_usd=-1,
                source="estimated",
            )
        return ProviderBalance(has_credits=True, source="estimated")

    def _check_unknown(self, api_key: str) -> ProviderBalance:
        """Unknown provider: assume credits available, but unverified."""
        return ProviderBalance(has_credits=True, source="estimated")

    _CHECKERS = {
        "openrouter": _check_openrouter,
        "openai": _check_openai,
        "deepseek": _check_deepseek,
        "google": _check_google,
        "groq": _check_groq,
        "anthropic": _check_anthropic,
    }

    def get_balance(self, provider: str) -> ProviderBalance | None:
        """Get cached balance for a specific provider."""
        return self._cache.get(provider)

    def has_credits(self, provider: str) -> bool:
        """Quick check: does this provider have credits?"""
        bal = self._cache.get(provider)
        if not bal:
            return True
        return bal.has_credits

    def invalidate(self, provider: str = None):
        """Clear cache for one or all providers."""
        if provider:
            self._cache.pop(provider, None)
        else:
            self._cache.clear()
