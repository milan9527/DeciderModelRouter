"""Counterfactual prices only; public scores and execution conditions stay fixed."""
import copy
import json
import re
from pathlib import Path

import httpx

root = Path(__file__).parent
base = next(c["payload"] for c in json.loads((root / "cases-paired-0.json").read_text())
            if "/new/0" in c["name"])
state = json.loads(base["state"])
meta = json.loads((root / "paired-metadata.json").read_text())["manufacturing-liquid/high/new/0"]
strong = next(k for k, r in meta["aliases"].items()
              if r["id"] == "openai.gpt-6-astra" and r["configuration"] == {})
near = next(k for k, r in meta["aliases"].items()
            if r["id"] == "openai.gpt-6.2-sol" and r["configuration"] == {})
preferences = {
    "high": "Prioritize task-relevant capability.",
    "medium": "Balance task-relevant capability against cost.",
    "low": "Save cost while meeting this task's capability needs.",
}
cases = []
for tier, preference in preferences.items():
    for cheap in (strong, near):
        for reverse in (False, True):
            payload = copy.deepcopy(base)
            st = copy.deepcopy(state)
            st["preference"] = preference
            order = [near, strong] if reverse else [strong, near]
            st["candidates"] = {
                k: re.sub(r"; estimated USD=[^;]+", f"; estimated USD={0.0001 if k == cheap else 0.02}",
                          st["candidates"][k]) for k in order}
            payload["state"] = json.dumps(st, ensure_ascii=False, separators=(",", ":"))
            payload["questions"]["action"]["criteria"] = {k: "Use candidate " + k for k in order}
            cases.append({"tier": tier, "cheap": cheap, "order": order, "payload": payload,
                          "expected": strong if tier == "high" else cheap})
(root / "cost-cases.json").write_text(json.dumps(cases, ensure_ascii=False))
out = {"scope": "Synthetic price sensitivity probe, not business accuracy or actual billing.",
       "strong": meta["aliases"][strong], "near": meta["aliases"][near], "cases": []}
with httpx.Client(timeout=90, trust_env=False) as client:
    for case in cases:
        for enabled in (False, True):
            response = client.post("http://127.0.0.1:18814/v1/systemone",
                                   json={**case["payload"], "adapter": enabled})
            response.raise_for_status()
            result = response.json()
            out["cases"].append({k: v for k, v in case.items() if k != "payload"} | {
                "adapter": enabled, "response": result,
                "matches_predefined_preference": result["answers"]["action"]["choice"] == case["expected"]})
            (root / "cost-probe.json").write_text(json.dumps(out))
print({str(enabled): sum(r["matches_predefined_preference"] for r in out["cases"]
                         if r["adapter"] == enabled) for enabled in (False, True)})
