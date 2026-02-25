import logging

try:
    import requests
except ImportError:
    requests = None

def discover_capabilities(keys: dict, log: logging.Logger) -> dict:
    """Discover capabilities from Groq API."""
    result = {
        "status": "unconfigured",
        "available_models": [],
        "cost_mode": "api-per-token",
        "limits": {}
    }
    
    if not requests:
        log.error("python 'requests' library missing. Cannot discover Groq capabilities.")
        result["status"] = "error"
        return result

    api_key = keys.get("GROQ_API_KEY") 
    
    if not api_key:
        log.info("Groq capability discovery skipped (no API key configured).")
        return result
        
    log.info("Authenticating with Groq capabilities...")
    headers = {
        "Authorization": f"Bearer {api_key}"
    }
    
    try:
        # Check models endpoint using OpenAI compatible endpoint format
        models_resp = requests.get("https://api.groq.com/openai/v1/models", headers=headers, timeout=15)
        
        if models_resp.status_code != 200:
            log.warning(f"Groq authentication failed (HTTP {models_resp.status_code}).")
            result["status"] = "auth_failed"
            return result
            
        result["status"] = "authenticated"
        models_data = models_resp.json().get("data", [])
        
        # Add 'groq/' prefix to match routing table structure
        # Groq returns IDs like 'llama3-70b-8192'
        available_models = [m["id"] for m in models_data]
        result["available_models"] = available_models
        
        log.info(f"Groq: Authenticated successfully. Discovered {len(available_models)} capabilities.")
        
    except Exception as e:
        log.error(f"Groq discovery encountered a connectivity error: {e}")
        result["status"] = "error"
        
    return result
