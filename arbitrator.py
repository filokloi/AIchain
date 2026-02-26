#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════════╗
║  AIchain — The Sovereign Global Value Maximizer                  ║
║  arbitrator.py v3.0 — Multi-Source Intelligence Fusion Engine    ║
║                                                                   ║
║  Data Sources:                                                    ║
║    1. OpenRouter API  → Price, Speed, Context, Model catalog     ║
║    2. Artificial Analysis → Intelligence Index (scraped)         ║
║    3. LMSYS Chatbot Arena → ELO Rankings (public JSON)           ║
║                                                                   ║
║  Output: ai_routing_table.json with Value Scores + Task Labels   ║
║  Philosophy: Maximum Intelligence at Zero Cost.                  ║
╚═══════════════════════════════════════════════════════════════════╝
"""

import json
import os
import sys
import math
import re
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    print("[FATAL] 'requests' is not installed. Run: pip install requests")
    sys.exit(1)


# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────

OPENROUTER_API = "https://openrouter.ai/api/v1/models"
LMSYS_ARENA_URL = "https://huggingface.co/spaces/lmsys/chatbot-arena-leaderboard/resolve/main/results/latest/elo_results_full.json"
ARTIFICIAL_ANALYSIS_URL = "https://artificialanalysis.ai/api/models"

OUTPUT_FILE = Path(__file__).parent / "ai_routing_table.json"

# Value Score formula weights
# ValueScore = (Intelligence / (Cost + COST_EPSILON)) * Stability * SpeedFactor
COST_EPSILON = 0.01  # prevents division by zero, gives free models massive advantage

# Known OAuth / Subscription bridge models (free via subscription)
OAUTH_BRIDGES = {
    "openai/gpt-4o":           {"provider": "OpenAI",    "note": "ChatGPT Plus subscription"},
    "openai/gpt-4.1":          {"provider": "OpenAI",    "note": "ChatGPT Plus subscription"},
    "openai/codex-mini":       {"provider": "OpenAI",    "note": "Codex free-tier window"},
    "openai/o3-pro":           {"provider": "OpenAI",    "note": "ChatGPT Pro subscription"},
    "openai/o4-mini":          {"provider": "OpenAI",    "note": "ChatGPT Plus subscription"},
    "google/gemini-2.5-pro":   {"provider": "Google",    "note": "AI Studio free tier"},
    "google/gemini-2.5-flash": {"provider": "Google",    "note": "AI Studio free tier"},
    "anthropic/claude-sonnet-4": {"provider": "Anthropic", "note": "Claude.ai free tier"},
}

# ── Intelligence Benchmark Map ──
# Curated from public arena data and Artificial Analysis Intelligence Index.
# These are baseline scores; live data from scraping overrides them.
BENCHMARK_MAP = {
    # OpenAI
    "openai/o3-pro": 99, "openai/gpt-4.1": 96, "openai/o4-mini": 94, "openai/o4-mini-high": 95,
    "openai/gpt-4o": 92, "openai/gpt-4.1-mini": 86, "openai/o3-mini": 90, "openai/o3-mini-high": 91,
    "openai/codex-mini": 88, "openai/gpt-4.1-nano": 76,
    "openai/gpt-5.3-codex": 97, "openai/gpt-5.2-codex": 96, "openai/gpt-5.1-codex-max": 95,
    "openai/gpt-5-mini": 92, "openai/gpt-5": 97,
    # Google
    "google/gemini-2.5-pro": 97, "google/gemini-2.5-flash": 90,
    "google/gemini-2.0-flash": 86, "google/gemini-3.1-pro": 98, "google/gemini-3-pro-preview": 97,
    "google/gemma-3-27b-it": 83, "google/gemma-3-12b-it": 76, "google/gemma-3-4b-it": 68,
    "google/gemma-2-27b-it": 81, "google/gemma-2-9b-it": 75,
    # Anthropic
    "anthropic/claude-opus-4.6": 99, "anthropic/claude-sonnet-4.6": 97, "anthropic/claude-haiku-4.5": 92,
    "anthropic/claude-sonnet-4": 95, "anthropic/claude-3.5-sonnet": 94,
    "anthropic/claude-3.5-haiku": 83,
    # DeepSeek
    "deepseek/deepseek-r1": 97, "deepseek/deepseek-r1-0528": 98, "deepseek/deepseek-r1-distill-llama-70b": 92,
    "deepseek/deepseek-chat": 93, "deepseek/deepseek-v3": 95,
    # Mistral
    "mistralai/mistral-large": 90, "mistralai/mistral-large-2407": 92, "mistralai/mistral-large-2411": 93,
    "mistralai/mistral-small-3.1-24b-instruct": 82, "mistralai/mistral-small": 78,
    "mistralai/ministral-8b-instruct": 74, "mistralai/pixtral-large-2411": 91,
    # Meta Llama
    "meta-llama/llama-4-maverick": 88, "meta-llama/llama-4-scout": 84,
    "meta-llama/llama-3.3-70b-instruct": 92, "meta-llama/llama-3.1-405b-instruct": 94,
    "meta-llama/llama-3.1-70b-instruct": 90, "meta-llama/llama-3.1-8b-instruct": 76,
    "meta-llama/llama-3.2-3b-instruct": 65, "meta-llama/llama-3.2-1b-instruct": 58,
    # Qwen
    "qwen/qwen-max": 93, "qwen/qwen-plus": 88, 
    "qwen/qwen3-235b-a22b": 92, "qwen/qwen3-30b-a3b": 85,
    "qwen/qwen2.5-72b-instruct": 90, "qwen/qwen2.5-32b-instruct": 84, "qwen/qwen2.5-14b-instruct": 78,
    "qwen/qwen2.5-7b-instruct": 74, "qwen/qwq-32b-preview": 87,
    # Nvidia
    "nvidia/nemotron-4-340b-instruct": 91, "nvidia/llama-3.1-nemotron-70b-instruct": 92,
    # Others
    "cohere/command-r-plus": 89, "cohere/command-r": 82, "cohere/command-r-plus-08-2024": 91,
    "x-ai/grok-beta": 88, "x-ai/grok-2-1212": 93, "inflection/inflection-3-pi": 86,
}


# ─────────────────────────────────────────────
# DATA SOURCE 1: OPENROUTER
# ─────────────────────────────────────────────

def fetch_openrouter_models(api_key: str | None) -> list[dict]:
    """Fetch the full model catalog from OpenRouter."""
    headers = {
        "HTTP-Referer": "https://github.com/AIchain",
        "X-Title": "AIchain Sovereign Arbitrator",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        resp = requests.get(OPENROUTER_API, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        models = data.get("data", [])
        print(f"[AIchain] OpenRouter: {len(models)} models fetched")
        return models
    except requests.RequestException as exc:
        print(f"[WARN] OpenRouter fetch failed: {exc}")
        return []


# ─────────────────────────────────────────────
# DATA SOURCE 2: LMSYS CHATBOT ARENA (ELO)
# ─────────────────────────────────────────────

def fetch_lmsys_arena() -> dict:
    """
    Fetch LMSYS Chatbot Arena ELO rankings.
    Returns: {model_name_lower: elo_score}
    """
    elo_map = {}
    try:
        resp = requests.get(LMSYS_ARENA_URL, timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            # The structure varies; try common formats
            if isinstance(data, dict):
                for key, value in data.items():
                    if isinstance(value, dict) and "elo" in value:
                        elo_map[key.lower()] = value["elo"]
                    elif isinstance(value, (int, float)):
                        elo_map[key.lower()] = value
            print(f"[AIchain] LMSYS Arena: {len(elo_map)} ELO scores loaded")
        else:
            print(f"[WARN] LMSYS Arena returned {resp.status_code}")
    except requests.RequestException as exc:
        print(f"[WARN] LMSYS Arena fetch failed: {exc}")
    return elo_map


# ─────────────────────────────────────────────
# DATA SOURCE 3: ARTIFICIAL ANALYSIS (SCRAPE)
# ─────────────────────────────────────────────

def fetch_artificial_analysis() -> dict:
    """
    Try to fetch intelligence data from Artificial Analysis.
    Returns: {model_id_lower: {"quality": score, "speed": tps}}
    Falls back gracefully if unavailable (no API key needed for public leaderboard).
    """
    aa_map = {}
    try:
        # Try their public API endpoint
        resp = requests.get(
            ARTIFICIAL_ANALYSIS_URL,
            timeout=20,
            headers={"User-Agent": "AIchain-Arbitrator/3.0"}
        )
        if resp.status_code == 200:
            data = resp.json()
            models = data if isinstance(data, list) else data.get("data", data.get("models", []))
            for m in models:
                name = m.get("name", m.get("model", "")).lower()
                if name:
                    aa_map[name] = {
                        "quality": m.get("quality_index", m.get("quality", 0)),
                        "speed": m.get("output_speed", m.get("tokens_per_second", 0)),
                    }
            print(f"[AIchain] Artificial Analysis: {len(aa_map)} quality scores loaded")
        else:
            print(f"[WARN] Artificial Analysis returned {resp.status_code} (using fallback benchmarks)")
    except requests.RequestException as exc:
        print(f"[WARN] Artificial Analysis unavailable: {exc} (using fallback benchmarks)")
    return aa_map


# ─────────────────────────────────────────────
# DATA SOURCE 4: PROMO TRACKER (GEMINI FLASH)
# ─────────────────────────────────────────────

def fetch_promo_tracker(models: list[dict], gemini_key: str | None) -> list[str]:
    """
    Use Gemini Flash (via API) to scan the OpenRouter catalog for new :free models or promos.
    Returns: List of 'Free Kings' model IDs to prioritize.
    """
    if not gemini_key:
        print("[AIchain] Promo-Tracker skipped (No Gemini API key)")
        return []

    print("[AIchain] Promo-Tracker: Scanning catalog with Gemini Flash...")
    # Extract only relevant fields for free/low-cost models to save tokens
    free_candidates = []
    for m in models:
        cost = parse_cost(m.get("pricing", {}))
        if cost <= 0 or ":free" in m.get("id", ""):
            free_candidates.append({
                "id": m.get("id"),
                "context": m.get("context_length"),
                "name": m.get("name")
            })

    if not free_candidates:
        return []

    prompt = (
        "You are the AIchain Promo-Hunter. Analyze these free/promotional models "
        "and return a JSON array (just the raw JSON array of strings, no markdown) containing the exact 'id' "
        "of the top 5 'Free Kings' (the absolute best, most intelligent models available for free right now).\n"
        "Models:\n" + json.dumps(free_candidates[:100])
    )

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        resp = requests.post(url, json=payload, timeout=20)
        
        if resp.status_code == 200:
            data = resp.json()
            text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "[]")
            text = text.strip().replace("```json", "").replace("```", "").strip()
            kings = json.loads(text)
            if isinstance(kings, list):
                print(f"[AIchain] Promo-Tracker identified Free Kings: {kings}")
                return kings
        else:
            print(f"[WARN] Promo-Tracker API error {resp.status_code}: {resp.text}")
    except Exception as exc:
        print(f"[WARN] Promo-Tracker failed: {exc}")
    
    return []


# ─────────────────────────────────────────────
# INTELLIGENCE FUSION ENGINE
# ─────────────────────────────────────────────

def fuse_intelligence(
    model_id: str,
    context_length: int,
    elo_map: dict,
    aa_map: dict,
) -> int:
    """
    Multi-source intelligence fusion:
      1. Direct benchmark lookup (curated, highest priority)
      2. Artificial Analysis quality score (if available)
      3. LMSYS Arena ELO (if available, normalized to 0-100)
      4. Context-length heuristic (last resort)
    """
    scores = []

    # Source 1: Curated benchmarks
    if model_id in BENCHMARK_MAP:
        scores.append(("benchmark", BENCHMARK_MAP[model_id]))

    # Source 2: Artificial Analysis
    model_lower = model_id.lower().split("/")[-1]
    for aa_key, aa_val in aa_map.items():
        if model_lower in aa_key or aa_key in model_lower:
            q = aa_val.get("quality", 0)
            if q > 0:
                # AA quality is often 0-100 already
                normalized = min(int(q), 100)
                scores.append(("artificial_analysis", normalized))
            break

    # Source 3: LMSYS Arena ELO
    for elo_key, elo_val in elo_map.items():
        if model_lower in elo_key or elo_key in model_lower:
            # ELO typically 900-1400+; normalize to 0-100
            normalized = max(0, min(100, int((elo_val - 800) / 6)))
            scores.append(("lmsys_arena", normalized))
            break

    # Weighted average if multiple sources
    if scores:
        # Priority weights: benchmark > AA > LMSYS
        weight_map = {"benchmark": 3, "artificial_analysis": 2, "lmsys_arena": 1}
        total_weight = sum(weight_map.get(s[0], 1) for s in scores)
        weighted_sum = sum(weight_map.get(s[0], 1) * s[1] for s in scores)
        return int(weighted_sum / total_weight)

    # Source 4: Name/Parameter Semantic Parser Heuristic (fallback)
    # Extracts keywords, 'xxxB' patterns to estimate real capability instead of context length.
    base_score = 65
    
    # Keyword boosts
    if "pro" in model_lower or "opus" in model_lower or "-max" in model_lower or "large" in model_lower:
        base_score += 18
    elif "sonnet" in model_lower or "plus" in model_lower or "medium" in model_lower:
        base_score += 15
    elif "flash" in model_lower or "mini" in model_lower or "haiku" in model_lower or "small" in model_lower:
        base_score += 12
        
    # Thinking/Reasoner bonus
    if "thinking" in model_lower or "reason" in model_lower or "r1" in model_lower or "o1" in model_lower or "o3" in model_lower or "qwq" in model_lower:
        base_score += 6
        
    # Parameter size logic (overrides some base stats if present)
    param_match = re.search(r'(\d+(?:\.\d+)?)\s*[bm]', model_lower.replace('-', ''))
    if param_match:
        val = float(param_match.group(1))
        # if matched an 'm' (million), it's tiny
        if 'm' in param_match.group(0):
            val = val / 1000.0
            
        if val >= 400:     param_score = 93
        elif val >= 100:   param_score = 89
        elif val >= 65:    param_score = 87
        elif val >= 30:    param_score = 83
        elif val >= 12:    param_score = 77
        elif val >= 7:     param_score = 73
        elif val >= 3:     param_score = 66
        elif val < 3:      param_score = 60
        else:              param_score = 75
        
        # Blend keyword bonuses into parameter score (limited)
        base_score = param_score + (base_score - 65) * 0.5
        
    # Minimal contextual length bonus (+0 to +3)
    # E.g. 1M token context adds +3 to intelligence score, 4k adds 0.
    ctx_boost = max(0, min(3, math.log2(max(context_length / 4096, 1))))
    
    final_score = base_score + ctx_boost
    
    return min(int(final_score), 95)


def apply_promo_boost(model_id: str, promo_kings: list[str], intel: int) -> int:
    """Give a massive intelligence boost to Promo Tracker's selected 'Free Kings'."""
    if model_id in promo_kings:
        return min(intel + 15, 99)  # Boost them to near front-runner status
    return intel


