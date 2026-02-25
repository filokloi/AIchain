import os
import sys
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Static provider mappings
from providers import openrouter, gemini, groq

def load_keys_from_env():
    # Attempt to load from environment first
    keys = {
        "OPENROUTER_API_KEY": os.environ.get("OPENROUTER_API_KEY"),
        "GEMINI_API_KEY": os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"),
        "GROQ_API_KEY": os.environ.get("GROQ_API_KEY")
    }
    return keys

def run_discovery(log: logging.Logger) -> dict:
    """Executes capability discovery across all registered providers."""
    keys = load_keys_from_env()
    
    log.info("Starting Auto-Discovery across providers...")
    
    discovery_report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "providers": {}
    }
    
    # 1. OpenRouter
    or_result = openrouter.discover_capabilities(keys, log)
    discovery_report["providers"]["openrouter"] = or_result
    
    # 2. Gemini
    gem_result = gemini.discover_capabilities(keys, log)
    discovery_report["providers"]["gemini"] = gem_result
    
    # 3. Groq
    groq_result = groq.discover_capabilities(keys, log)
    discovery_report["providers"]["groq"] = groq_result
    
    return discovery_report

def cmd_discover(log: logging.Logger) -> bool:
    """CLI entry point for --discover"""
    report = run_discovery(log)
    
    # Write to local cache
    out_dir = Path(os.path.expanduser("~")) / ".openclaw" / "aichain"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "discovered_capabilities.json"
    
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
        
    log.info(f"Capability discovery complete. Results written to {out_path}")
    
    # Audit log
    audit_path = out_dir / "capability_audit.log"
    with open(audit_path, "a", encoding="utf-8") as f:
        audit_entry = {
            "timestamp": report["timestamp"],
            "summary": {p: v["status"] for p, v in report["providers"].items()},
            "total_models": sum(len(v.get("available_models", [])) for v in report["providers"].values())
        }
        f.write(json.dumps(audit_entry) + "\n")
        
    return True
