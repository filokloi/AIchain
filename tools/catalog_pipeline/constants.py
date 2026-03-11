from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_FILE = REPO_ROOT / "ai_routing_table.json"
ARTIFACT_ROOT = REPO_ROOT / "artifacts" / "source_snapshots"
OPENROUTER_API = "https://openrouter.ai/api/v1/models"
LMSYS_ARENA_URL = "https://huggingface.co/spaces/lmarena-ai/arena-leaderboard/resolve/main/results/latest/elo_results_full.json"
LMSYS_SPACE_API = "https://huggingface.co/api/spaces/lmarena-ai/arena-leaderboard"
ARTIFICIAL_ANALYSIS_URL = "https://artificialanalysis.ai/api/v2/data/llms/models"

DEFAULT_GEMINI_HELPER_MODEL = "gemini-2.5-flash"
DEFAULT_GROQ_HELPER_MODEL = "llama-3.1-8b-instant"
DEFAULT_HELPER_TIMEOUT_SECONDS = 20
MAX_HELPER_ALIAS_BATCH = 20
MAX_HELPER_TASK_ENRICH_MODELS = 24

SOURCE_THRESHOLDS = {
    "openrouter": {"min_records": 50, "min_coverage": 0.75, "critical": True},
    "lmsys": {"min_records": 10, "min_coverage": 0.60, "critical": False},
    "artificial_analysis": {"min_records": 10, "min_coverage": 0.60, "critical": False},
}

SCORING_WEIGHTS = {
    "intelligence": 0.30,
    "speed": 0.14,
    "stability": 0.16,
    "cost_efficiency": 0.18,
    "availability": 0.10,
    "context": 0.06,
    "task_fit": 0.06,
}
SCORING_VERSION = "2026.03-control-plane-v1"
SCORING_DISPLAY_FORMULA = (
    "Score = 0.30·Intel + 0.14·Speed + 0.16·Stability + 0.18·CostEff + "
    "0.10·Avail + 0.06·Context + 0.06·TaskFit"
)

SUPPORTED_TASK_TYPES = (
    "coding",
    "reasoning",
    "vision",
    "long_context",
    "extraction",
    "structured_output",
    "general_chat",
    "tool_agent_compatibility",
)

MAJOR_PROVIDER_PREFIXES = (
    "openai/",
    "google/",
    "anthropic/",
    "meta-llama/",
    "deepseek/",
    "mistralai/",
    "qwen/",
    "cohere/",
    "x-ai/",
)

OAUTH_BRIDGES = {
    "openai/gpt-4o": {"provider": "OpenAI", "note": "ChatGPT Plus subscription"},
    "openai/gpt-4.1": {"provider": "OpenAI", "note": "ChatGPT Plus subscription"},
    "openai/codex-mini": {"provider": "OpenAI", "note": "Codex free-tier window"},
    "openai/o3-pro": {"provider": "OpenAI", "note": "ChatGPT Pro subscription"},
    "openai/o4-mini": {"provider": "OpenAI", "note": "ChatGPT Plus subscription"},
    "google/gemini-2.5-pro": {"provider": "Google", "note": "AI Studio free tier"},
    "google/gemini-2.5-flash": {"provider": "Google", "note": "AI Studio free tier"},
    "anthropic/claude-sonnet-4": {"provider": "Anthropic", "note": "Claude.ai free tier"},
}