def estimate_speed(model: dict) -> int:
    """Estimate speed on 0-100 scale from OpenRouter top_provider data."""
    top = model.get("top_provider", {})
    max_tokens = top.get("max_completion_tokens")

    if max_tokens and max_tokens > 0:
        score = 50 + 10 * math.log2(max(max_tokens / 1024, 1))
        return min(int(score), 99)

    ctx = model.get("context_length", 4096)
    if ctx <= 8192:
        return 85
    elif ctx <= 32768:
        return 75
    elif ctx <= 131072:
        return 65
    return 55


def estimate_stability(model: dict) -> int:
    """Estimate stability on 0-100 scale."""
    top = model.get("top_provider", {})
    score = 70

    if top.get("is_moderated"):
        score += 10
    ctx = model.get("context_length", 4096)
    if ctx >= 128000:
        score += 10
    elif ctx >= 32000:
        score += 5

    model_id = model.get("id", "")
    major = ["openai/", "google/", "anthropic/", "meta-llama/", "deepseek/", "mistralai/"]
    if any(model_id.startswith(p) for p in major):
        score += 8

    return min(score, 99)


def parse_cost(pricing: dict | None) -> float:
    """Return average cost per token (prompt + completion) / 2."""
    if not pricing:
        return 0.0
    try:
        prompt = float(pricing.get("prompt", "0"))
        completion = float(pricing.get("completion", "0"))
        return (prompt + completion) / 2.0
    except (ValueError, TypeError):
        return 0.0


