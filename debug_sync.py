from scripts.sync import apply_scenario_recalculation
import json
import logging

log = logging.getLogger()
table = json.load(open(r'C:\Users\filok\OneDrive\Desktop\AI chain for Open Claw envirement\ai_routing_table.json'))
scenario = json.load(open(r'C:\Users\filok\OneDrive\Desktop\AI chain for Open Claw envirement\ai-chain-skill\scenarios\openai_plus.json'))
out = apply_scenario_recalculation(table, scenario, log)

for e in out["routing_hierarchy"][:10]:
    print(f"{e['model']} | Score: {e['value_score']:.1f} | Cost: {e['metrics'].get('effective_cost')}")
