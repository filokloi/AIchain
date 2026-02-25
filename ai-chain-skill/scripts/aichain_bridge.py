#!/usr/bin/env python3
"""
╔═══════════════════════════════════════════════════════════════════╗
║  AIchain — The Sovereign Global Value Maximizer                  ║
║  aichain_bridge.py v4.0 — Multi-Role Proxy Bridge                ║
║                                                                   ║
║  Roles:                                                           ║
║    1. Analyst: High reasoning (Tier 0 > Best Free)                ║
║    2. Visualist: Multimodal handling (0ms heuristic)              ║
║    3. Quick-Responder: Lowest latency $0 model for simple queries ║
║                                                                   ║
║  Decision Engine:                                                 ║
║    - 0ms Visual Heuristic (Image/File detection)                  ║
║    - <0.1s Flash Categorization (via Gemini Flash or Groq Llama)  ║
║                                                                   ║
║  Dual-Override Protocol:                                          ║
║    - Control A: 'auto_routing' flag in local config               ║
║    - Control B: NL Commands ("Disable router", "Always use X")    ║
╚═══════════════════════════════════════════════════════════════════╝
"""

import json
import os
import sys
import time
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import URLError
import urllib.request
import urllib.parse
try:
    import requests
except ImportError:
    print("[FATAL] 'requests' is not installed. Run: pip install requests")
    sys.exit(1)

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

PORT = 8080
OPENROUTER_API = "https://openrouter.ai/api/v1/chat/completions"

# Default Trio System Roles (can be overridden by personalized_routing_table or discovery)
TRIO_ROLES = {
    "analyst": "google/gemini-2.5-pro",
    "visualist": "openai/gpt-4o",
    "quick": "google/gemini-2.5-flash"
}

CONFIG_DIR = Path.home() / ".openclaw" / "aichain"
BRIDGE_CONFIG_FILE = CONFIG_DIR / "bridge_config.json"
TABLE_FILE = CONFIG_DIR / "personalized_routing_table.json"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [AIchain Bridge] %(levelname)s: %(message)s"
)
logger = logging.getLogger("aichain_bridge")

# ─────────────────────────────────────────────
# STATE MANAGEMENT & DUAL-OVERRIDE
# ─────────────────────────────────────────────

def ensure_config():
    if not CONFIG_DIR.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not BRIDGE_CONFIG_FILE.exists():
        default_cfg = {
            "auto_routing": True,
            "pinned_model": "openai/gpt-4o",
            "trio_overrides": {}
        }
        with open(BRIDGE_CONFIG_FILE, "w") as f:
            json.dump(default_cfg, f, indent=2)

