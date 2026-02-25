import logging

try:
    import requests
except ImportError:
    requests = None

def discover_capabilities(keys: dict, log: logging.Logger) -> dict:
    """Discover capabilities from Gemini API."""
    result = {
        "status": "unconfigured",
        "available_models": [],
        "cost_mode": "api-per-token",
        "limits": {}
    }
    
    if not requests:
        log.error("python 'requests' library missing. Cannot discover Gemini capabilities.")
        result["status"] = "error"
        return result

    api_key = keys.get("GOOGLE_API_KEY") or keys.get("GEMINI_API_KEY")
    
    if not api_key:
        log.info("Gemini capability discovery skipped (no API key configured).")
        return result
        
    log.info("Fetching Gemini capabilities...")
    
    try:
        # Check models endpoint using the API key in the URL
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        models_resp = requests.get(url, timeout=15)
        
        if models_resp.status_code != 200:
            log.warning(f"Gemini authentication or fetch failed (HTTP {models_resp.status_code}).")
            result["status"] = "auth_failed"
            return result
            
        result["status"] = "authenticated"
        models_data = models_resp.json().get("models", [])
        
        # Format the IDs to align closely with standard AIchain/OpenRouter naming if possible
        # Gemini native model list returns names like 'models/gemini-1.5-pro'
        available_models = [m["name"].replace("models/", "google/") for m in models_data]
        
        # Additionally, push native format and standard versions
        standardized = []
        for am in available_models:
            standardized.append(am)
            if "gemini" in am:
                # E.g. 'google/gemini-1.5-pro' -> 'google/gemini-pro-1.5' (OR routing style mapping fallback)
                pass # Mapping logic is handled best at router compile time, just return raw for now.
                
        result["available_models"] = standardized
        log.info(f"Gemini: Authenticated successfully. Discovered {len(standardized)} capabilities.")
        
    except Exception as e:
        log.error(f"Gemini discovery encountered a connectivity error: {e}")
        result["status"] = "error"
        
    return result
