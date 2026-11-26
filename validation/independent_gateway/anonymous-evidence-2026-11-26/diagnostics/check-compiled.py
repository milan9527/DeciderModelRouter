import gzip
import json
from pathlib import Path

import httpx

root = Path(__file__).parent
eager = {r["name"]: r["response"] for r in json.loads((root / "adapter-probe.json").read_text())["cases"]
         if r["adapter"]}
payloads = {c["name"]: c["payload"] for p in root.glob("cases-paired-*.json")
            for c in json.loads(p.read_text()) if c["name"] in eager}
rows = []
with httpx.Client(timeout=90, trust_env=False) as client:
    health = client.get("http://127.0.0.1:18814/health").json()
    for name, payload in payloads.items():
        r = client.post("http://127.0.0.1:18814/v1/systemone", json=payload)
        r.raise_for_status()
        actual = r.json()
        a, b = eager[name]["answers"]["action"], actual["answers"]["action"]
        row = {"name": name, "same_choice": a["choice"] == b["choice"],
               "max_probability_delta": max(abs(a["probabilities"][k] - b["probabilities"][k])
                                            for k in a["probabilities"]),
               "eager_service_ms": eager[name]["latency_ms"], "response": actual}
        assert actual["selection_adapter"]["active"] is True
        assert actual["forward_passes"] == 1
        rows.append(row)
        (root / "compiled-fidelity.json").write_text(json.dumps({"health": health, "cases": rows}))
    profiles = []
    for path in sorted((root / "baseline/base").glob("*.gz"))[:12]:
        raw = json.loads(gzip.decompress(path.read_bytes()))
        original = raw["route"]["gpu_calls"][0]
        r = client.post("http://127.0.0.1:18814/v1/systemone", json=original["payload"])
        r.raise_for_status()
        actual = r.json()
        answers = {k: {"choice": v["choice"]} for k, v in actual["answers"].items()}
        profiles.append({"case": raw["case_id"], "same_answers": answers == original["result"]["answers"],
                         "response": actual})
        assert actual["selection_adapter"]["active"] is False
        assert actual["forward_passes"] == 1
    (root / "compiled-profiles.json").write_text(json.dumps(profiles))
print("Same selections", sum(r["same_choice"] for r in rows), "/", len(rows))
print("Same profiles", sum(r["same_answers"] for r in profiles), "/", len(profiles))
assert all(r["same_choice"] for r in rows)
assert all(r["same_answers"] for r in profiles)
