#!/usr/bin/env python3
"""
AIchain global catalog/control-plane arbitrator.

This module remains the CLI entrypoint and backwards-compatible import surface,
but the actual ingestion logic now lives in tools.catalog_pipeline.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.catalog_pipeline.constants import BENCHMARK_MAP, OAUTH_BRIDGES, OUTPUT_FILE
from tools.catalog_pipeline.credentials import resolve_credential
from tools.catalog_pipeline.pipeline import arbitrate_catalog
from tools.catalog_pipeline.rank.scoring import assess_geopolitical_risk, compute_value_score, parse_cost


def arbitrate() -> dict:
    openrouter_key = resolve_credential("OPENROUTER_KEY", "OPENROUTER_API_KEY")
    gemini_key = resolve_credential("GEMINI_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")
    groq_key = resolve_credential("GROQ_KEY", "GROQ_API_KEY")
    artificial_analysis_key = resolve_credential("ARTIFICIAL_ANALYSIS_KEY")

    print("=" * 64)
    print("[AIchain] GLOBAL CONTROL PLANE ARBITRATION v5.0")
    print("[AIchain] Mission: Maximum Intelligence, Speed, Stability, Minimum Cost")
    print("=" * 64)
    print(
        f"[AIchain] Keys: OpenRouter={'OK' if openrouter_key else 'ANON'} | "
        f"Gemini={'OK' if gemini_key else 'NONE'} | "
        f"Groq={'OK' if groq_key else 'NONE'} | "
        f"AA={'OK' if artificial_analysis_key else 'NONE'}"
    )

    table = arbitrate_catalog(
        openrouter_key=openrouter_key,
        gemini_key=gemini_key,
        groq_key=groq_key,
        artificial_analysis_key=artificial_analysis_key,
        output_file=Path(OUTPUT_FILE),
    )

    print(f"[AIchain] Arbitration complete: {table['total_models_analyzed']} models ranked")
    print(f"[AIchain] System status: {table['system_status']}")
    print(f"[AIchain] Routing table written to {OUTPUT_FILE}")
    return table


def main() -> None:
    table = arbitrate()
    print(json.dumps({
        "status": table["system_status"],
        "models": table["total_models_analyzed"],
        "heavy_hitter": table["heavy_hitter"]["model"],
    }, indent=2))


if __name__ == "__main__":
    main()
