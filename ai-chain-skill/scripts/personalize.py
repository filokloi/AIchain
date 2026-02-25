import os
import sys
import json
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.sync import fetch_routing_table

COST_EPSILON = 0.00000001

def load_capabilities(log: logging.Logger) -> dict:
    cap_path = Path(os.path.expanduser("~")) / ".openclaw" / "aichain" / "discovered_capabilities.json"
    if not cap_path.exists():
        log.warning("No discovered_capabilities.json found. Run --discover first.")
        return {}
    with open(cap_path, "r", encoding="utf-8") as f:
        return json.load(f)

def build_personalized_table(global_table: dict, capabilities: dict, log: logging.Logger) -> dict:
    if not capabilities:
        return global_table
        
    providers_data = capabilities.get("providers", {})
    
    # Flatten all available models from authenticated providers into a set
    user_models = set()
    for provider_name, p_data in providers_data.items():
        if p_data.get("status") == "authenticated":
            user_models.update(p_data.get("available_models", []))
            
    if not user_models:
        log.warning("No authenticated providers with models discovered. Personalized table will be empty.")
        
    personalized_hierarchy = []
    
    for entry in global_table.get("routing_hierarchy", []):
        model_id = entry.get("model", "")
        
        # 1. Accessibility Filtering
        # If the model is not in our discovered set, it is inaccessible to this user
        if model_id not in user_models:
            continue
            
        metrics = entry.get("metrics", {})
        intelligence = metrics.get("intelligence", 0)
        
        # Base cost from the global table
        effective_cost = metrics.get("cost", 0)
        
        # 2. Priority calculation (can be expanded later for Groq/Gemini specifics)
        # We boost Priority for models that the user specifically enabled via direct API
        priority = 1.0
        if model_id.startswith("google/") and "gemini" in providers_data.keys():
             if providers_data["gemini"].get("status") == "authenticated":
                 # Direct API often implies free tier or heavy utilization preference
                 priority = 10.0
                 effective_cost = 0.0 # Clamp direct Gemini usage to 0
                 
        if model_id.startswith("groq/") and "groq" in providers_data.keys():
            if providers_data["groq"].get("status") == "authenticated":
                 priority = 10.0
                 effective_cost = 0.0
        
        # 3. Recalculate Value Score
        val_score = (intelligence * priority) / (effective_cost + COST_EPSILON)
        
        # Inject recalculations
        entry["value_score"] = float(val_score)
        entry["metrics"]["effective_cost"] = effective_cost
        
        personalized_hierarchy.append(entry)
        
    # Re-sort descending by value score
    personalized_hierarchy.sort(key=lambda x: x.get("value_score", 0), reverse=True)
    
    new_table = global_table.copy()
    new_table["routing_hierarchy"] = personalized_hierarchy
    new_table["version"] = global_table.get("version", "v4.0") + ".1-personalized"
    
    return new_table

def cmd_build_personal(cfg: dict, log: logging.Logger) -> bool:
    """CLI Entry point for --build-personal"""
    capabilities = load_capabilities(log)
    if not capabilities:
        return False
        
    global_table = fetch_routing_table(cfg["routing_url"], log, cfg.get("version_compat"))
    if not global_table:
        log.error("Failed to fetch global routing table for personalization.")
        return False
        
    personalized_table = build_personalized_table(global_table, capabilities, log)
    
    out_dir = Path(os.path.expanduser("~")) / ".openclaw" / "aichain"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "personalized_routing_table.json"
    
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(personalized_table, f, indent=2)
        
    log.info(f"Personalized routing table compiled. Retained {len(personalized_table['routing_hierarchy'])} accessible models.")
    return True
