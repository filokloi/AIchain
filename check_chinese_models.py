#!/usr/bin/env python3
import json, requests, os
cfg_path = r"C:\Users\filok\AppData\Roaming\npm\node_modules\openclaw\skills\ai-chain-skill\bridge_config.json"
with open(cfg_path) as f: cfg = json.load(f)
tbl_url = cfg['routing_url']
print(f"Routing table URL: {tbl_url}")
try:
    r = requests.get(tbl_url, timeout=10)
    if r.status_code == 200:
        table = r.json()
        models = table.get('routing_hierarchy', [])
        print(f"Total models in table: {len(models)}")
        # Chinese/East Asian providers
        keywords = ['qwen', 'stepfun', 'minimax', 'yi', 'baichuan', '01-ai', 'bytedance', 'kimi', 'glm']
        chinese = [m for m in models if any(k in m.get('provider','').lower() or k in m.get('model','').lower() for k in keywords)]
        print(f"Chinese/East Asian models found: {len(chinese)}")
        for m in chinese[:15]:
            print(f"- {m['model']} | {m['provider']} | tier={m.get('tier')} | intel={m['metrics']['intelligence']} | cost=${m['metrics']['cost']:.6f}")
    else:
        print(f"Fetch failed: {r.status_code}")
except Exception as e:
    print(f"Error: {e}")
