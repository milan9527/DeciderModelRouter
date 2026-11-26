import gzip
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

root = Path(__file__).parent


def read(path):
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if path.suffix == ".gz" else raw)


def percentile(values, q):
    values = sorted(v for v in values if v is not None)
    return round(values[max(0, math.ceil(len(values) * q) - 1)], 3) if values else None


def arm(folder):
    routes, outputs = [], []
    base = {}
    for p in (folder / "base").glob("*.gz"):
        r = read(p)
        base[r["case_id"]] = r
        if r.get("route"):
            routes.append(r["route"])
        if r.get("output"):
            outputs.append(r["output"])
    for p in (folder / "industries").glob("*.gz"):
        for turn in read(p).get("turns", []):
            for step in turn.get("steps", []):
                if step.get("route"):
                    routes.append(step["route"])
                if step.get("output"):
                    outputs.append(step["output"])
    known = [o["observed_reference_cost_usd"] for o in outputs
             if o.get("observed_reference_cost_usd") is not None]
    summary = read(folder / "summary.json")
    summary["performance"] = {
        "routes": len(routes), "gpu_http_calls": sum(len(r["gpu_calls"]) for r in routes),
        "two_gpu_routes": sum(len(r["gpu_calls"]) == 2 for r in routes),
        "route_p50_ms": percentile([r["route_ms"] for r in routes], .5),
        "route_p95_ms": percentile([r["route_ms"] for r in routes], .95),
        "decision_p50_ms": percentile([r["selection"].get("decision_ms") for r in routes], .5),
        "decision_p95_ms": percentile([r["selection"].get("decision_ms") for r in routes], .95),
        "target_p50_ms": percentile([o.get("backend_total_ms") for o in outputs], .5),
        "target_p95_ms": percentile([o.get("backend_total_ms") for o in outputs], .95),
        "known_reference_cost_usd": round(math.fsum(float(v) for v in known), 8), "cost_known_outputs": len(known),
        "recorded_outputs": len(outputs),
        "cache_read_tokens": sum((o.get("cache") or {}).get("read_tokens", 0) for o in outputs),
        "cache_write_tokens": sum((o.get("cache") or {}).get("write_tokens", 0) for o in outputs),
        "selected_models": dict(Counter(r["selection"]["selected_model_id"] for r in routes)),
        "timing_boundary": "local backend and SSM-forwarded private HTTP; excludes evaluation admission queue",
        "limits": "Baseline overlapped training; initial adapter run overlapped probes. Do not infer a causal speedup from cross-arm batch timings.",
    }
    return summary, base


report = {"arms": {}, "scope": "Targeted evaluation, not universal router accuracy"}
bases = {}
for name in ["baseline", "optimized", "final"]:
    report["arms"][name], bases[name] = arm(root / {"optimized": "adapter-initial"}.get(name, name))
for name in ["optimized", "final"]:
    paired = [(a["route"]["gpu_calls"][0], bases[name][cid]["route"]["gpu_calls"][0])
              for cid, a in bases["baseline"].items()]
    report["arms"][name]["first_assessment_vs_baseline"] = {
        "compared": len(paired),
        "same_input": sum(a["payload"] == b["payload"] for a, b in paired),
        "same_answers": sum(a["result"]["answers"] == b["result"]["answers"] for a, b in paired),
    }
fidelity = read(root / "diagnostics/compiled-fidelity.json.gz")["cases"]
profiles = read(root / "diagnostics/compiled-profiles.json")
report["compiled_fidelity"] = {
    "same_selection": sum(r["same_choice"] for r in fidelity), "total_selection": len(fidelity),
    "same_profile": sum(r["same_answers"] for r in profiles), "total_profile": len(profiles),
    "maximum_probability_delta": max(r["max_probability_delta"] for r in fidelity),
    "compiled_service_p50_ms": percentile([r["response"]["latency_ms"] for r in fidelity], .5),
    "compiled_service_p95_ms": percentile([r["response"]["latency_ms"] for r in fidelity], .95),
    "eager_service_p50_ms": percentile([r["eager_service_ms"] for r in fidelity], .5),
    "timing_limit": "Eager measurements partly overlapped other probes; not an isolated speedup benchmark.",
}
cost = read(root / "diagnostics/cost-probe.json.gz")["cases"]
report["counterfactual_prices"] = {
    str(active): {"passed": sum(r["matches_predefined_preference"] for r in cost if r["adapter"] == active),
                  "total": sum(r["adapter"] == active for r in cost)}
    for active in [False, True]
}
probe = read(root / "diagnostics/adapter-probe.json.gz")["cases"]
report["candidate_order"] = {}
for active in [False, True]:
    subset = {r["name"]: r for r in probe if r["adapter"] == active}
    pairs = [(r, subset[name[:-1] + "1"]) for name, r in subset.items() if name.endswith("/0")]
    report["candidate_order"][str(active)] = {
        "same_model": sum(a["selected"]["id"] == b["selected"]["id"] for a, b in pairs),
        "same_configuration": sum(a["selected"]["candidate_id"] == b["selected"]["candidate_id"]
                                  for a, b in pairs),
        "pairs": len(pairs),
    }
factory = bases["final"]["manufacturing-causal-regression"]
report["factory"] = {
    "model": factory["route"]["selection"]["selected_model_id"],
    "configuration": factory["route"]["selected_candidate"]["benchmark"]["request_fields"],
    "route_ms": factory["route_ms"], "target_ms": factory["output"]["backend_total_ms"],
    "output_sha256": hashlib.sha256(factory["output"]["text"].encode()).hexdigest(),
}
(root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
for name, data in report["arms"].items():
    print(name, data["overall"], data["performance"])
