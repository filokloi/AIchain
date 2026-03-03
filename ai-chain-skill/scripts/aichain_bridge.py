#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════════╗
║  AIchain — The Sovereign Global Value Maximizer                   ║
║  aichain_bridge.py v4.0 — Universal Arbitrator (Two-Brain)        ║
║                                                                   ║
║  Logics:                                                          ║
║    - Fast Brain (Arbitrator): Best $0/Fast model.                 ║
║      Evaluates user queries in <0.2s to classify them.            ║
║    - Heavy Brain (Analytic): Highest Intelligence model.          ║
║      Takes over only when Fast Brain deems the query complex.     ║
║                                                                   ║
║  Universal Proxy:                                                 ║
║    Routes Google to Google, OpenAI to OpenAI, OpenRouter to OR.   ║
╚═══════════════════════════════════════════════════════════════════╝
"""

import json
import os
import sys
import time
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import urllib.request
import urllib.parse
from urllib.error import URLError
try:
    import requests
except ImportError:
    print("[FATAL] 'requests' is not installed. Run: pip install requests")
    sys.exit(1)

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

PORT = 8080
ROUTING_URL = "https://filokloi.github.io/AIchain/ai_routing_table.json"

CONFIG_DIR = Path.home() / ".openclaw" / "aichain"
BRIDGE_CONFIG_FILE = CONFIG_DIR / "bridge_config.json"
CACHE_FILE = CONFIG_DIR / "routing_table_cache.json"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [AIchain Bridge] %(levelname)s: %(message)s"
)
logger = logging.getLogger("aichain_bridge")

# ─────────────────────────────────────────────
# PROVIDER ENDPOINTS (OpenAI Compatible)
# ─────────────────────────────────────────────
ENDPOINTS = {
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    "openai": "https://api.openai.com/v1/chat/completions",
    "deepseek": "https://api.deepseek.com/v1/chat/completions",
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "anthropic": "https://api.anthropic.com/v1/messages" # Anthropic uses native format, but we'll try to stick to OR if possible for them unless they support OpenAI compat
}

# ─────────────────────────────────────────────
# STATE MANAGEMENT
# ─────────────────────────────────────────────

def ensure_config():
    if not CONFIG_DIR.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not BRIDGE_CONFIG_FILE.exists():
        default_cfg = {
            "auto_routing": True,
            "pinned_model": "openai/gpt-4o",
        }
        with open(BRIDGE_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(default_cfg, f, indent=2)

def load_bridge_config() -> dict:
    ensure_config()
    try:
        with open(BRIDGE_CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load bridge config: {e}")
        return {"auto_routing": True, "pinned_model": "openai/gpt-4o"}

def save_bridge_config(cfg: dict):
    import tempfile
    fd, tmp_path = tempfile.mkstemp(dir=CONFIG_DIR, text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp_path, BRIDGE_CONFIG_FILE)

# ─────────────────────────────────────────────
# TWO-BRAIN DYNAMIC ROUTING
# ─────────────────────────────────────────────

def get_dynamic_roles():
    """Fetches the routing table and assigns Fast Brain and Heavy Brain."""
    table = None
    # 1. Try to fetch from web
    try:
        resp = requests.get(ROUTING_URL, timeout=10)
        if resp.status_code == 200:
            table = resp.json()
            # Cache it
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(table, f)
            logger.info("Fetched fresh routing table from GitHub.")
    except Exception as e:
        logger.warning(f"Failed to fetch live routing table: {e}")
    
    # 2. Fallback to cache
    if not table and CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                table = json.load(f)
            logger.info("Loaded routing table from local cache.")
        except:
            pass
            
    # Default fallback if all fails
    roles = {
        "fast_brain": "openrouter/google/gemini-2.5-flash:free",
        "heavy_brain": "openrouter/google/gemini-2.5-pro",
        "visual_brain": "openrouter/openai/gpt-4o"
    }
    
    if table and "routing_hierarchy" in table:
        hierarchy = table["routing_hierarchy"]
        
        # Fast Brain: Highest value score that is FREE ($0)
        fast_brain = None
        for m in hierarchy:
            if m.get("tier") in ("FREE_FRONTIER", "OAUTH_BRIDGE") or m.get("metrics", {}).get("cost", 1) <= 0.0:
                fast_brain = m["model"]
                break
        
        # Heavy Brain: Highest pure intelligence
        heavy_brain = table.get("heavy_hitter", {}).get("model")
        if not heavy_brain or heavy_brain == "N/A":
            if hierarchy:
                heavy_brain = sorted(hierarchy, key=lambda x: x.get("metrics", {}).get("intelligence", 0), reverse=True)[0]["model"]
                
        if fast_brain: roles["fast_brain"] = fast_brain
        if heavy_brain: roles["heavy_brain"] = heavy_brain
        
    return roles

def detect_visual(messages: list) -> bool:
    """0ms Visual Heuristic."""
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    return True
    return False

def fast_text_categorize(messages: list, fast_model: str) -> str:
    """
    Two-Brain Arbitrator Logic.
    Uses Fast Brain to classify text complexity.
    """
    # Extract user prompt
    last_user_msg = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                last_user_msg = content
            elif isinstance(content, list):
                last_user_msg = " ".join([p.get("text", "") for p in content if p.get("type") == "text"])
            break

    if not last_user_msg:
        return "quick"

    prompt = (
        "Classify the complexity of the following user query into exactly one word: 'analyst' or 'quick'. "
        "'analyst' means the query requires deep reasoning, complex coding, math, or heavy synthesis. "
        "'quick' means the query is simple, greeting, factual retrieval, or short editing.\n\n"
        f"Query: {last_user_msg[:1000]}"
    )

    try:
        start_t = time.time()
        # Fast Brain classification request
        key, ep = get_endpoint_for_model(fast_model)
        if not key or not ep:
            return "analyst"
            
        model_val = fast_model
        if fast_model.startswith("openrouter/"):
            model_val = fast_model.replace("openrouter/", "", 1)
        
        payload = {
            "model": model_val,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 5,
            "temperature": 0.0
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        
        resp = requests.post(ep, json=payload, headers=headers, timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            text = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip().lower()
            dt = time.time() - start_t
            if "analyst" in text:
                logger.info(f"🧠 Fast Brain decided: ANALYST [Task is complex] ({dt:.3f}s)")
                return "analyst"
            else:
                logger.info(f"⚡ Fast Brain decided: QUICK [Task is simple] ({dt:.3f}s)")
                return "quick"
    except Exception as e:
        logger.warning(f"Arbitrator failed to classify: {e}. Defaulting to 'analyst'.")
    
    return "analyst"

def detect_nl_override(messages: list, cfg: dict) -> bool:
    """Control B (Natural Language Override)"""
    for msg in messages:
        if msg.get("role") == "system":
            content = msg.get("content", "").lower()
            if "disable router" in content or "auto_routing=false" in content:
                if cfg.get("auto_routing"):
                    logger.warning("NL Override: Disabling Auto-Routing!")
                    cfg["auto_routing"] = False
                    save_bridge_config(cfg)
                return True
            if "always use " in content:
                parts = content.split("always use ")
                if len(parts) > 1:
                    model_target = parts[1].split()[0]
                    if model_target and model_target != cfg.get("pinned_model"):
                        logger.warning(f"NL Override: Pinning model to '{model_target}'")
                        cfg["auto_routing"] = False
                        cfg["pinned_model"] = f"openrouter/{model_target}" if "/" not in model_target else model_target
                        save_bridge_config(cfg)
                        return True
    return False

# ─────────────────────────────────────────────
# UNIVERSAL API ROUTING
# ─────────────────────────────────────────────

def get_endpoint_for_model(model_id: str):
    """Returns (api_key, endpoint_url) based on provider prefix or environment variables."""
    prefix = model_id.split("/")[0].lower() if "/" in model_id else "openrouter"
    
    # Try environmental variables first based on prefix
    g_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GEMINI_KEY")
    oa_key = os.environ.get("OPENAI_API_KEY")
    ds_key = os.environ.get("DEEPSEEK_API_KEY")
    gq_key = os.environ.get("GROQ_API_KEY") or os.environ.get("GROQ_KEY")
    
    if prefix == "google" and g_key:
        key = g_key
        ep = ENDPOINTS["google"]
    elif prefix == "openai" and oa_key:
        key = oa_key
        ep = ENDPOINTS["openai"]
    elif prefix == "deepseek" and ds_key:
        key = ds_key
        ep = ENDPOINTS["deepseek"]
    elif prefix == "groq" and gq_key:
        key = gq_key
        ep = ENDPOINTS["groq"]
    else:
        # Default OpenRouter fallback (even for missing native keys)
        key = os.environ.get("OPENROUTER_KEY") or os.environ.get("OPENROUTER_API_KEY")
        ep = ENDPOINTS["openrouter"]
        
        # If the target model was native (e.g. "openai/gpt-4o") and we fell back to OpenRouter,
        # OpenRouter wants the model format as "openai/gpt-4o" (so no change)
        
    return key, ep

# ─────────────────────────────────────────────
# HTTP PROXY HANDLER
# ─────────────────────────────────────────────

class AIchainBridgeHandler(BaseHTTPRequestHandler):
    
    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_error(404, "Not Found")
            return
            
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, "Bad Request")
            return

        messages = payload.get("messages", [])
        cfg = load_bridge_config()
        
        # 1. Check NL Overrides
        nl_triggered = detect_nl_override(messages, cfg)
        if nl_triggered:
            cfg = load_bridge_config()

        # 2. Sovereign Logic
        if not cfg.get("auto_routing", True):
            target_model = cfg.get("pinned_model") or "openrouter/openai/gpt-4o"
            logger.info(f"🔒 God Mode / Bypass Active. Routing to: {target_model}")
        else:
            # Refresh roles
            roles = get_dynamic_roles()
            if detect_visual(messages):
                logger.info("👁️ Visual Heuristic triggered: Routing to VISUALIST")
                target_model = roles.get("visual_brain", "openrouter/openai/gpt-4o")
            else:
                category = fast_text_categorize(messages, fast_model=roles["fast_brain"])
                target_model = roles["heavy_brain"] if category == "analyst" else roles["fast_brain"]
                
        # Prep payload model name (some APIs need prefix stripped, but OpenAI compat usually ignores unused prefixes, however Google/DeepSeek strictness might require stripping)
        # We will strip the prefix ONLY IF it is going to a native provider API that isn't OpenRouter.
        api_key, endpoint_url = get_endpoint_for_model(target_model)
        
        # Strip provider prefixes strictly
        model_val = target_model
        if target_model.startswith("openrouter/"):
            model_val = target_model.replace("openrouter/", "", 1)
        elif "/" in target_model:
            prefix = target_model.split("/")[0]
            # for native google/openai endpoints, remove their own prefix
            if prefix in ("google", "openai", "deepseek", "anthropic", "groq"):
                model_val = target_model.replace(f"{prefix}/", "", 1)
                
        payload["model"] = model_val
            
        # 4. Forward
        self.forward_request(payload, api_key, endpoint_url, target_model)

    def forward_request(self, payload: dict, api_key: str, endpoint_url: str, original_model_id: str, is_retry: bool = False):
        if not api_key:
            logger.error(f"❌ Missing API key for endpoint {endpoint_url} (Model: {original_model_id})")
            self.send_error(500, "Missing required API Key")
            return
            
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/AIchain",
            "X-Title": "AIchain Two-Brain Arbitrator"
        }
        
        try:
            start_t = time.time()
            logger.info(f"🚀 Forwarding to {endpoint_url} with model {payload.get('model')}")
            req = urllib.request.Request(endpoint_url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=60) as response:
                resp_data = response.read()
                self.send_response(response.status)
                for k, v in response.getheaders():
                    # skip transfer-encoding to avoid issues
                    if k.lower() != "transfer-encoding":
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(resp_data)
                
            dt = time.time() - start_t
            logger.info(f"✅ Forwarded successfully to {original_model_id} in {dt:.2f}s")
            
        except urllib.error.HTTPError as e:
            err_data = e.read()
            if e.code == 401 and not is_retry and endpoint_url != ENDPOINTS["openrouter"]:
                logger.warning(f"Native API returned 401 Unauthorized. Retrying via OpenRouter...")
                fallback_key = os.environ.get("OPENROUTER_KEY") or os.environ.get("OPENROUTER_API_KEY")
                if fallback_key:
                    payload["model"] = original_model_id.replace("openrouter/", "", 1) if original_model_id.startswith("openrouter/") else original_model_id
                    self.forward_request(payload, fallback_key, ENDPOINTS["openrouter"], original_model_id, is_retry=True)
                    return
                    
            logger.error(f"❌ HTTP Error {e.code}: {err_data.decode()[:200]}")
            self.send_response(e.code)
            self.end_headers()
            self.wfile.write(err_data)
        except Exception as e:
            logger.error(f"❌ Failed to forward request: {e}")
            self.send_error(500, "Internal Bridge Error")

def main():
    ensure_config()
    server_address = ('', PORT)
    httpd = HTTPServer(server_address, AIchainBridgeHandler)
    logger.info("=" * 64)
    logger.info(f"AIchain v4.0 Universal Arbitrator (Two-Brain) on port {PORT}")
    roles = get_dynamic_roles()
    logger.info(f"⚡ Fast Brain  : {roles.get('fast_brain')}")
    logger.info(f"🧠 Heavy Brain : {roles.get('heavy_brain')}")
    logger.info("=" * 64)
    
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Bridge shutting down...")
        httpd.server_close()

if __name__ == "__main__":
    main()