PROVIDER_ACCESS_MATRIX = {
    "openai": {
        "label": "OpenAI API",
        "overall_mode": "runtime_confirmed",
        "factual_state": "runtime_confirmed",
        "billing_basis": "metered_api_billing",
        "usage_tracking": "provider_console_and_api_spend",
        "quota_visibility": "api_balance_and_provider_console",
        "limit_type": "metered_spend",
        "verification_scope": "aichaind_runtime",
        "project_verification": "Runtime confirmed in AIchain via API key execution.",
        "fallback_path": ["openai-codex.oauth", "local", "next_ranked_model"],
        "limitations": [
            "Requires separate Platform billing; ChatGPT subscription does not automatically cover API usage.",
        ],
        "methods": {
            "api_key": {
                "mode": "runtime_confirmed",
                "official_support": True,
                "runtime_confirmed": True,
                "verification_basis": "provider_api_plus_aichaind_runtime",
                "limit_type": "metered_spend",
                "machine_readable_state": "partial",
                "note": "Primary stable execution path for OpenAI models in aichaind.",
            },
        },
    },
    "openai-codex": {
        "label": "OpenAI Codex OAuth",
        "overall_mode": "target_form_not_reached",
        "factual_state": "target_form_not_reached",
        "billing_basis": "subscription_plan_window",
        "usage_tracking": "openclaw_auth_usage_stats_plus_provider_ui",
        "quota_visibility": "provider_ui_or_openclaw_sign_in_window_not_fully_machine_readable",
        "limit_type": "daily_and_weekly_window",
        "verification_scope": "maintainer_runtime_probe",
        "project_verification": "OpenClaw OAuth access is supported in the architecture; runtime target confirmation is driven by factual probes rather than by static model lists.",
        "fallback_path": ["openai.api_key", "local", "next_ranked_model"],
        "limitations": [
            "Daily and weekly sign-in plan windows may apply and can change by provider policy or subscription tier.",
            "Remaining entitlement is not yet fully machine-readable inside AIchain; verify in provider or OpenClaw UI.",
        ],
        "methods": {
            "oauth": {
                "mode": "target_form_not_reached",
                "official_support": True,
                "runtime_confirmed": False,
                "verification_basis": "openclaw_gateway_runtime_probe",
                "limit_type": "daily_and_weekly_window",
                "machine_readable_state": "ui_only",
                "note": "Runtime status is upgraded only when factual gateway execution probes succeed.",
                "verified_models": ["openai-codex/gpt-5.3-codex"],
                "target_model": "openai-codex/gpt-5.4",
            },
        },
    },
    "google": {
        "label": "Google Gemini",
        "overall_mode": "target_form_not_reached",
        "factual_state": "target_form_not_reached",
        "billing_basis": "metered_api_billing_or_workspace_entitlement",
        "usage_tracking": "provider_console",
        "quota_visibility": "provider_console_or_workspace_ui",
        "limit_type": "project_or_workspace_quota",
        "verification_scope": "docs_plus_runtime_when_available",
        "project_verification": "Official OAuth exists, but AIchain does not yet have a runtime-confirmed Google OAuth execution adapter.",
        "fallback_path": ["google.api_key", "local", "next_ranked_model"],
        "limitations": [
            "OAuth and workspace access should remain optional and must not replace ranked local routing.",
        ],
        "methods": {
            "api_key": {
                "mode": "target_form_not_reached",
                "official_support": True,
                "runtime_confirmed": False,
                "verification_basis": "aichaind_discovery",
                "limit_type": "project_or_workspace_quota",
                "machine_readable_state": "provider_console",
                "note": "Supported execution path, but runtime health varies with current provider auth state.",
            },
            "oauth": {
                "mode": "target_form_not_reached",
                "official_support": True,
                "runtime_confirmed": False,
                "verification_basis": "official_documentation",
                "limit_type": "project_or_workspace_quota",
                "machine_readable_state": "ui_only",
                "note": "Officially documented, not yet runtime-confirmed in AIchain execution.",
            },
            "workspace_connector": {
                "mode": "target_form_not_reached",
                "official_support": True,
                "runtime_confirmed": False,
                "verification_basis": "design_target",
                "limit_type": "workspace_quota",
                "machine_readable_state": "provider_console",
                "note": "Planned connector class; not yet wired as a verified execution adapter.",
            },
        },
    },
    "anthropic": {
        "label": "Anthropic Claude",
        "overall_mode": "target_form_not_reached",
        "factual_state": "target_form_not_reached",
        "billing_basis": "metered_api_billing",
        "usage_tracking": "provider_console",
        "quota_visibility": "provider_console",
        "limit_type": "metered_spend",
        "verification_scope": "aichaind_runtime",
        "project_verification": "AIchain currently treats API key execution as the path to finish before adding any broader connector story.",
        "fallback_path": ["openrouter.api_key", "local", "next_ranked_model"],
        "limitations": [
            "OAuth is not treated as a verified generic model execution path here.",
        ],
        "methods": {
            "api_key": {
                "mode": "target_form_not_reached",
                "official_support": True,
                "runtime_confirmed": False,
                "verification_basis": "aichaind_discovery",
                "limit_type": "metered_spend",
                "machine_readable_state": "provider_console",
                "note": "Architecturally supported, but direct adapter/runtime path is not fully closed yet.",
            },
            "oauth": {
                "mode": "not_officially_supported",
                "official_support": False,
                "runtime_confirmed": False,
                "verification_basis": "no_verified_provider_execution_path",
                "limit_type": "n/a",
                "machine_readable_state": "n/a",
                "note": "Do not treat consumer sign-in as a generic Claude execution method.",
            },
        },
    },
    "mistral": {
        "label": "Mistral",
        "overall_mode": "target_form_not_reached",
        "factual_state": "target_form_not_reached",
        "billing_basis": "metered_api_billing_or_cloud_connector",
        "usage_tracking": "provider_console_or_cloud_billing",
        "quota_visibility": "provider_console_or_cloud_ui",
        "limit_type": "metered_spend_or_cloud_quota",
        "verification_scope": "docs_plus_runtime_when_available",
        "project_verification": "API key access is the practical path today; cloud/enterprise auth is a connector concern, not a default consumer OAuth assumption.",
        "fallback_path": ["mistral.api_key", "openrouter.api_key", "next_ranked_model"],
        "limitations": [
            "Cloud auth and enterprise connectors should not be conflated with a generic consumer sign-in bridge.",
        ],
        "methods": {
            "api_key": {
                "mode": "target_form_not_reached",
                "official_support": True,
                "runtime_confirmed": False,
                "verification_basis": "aichaind_discovery",
                "limit_type": "metered_spend",
                "machine_readable_state": "provider_console",
                "note": "Supported, but not yet part of the verified public access matrix runtime set.",
            },
            "enterprise_connector": {
                "mode": "target_form_not_reached",
                "official_support": True,
                "runtime_confirmed": False,
                "verification_basis": "official_cloud_docs",
                "limit_type": "cloud_project_quota",
                "machine_readable_state": "provider_console",
                "note": "Enterprise/cloud connector class exists conceptually; not yet wired as an AIchain execution adapter.",
            },
        },
    },
    "deepseek": {
        "label": "DeepSeek",
        "overall_mode": "runtime_confirmed",
        "factual_state": "runtime_confirmed",
        "billing_basis": "metered_api_billing",
        "usage_tracking": "balance_api_plus_provider_console",
        "quota_visibility": "machine_readable_balance_and_provider_console",
        "limit_type": "metered_spend",
        "verification_scope": "aichaind_runtime",
        "project_verification": "Runtime confirmed in AIchain and currently used as a stable direct provider path.",
        "fallback_path": ["local", "openrouter.api_key", "next_ranked_model"],
        "limitations": [
            "Balance and quota health still depend on provider-side availability.",
        ],
        "methods": {
            "api_key": {
                "mode": "runtime_confirmed",
                "official_support": True,
                "runtime_confirmed": True,
                "verification_basis": "provider_balance_api_plus_runtime",
                "limit_type": "metered_spend",
                "machine_readable_state": "machine_readable",
                "note": "Direct API path with balance-aware routing support.",
            },
        },
    },
    "moonshot": {
        "label": "Moonshot / Kimi",
        "overall_mode": "target_form_not_reached",
        "factual_state": "target_form_not_reached",
        "billing_basis": "metered_api_billing",
        "usage_tracking": "provider_console",
        "quota_visibility": "provider_console",
        "limit_type": "metered_spend",
        "verification_scope": "docs_plus_runtime_when_available",
        "project_verification": "Treat as API-key centric until a real execution connector path is verified.",
        "fallback_path": ["openrouter.api_key", "next_ranked_model"],
        "limitations": [
            "Do not assume consumer sign-in equals supported third-party execution access.",
        ],
        "methods": {
            "api_key": {
                "mode": "target_form_not_reached",
                "official_support": True,
                "runtime_confirmed": False,
                "verification_basis": "official_api_docs",
                "limit_type": "metered_spend",
                "machine_readable_state": "provider_console",
                "note": "Official API path exists; runtime confirmation in AIchain remains to be closed.",
            },
            "oauth": {
                "mode": "unknown",
                "official_support": False,
                "runtime_confirmed": False,
                "verification_basis": "unverified",
                "limit_type": "n/a",
                "machine_readable_state": "n/a",
                "note": "No verified generic OAuth execution path is assumed.",
            },
        },
    },
    "zhipu": {
        "label": "Zhipu",
        "overall_mode": "target_form_not_reached",
        "factual_state": "target_form_not_reached",
        "billing_basis": "metered_api_billing",
        "usage_tracking": "provider_console",
        "quota_visibility": "provider_console",
        "limit_type": "metered_spend",
        "verification_scope": "docs_plus_runtime_when_available",
        "project_verification": "Treat as API-key centric until a real connector or sign-in execution path is factually verified.",
        "fallback_path": ["openrouter.api_key", "next_ranked_model"],
        "limitations": [
            "Access semantics may vary; do not advertise OAuth-like execution without factual verification.",
        ],
        "methods": {
            "api_key": {
                "mode": "target_form_not_reached",
                "official_support": True,
                "runtime_confirmed": False,
                "verification_basis": "official_api_docs",
                "limit_type": "metered_spend",
                "machine_readable_state": "provider_console",
                "note": "API path exists; AIchain runtime confirmation is still conservative here.",
            },
            "oauth": {
                "mode": "unknown",
                "official_support": False,
                "runtime_confirmed": False,
                "verification_basis": "unverified",
                "limit_type": "n/a",
                "machine_readable_state": "n/a",
                "note": "No verified generic OAuth execution path is assumed.",
            },
        },
    },
    "openrouter": {
        "label": "OpenRouter",
        "overall_mode": "runtime_confirmed",
        "factual_state": "runtime_confirmed",
        "billing_basis": "metered_api_billing",
        "usage_tracking": "balance_api_plus_provider_console",
        "quota_visibility": "machine_readable_balance_and_provider_console",
        "limit_type": "metered_spend",
        "verification_scope": "aichaind_runtime",
        "project_verification": "Runtime confirmed as the primary catalog source and a controlled execution fallback.",
        "fallback_path": ["next_ranked_model", "local"],
        "limitations": [
            "Should remain a fallback and catalog-rich option, not the only execution dependency.",
        ],
        "methods": {
            "api_key": {
                "mode": "runtime_confirmed",
                "official_support": True,
                "runtime_confirmed": True,
                "verification_basis": "provider_balance_api_plus_runtime",
                "limit_type": "metered_spend",
                "machine_readable_state": "machine_readable",
                "note": "Catalog-rich API path and execution fallback.",
            },
        },
    },
    "local": {
        "label": "Local Runtime",
        "overall_mode": "target_form_not_reached",
        "factual_state": "target_form_not_reached",
        "billing_basis": "local_inference_hardware",
        "usage_tracking": "local_runtime_health_and_operator_observation",
        "quota_visibility": "local_only",
        "limit_type": "local_hardware_capacity",
        "verification_scope": "machine_specific_runtime_probe",
        "project_verification": "Implemented and tested; runtime confirmation depends on a healthy local model server.",
        "fallback_path": ["next_ranked_model"],
        "limitations": [
            "Privacy reroute only becomes runtime-confirmed when a local completion probe succeeds.",
        ],
        "methods": {
            "local": {
                "mode": "target_form_not_reached",
                "official_support": True,
                "runtime_confirmed": False,
                "verification_basis": "local_completion_probe",
                "limit_type": "local_hardware_capacity",
                "machine_readable_state": "machine_readable",
                "note": "Available in architecture, but this machine still requires a healthy local serving path.",
            },
        },
    },
}


