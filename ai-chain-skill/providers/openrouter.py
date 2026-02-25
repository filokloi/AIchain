import logging

try:
    import requests
except ImportError:
    requests = None

def discover_capabilities(keys: dict, log: logging.Logger) -> dict:
    """Discover capabilities from OpenRouter API."""
    result = {
        "status": "unconfigured",
        "available_models": [],
        "cost_mode": "api-per-token",
        "limits": {}
    }
    
    if not requests:
        log.error("python 'requests' library missing. Cannot discover OpenRouter capabilities.")
        result["status"] = "error"
        return result

    # Check for direct key, or environmental key name as a string mapping
    api_key = keys.get("OPENROUTER_API_KEY") 
    
    if not api_key:
        log.info("OpenRouter capability discovery skipped (no API key configured).")
        return result
        
    log.info("Authenticating with OpenRouter capabilities...")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://github.com/filok94/AIchain",
        "X-Title": "AIchain Capability Discovery"
    }
    
    try:
        # Check Auth
        auth_resp = requests.get("https://openrouter.ai/api/v1/auth/key", headers=headers, timeout=15)
        if auth_resp.status_code != 200:
            log.warning(f"OpenRouter authentication failed (HTTP {auth_resp.status_code}).")
            result["status"] = "auth_failed"
            return result
            
        auth_data = auth_resp.json().get("data", {})
        
        # Determine strict limits
        result["limits"] = {
            "credit_limit": auth_data.get("limit"),
            "credit_usage": auth_data.get("usage"),
            "credit_remaining": auth_data.get("limit_remaining"),
            "is_free_tier": auth_data.get("is_free_tier", False)
        }
        result["status"] = "authenticated"
        
        # Check models endpoint
        models_resp = requests.get("https://openrouter.ai/api/v1/models", headers=headers, timeout=15)
        if models_resp.status_code != 200:
            log.warning(f"OpenRouter models fetch failed (HTTP {models_resp.status_code}).")
            return result
            
        models_data = models_resp.json().get("data", [])
        
        # For openrouter, the IDs match the global routing table exactly
        available_models = [m["id"] for m in models_data]
        result["available_models"] = available_models
        log.info(f"OpenRouter: Authenticated successfully. Discovered {len(available_models)} capabilities.")
        
    except Exception as e:
        log.error(f"OpenRouter discovery encountered a connectivity error: {e}")
        result["status"] = "error"
        
    return result