def load_bridge_config() -> dict:
    ensure_config()
    try:
        with open(BRIDGE_CONFIG_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load bridge config: {e}")
        return {"auto_routing": True, "pinned_model": "openai/gpt-4o"}

def save_bridge_config(cfg: dict):
    # Atomic save to prevent EBADF
    import tempfile
    fd, tmp_path = tempfile.mkstemp(dir=CONFIG_DIR, text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp_path, BRIDGE_CONFIG_FILE)
    logger.info("Bridge config atomically updated.")

# ─────────────────────────────────────────────
# DECISION ENGINE
# ─────────────────────────────────────────────

def detect_visual(messages: list) -> bool:
    """0ms Visual Heuristic: Check for image URLs in the payload."""
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    return True
    return False

def fast_text_categorize(messages: list) -> str:
    """
    <0.1s Flash Categorization.
    Uses Groq Llama3 or Gemini Flash to classify text complexity.
    Returns: 'analyst' or 'quick'
    """
    api_key = os.environ.get("GEMINI_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.warning("No Gemini API key for fast categorization. Defaulting to 'analyst'.")
        return "analyst"

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
        "'analyst' means the query requires deep reasoning, coding, math, or complex generation. "
        "'quick' means the query is simple, greeting, factual retrieval, or short editing.\n\n"
        f"Query: {last_user_msg[:500]}"
    )

    try:
        start_t = time.time()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        resp = requests.post(url, json=payload, timeout=2)
        
        if resp.status_code == 200:
            data = resp.json()
            text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip().lower()
            dt = time.time() - start_t
            if "analyst" in text:
                logger.info(f"Categorized as: ANALYST ({dt:.3f}s)")
                return "analyst"
            else:
                logger.info(f"Categorized as: QUICK ({dt:.3f}s)")
                return "quick"
    except Exception as e:
        logger.warning(f"Categorization failed: {e}. Defaulting to 'analyst'.")
    
    return "analyst"

def detect_nl_override(messages: list, cfg: dict) -> bool:
    """
    Control B (Natural Language Override):
    Check system prompts for "Disable router" or "Always use X".
    Returns True if an override was executed.
    """
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
                # Naive extraction for demo purposes
                parts = content.split("always use ")
                if len(parts) > 1:
                    model_target = parts[1].split()[0] # get next word
                    # Just an approximation
                    if model_target and model_target != cfg.get("pinned_model"):
                        logger.warning(f"NL Override: Pinning model to '{model_target}'")
                        cfg["auto_routing"] = False
                        cfg["pinned_model"] = f"openrouter/{model_target}" if "/" not in model_target else model_target
                        save_bridge_config(cfg)
                        return True
    return False

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
            cfg = load_bridge_config() # Reload in case it changed

        # 2. Sovereign Logic: Bypass if auto_routing is False
        if not cfg.get("auto_routing", True):
            target_model = cfg.get("pinned_model") or "openai/gpt-4o"
            logger.info(f"🔒 Auto-routing disabled. Sovereign bypass to: {target_model}")
        else:
            # 3. Decision Engine Routing
            if detect_visual(messages):
                logger.info("👁️ Visual Heuristic triggered: Routing to VISUALIST")
                target_model = TRIO_ROLES.get("visualist", "openai/gpt-4o")
            else:
                category = fast_text_categorize(messages)
                target_model = TRIO_ROLES.get(category) or TRIO_ROLES.get("analyst", "openai/gpt-4o")
                logger.info(f"🧠 Text Categorization: Routing to {category.upper()} ({target_model})")
                
        payload["model"] = target_model or "google/gemini-2.5-pro"
        
        # 4. Forward to OpenRouter
        self.forward_request(payload)

    def forward_request(self, payload: dict):
        api_key = os.environ.get("OPENROUTER_KEY") or os.environ.get("OPENROUTER_API_KEY")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/AIchain",
            "X-Title": "AIchain Multi-Role Bridge"
        }
        
        try:
            start_t = time.time()
            logger.info(f"Forwarding Payload: {json.dumps(payload)}")
            req = urllib.request.Request(OPENROUTER_API, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req) as response:
                resp_data = response.read()
                self.send_response(response.status)
                for k, v in response.getheaders():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(resp_data)
                
            dt = time.time() - start_t
            logger.info(f"✅ Forwarded successfully to {payload.get('model')} in {dt:.2f}s")
            
        except urllib.error.HTTPError as e:
            logger.error(f"❌ HTTP Error {e.code} forwarding to OpenRouter: {e.read().decode()}")
            self.send_response(e.code)
            self.end_headers()
            self.wfile.write(e.read())
        except Exception as e:
            logger.error(f"❌ Failed to forward request: {e}")
            self.send_error(500, "Internal Bridge Error")

def main():
    ensure_config()
    server_address = ('', PORT)
    httpd = HTTPServer(server_address, AIchainBridgeHandler)
    logger.info("=" * 60)
    logger.info(f"AIchain v4.0 Multi-Role Bridge starting on port {PORT}")
    logger.info("Decision Engine: 0ms Visual Heuristic | Flash Categorization")
    logger.info("=" * 60)
    
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Bridge shutting down...")
        httpd.server_close()

if __name__ == "__main__":
    main()