# ─────────────────────────────────────────────
# VALUE SCORE & TIER CLASSIFICATION
# ─────────────────────────────────────────────

def compute_value_score(intelligence: int, speed: int, stability: int, cost: float) -> float:
    """
    The Sovereign Value Score:
      ValueScore = (Intelligence / (Cost + ε)) × (Stability/100) × (Speed/100)^0.3

    Free models (cost=0) get: Intelligence / 0.01 × Stability × Speed
    → Massive natural advantage for $0 models.
    Paid models get proportionally penalized by their price.
    """
    stability_factor = stability / 100.0
    speed_factor = (speed / 100.0) ** 0.3  # dampened — speed matters less than intelligence

    value = (intelligence / (cost + COST_EPSILON)) * stability_factor * speed_factor
    return round(value, 2)


def classify_tier(model_id: str, cost: float) -> str:
    """Three-tier hierarchy: PRIMARY → SECONDARY → RESCUE."""
    if model_id in OAUTH_BRIDGES:
        return "OAUTH_BRIDGE"
    elif cost <= 0:
        return "FREE_FRONTIER"
    else:
        return "HEAVY_HITTER"


def assign_task_label(tier: str, intelligence: int) -> str:
    """
    Label each model for its intended role:
      DAILY_FREE — everyday tasks ($0 or subscription-covered)
      COMPLEX_RESCUE — heavy hitter for when free models fail
    """
    if tier in ("OAUTH_BRIDGE", "FREE_FRONTIER"):
        return "DAILY_FREE"
    else:
        return "COMPLEX_RESCUE"


