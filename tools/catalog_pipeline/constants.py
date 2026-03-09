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


