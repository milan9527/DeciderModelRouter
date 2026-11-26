"""Matched eager-engine comparison; fixed checkpoint, full pools, two orders."""
import json
import time
from pathlib import Path

import httpx

root = Path(__file__).parent
metadata = json.loads((root / "paired-metadata.json").read_text())
cases = [c for p in sorted(root.glob("cases-paired-*.json"))
         for c in json.loads(p.read_text()) if metadata[c["name"]]["version"] == "new"]
output = root / "adapter-probe.json"
assert not output.exists()
results = []
with httpx.Client(timeout=90, trust_env=False) as client:
    health = client.get("http://127.0.0.1:18814/health").json()
    for case in cases:
        for enabled in [False, True]:
            started = time.perf_counter()
            response = client.post("http://127.0.0.1:18814/v1/systemone",
                                   json={**case["payload"], "adapter": enabled})
            response.raise_for_status()
            raw = response.json()
            m = metadata[case["name"]]
            choice = raw["answers"]["action"]["choice"]
            selected = m["aliases"].get(choice)
            row = {"name": case["name"], "adapter": enabled, "response": raw,
                   "selected": selected, "http_ms": (time.perf_counter()-started)*1000}
            results.append(row)
            output.write_text(json.dumps({"health": health, "cases": results}))
            print(json.dumps({"name": case["name"], "adapter": enabled,
                              "selected": selected["id"] if selected else None}), flush=True)