BENCHMARK_MAP = {
    "openai/o3-pro": 99, "openai/gpt-4.1": 96, "openai/o4-mini": 94, "openai/o4-mini-high": 95,
    "openai/gpt-4o": 92, "openai/gpt-4.1-mini": 86, "openai/o3-mini": 90, "openai/o3-mini-high": 91,
    "openai/codex-mini": 88, "openai/gpt-4.1-nano": 76,
    "openai/gpt-5.3-codex": 97, "openai/gpt-5.2-codex": 96, "openai/gpt-5.1-codex-max": 95,
    "openai/gpt-5-mini": 92, "openai/gpt-5": 97,
    "google/gemini-2.5-pro": 97, "google/gemini-2.5-flash": 90,
    "google/gemini-2.0-flash": 86, "google/gemini-3.1-pro": 98, "google/gemini-3-pro-preview": 97,
    "google/gemma-3-27b-it": 83, "google/gemma-3-12b-it": 76, "google/gemma-3-4b-it": 68,
    "google/gemma-2-27b-it": 81, "google/gemma-2-9b-it": 75,
    "anthropic/claude-opus-4.6": 99, "anthropic/claude-sonnet-4.6": 97, "anthropic/claude-haiku-4.5": 92,
    "anthropic/claude-sonnet-4": 95, "anthropic/claude-3.5-sonnet": 94,
    "anthropic/claude-3.5-haiku": 83,
    "deepseek/deepseek-r1": 97, "deepseek/deepseek-r1-0528": 98, "deepseek/deepseek-r1-distill-llama-70b": 92,
    "deepseek/deepseek-chat": 93, "deepseek/deepseek-v3": 95,
    "mistralai/mistral-large": 90, "mistralai/mistral-large-2407": 92, "mistralai/mistral-large-2411": 93,
    "mistralai/mistral-small-3.1-24b-instruct": 82, "mistralai/mistral-small": 78,
    "mistralai/ministral-8b-instruct": 74, "mistralai/pixtral-large-2411": 91,
    "meta-llama/llama-4-maverick": 88, "meta-llama/llama-4-scout": 84,
    "meta-llama/llama-3.3-70b-instruct": 92, "meta-llama/llama-3.1-405b-instruct": 94,
    "meta-llama/llama-3.1-70b-instruct": 90, "meta-llama/llama-3.1-8b-instruct": 76,
    "meta-llama/llama-3.2-3b-instruct": 65, "meta-llama/llama-3.2-1b-instruct": 58,
    "qwen/qwen-max": 93, "qwen/qwen-plus": 88,
    "qwen/qwen3-235b-a22b": 92, "qwen/qwen3-30b-a3b": 85,
    "qwen/qwen2.5-72b-instruct": 90, "qwen/qwen2.5-32b-instruct": 84, "qwen/qwen2.5-14b-instruct": 78,
    "qwen/qwen2.5-7b-instruct": 74, "qwen/qwq-32b-preview": 87,
    "nvidia/nemotron-4-340b-instruct": 91, "nvidia/llama-3.1-nemotron-70b-instruct": 92,
    "cohere/command-r-plus": 89, "cohere/command-r": 82, "cohere/command-r-plus-08-2024": 91,
    "x-ai/grok-beta": 88, "x-ai/grok-2-1212": 93, "inflection/inflection-3-pi": 86,
}