def tier_priority(tier: str) -> int:
    """Lower = higher priority. Strict PRIMARY → SECONDARY → RESCUE order."""
    return {"OAUTH_BRIDGE": 0, "FREE_FRONTIER": 1, "HEAVY_HITTER": 2}.get(tier, 3)


# ─────────────────────────────────────────────
# MAIN ARBITRATION
# ─────────────────────────────────────────────

def arbitrate() -> dict:
    """Run the full multi-source arbitration cycle."""
    api_key = os.environ.get("OPENROUTER_KEY")
    gemini_key = os.environ.get("GEMINI_KEY") or os.environ.get("GEMINI_API_KEY")
    groq_key = os.environ.get("GROQ_KEY") or os.environ.get("GROQ_API_KEY")
    print("=" * 64)
    print("[AIchain] SOVEREIGN GLOBAL ARBITRATION v4.0")
    print("[AIchain] Philosophy: Maximum Intelligence at Zero Cost")
    print("=" * 64)
    print(f"[AIchain] Keys: OpenRouter={'OK' if api_key else 'ANON'} | "
          f"Gemini={'OK' if gemini_key else 'NONE'} | "
          f"Groq={'OK' if groq_key else 'NONE'}")

    # ── Fetch all data sources in sequence ──
    raw_models = fetch_openrouter_models(api_key)
    elo_map = fetch_lmsys_arena()
    aa_map = fetch_artificial_analysis()
    promo_kings = fetch_promo_tracker(raw_models, gemini_key)

    print(f"[AIchain] Data fusion: OpenRouter({len(raw_models)}) + "
          f"LMSYS({len(elo_map)}) + AA({len(aa_map)}) + PromoKings({len(promo_kings)})")

    entries = []

    for model in raw_models:
        model_id = model.get("id", "")
        if not model_id:
            continue

        # ── Filter known placeholder models ──
        if "gpt-oss" in model_id:
            print(f"[AIchain] Skipping placeholder model: {model_id}")
            continue

        provider = model_id.split("/")[0].replace("-", " ").title() if "/" in model_id else "Unknown"
        pricing = model.get("pricing", {})
        cost = parse_cost(pricing)
        context_length = model.get("context_length", 4096)

        # ── Multi-source intelligence fusion ──
        base_intelligence = fuse_intelligence(model_id, context_length, elo_map, aa_map)
        intelligence = apply_promo_boost(model_id, promo_kings, base_intelligence)
        
        speed = estimate_speed(model)
        stability = estimate_stability(model)

        # ── Value Score (the sovereign formula) ──
        value_score = compute_value_score(intelligence, speed, stability, cost)

        tier = classify_tier(model_id, cost)
        task_label = assign_task_label(tier, intelligence)

        entry = {
            "model": model_id,
            "provider": provider,
            "tier": tier,
            "task_label": task_label,
            "metrics": {
                "intelligence": intelligence,
                "speed": speed,
                "stability": stability,
                "cost": round(cost, 8),
            },
            "value_score": value_score,
        }

        if model_id in OAUTH_BRIDGES:
            entry["bridge_note"] = OAUTH_BRIDGES[model_id]["note"]

        entries.append(entry)

    # ── Inject OAuth bridges not found in OpenRouter ──
    seen_ids = {e["model"] for e in entries}
    for bridge_id, info in OAUTH_BRIDGES.items():
        if bridge_id not in seen_ids:
            intel = fuse_intelligence(bridge_id, 128000, elo_map, aa_map)
            speed, stab = 85, 92
            vs = compute_value_score(intel, speed, stab, 0.0)
            entries.append({
                "model": bridge_id,
                "provider": info["provider"],
                "tier": "OAUTH_BRIDGE",
                "task_label": "DAILY_FREE",
                "metrics": {"intelligence": intel, "speed": speed, "stability": stab, "cost": 0.00},
                "value_score": vs,
                "bridge_note": info["note"],
            })

    # ── Sort: tier priority first, then value_score descending ──
    entries.sort(key=lambda e: (tier_priority(e["tier"]), -e["value_score"]))

    for i, entry in enumerate(entries, start=1):
        entry["rank"] = i

    # ── Identify the global Heavy Hitter (most intelligent, any cost) ──
    all_by_intel = sorted(entries, key=lambda e: -e["metrics"]["intelligence"])
    heavy_hitter = all_by_intel[0] if all_by_intel else None

    # ── Build output ──
    tier_counts = {
        "OAUTH_BRIDGE": sum(1 for e in entries if e["tier"] == "OAUTH_BRIDGE"),
        "FREE_FRONTIER": sum(1 for e in entries if e["tier"] == "FREE_FRONTIER"),
        "HEAVY_HITTER": sum(1 for e in entries if e["tier"] == "HEAVY_HITTER"),
    }

    routing_table = {
        "system_status": "OPERATIONAL",
        "scope": "GLOBAL_NON_DISCRIMINATORY",
        "version": "4.0-sovereign",
        "philosophy": "Maximum Intelligence at Zero Cost. Data is Sovereign Capital.",
        "last_synopsis": datetime.now(timezone.utc).isoformat(),
        "data_sources": {
            "openrouter": len(raw_models),
            "lmsys_arena": len(elo_map),
            "artificial_analysis": len(aa_map),
        },
        "total_models_analyzed": len(entries),
        "live_promos": promo_kings,
        "tier_breakdown": tier_counts,
        "heavy_hitter": {
            "model": heavy_hitter["model"] if heavy_hitter else "N/A",
            "intelligence": heavy_hitter["metrics"]["intelligence"] if heavy_hitter else 0,
            "note": "Global rescue model — use ONLY when $0 models fail",
        },
        "routing_hierarchy": entries,
    }

    print(f"\n[AIchain] Arbitration complete: {len(entries)} models ranked")
    print(f"[AIchain]   PRIMARY (OAuth):     {tier_counts['OAUTH_BRIDGE']}")
    print(f"[AIchain]   SECONDARY (Free):    {tier_counts['FREE_FRONTIER']}")
    print(f"[AIchain]   RESCUE (Paid):       {tier_counts['HEAVY_HITTER']}")
    if heavy_hitter:
        print(f"[AIchain]   Heavy Hitter:        {heavy_hitter['model']} "
              f"(intel={heavy_hitter['metrics']['intelligence']})")
    print("=" * 64)

    return routing_table


def main():
    routing_table = arbitrate()

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(routing_table, f, indent=2, ensure_ascii=False)

    print(f"[AIchain] Routing table written to {OUTPUT_FILE}")
    print(f"[AIchain] System status: {routing_table['system_status']}")


if __name__ == "__main__":
    main()
