"""Summarize preserved industry jobs; completion is not business acceptance."""

import argparse
import json
import math
from collections import Counter
from decimal import Decimal
from pathlib import Path


def percentiles(values):
    ordered = sorted(v for v in values if v is not None)
    return {
        "samples": len(ordered),
        "p50_ms": ordered[math.ceil(len(ordered) * .5) - 1] if ordered else None,
        "p95_ms": ordered[math.ceil(len(ordered) * .95) - 1] if ordered else None,
        "method": "nearest_rank",
    }


def summarize(root):
    batches = {}
    standard = {}
    jobs = {}
    for path in sorted(root.rglob("*.json")):
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or not data.get("scenario_id") or not isinstance(data.get("steps"), list):
            continue
        if data["id"] in jobs:
            continue
        jobs[data["id"]] = (path, data)
    for path, job in jobs.values():
        batch = path.relative_to(root).parts[0]
        bucket = batches.setdefault(batch, [])
        bucket.append(job)
        if batch in ("primary-8192", "completion-16384") and job["status"] == "completed":
            standard.setdefault(job["scenario_id"], set()).add(job["turn"])
    result = {
        "scope": "real_calls_with_synthetic_business_records",
        "completion_is_not_business_acceptance": True,
        "standard_exercise": {
            "planned": 48, "completed": sum(len(turns) for turns in standard.values()),
            "industries": {k: {"completed_turns": sorted(v), "planned_turns": 4} for k, v in sorted(standard.items())},
            "batches": ["primary-8192", "completion-16384"],
            "note": "累积补测完成数，不是单一版本首次成功率；进阶追问另列。",
        },
        "batches": {},
    }
    for name, rows in batches.items():
        steps = [step for row in rows for step in row["steps"]]
        successful = [s for s in steps if s["status"] == "completed"]
        prices = [s.get("reference_cost_usd") for s in successful]
        result["batches"][name] = {
            "attempted_turns": len(rows), "statuses": dict(Counter(j["status"] for j in rows)),
            "router_steps": len(steps), "completed_router_steps": len(successful),
            "models_completed": dict(Counter(s.get("model_id") for s in successful)),
            "tool_calls": sum(j["metrics"]["tool_calls"] for j in rows),
            "model_switches": sum(j["metrics"]["model_switches"] for j in rows),
            "retained_calls": sum(j["metrics"]["retained_calls"] for j in rows),
            "cache_read_tokens": sum(j["metrics"]["cache_read_tokens"] for j in rows),
            "gpu_completed_call_latency": percentiles(s.get("gpu_ms") for s in successful),
            "target_completed_call_latency": percentiles(s.get("target_ms") for s in successful),
            "job_completed_latency": percentiles(
                j["finished_at"] - j["created_at"] for j in rows if j["status"] == "completed"),
            "known_reference_cost_usd": (
                str(sum((Decimal(str(p)) for p in prices if p is not None), Decimal(0)))
                if any(p is not None for p in prices) else None),
            "completed_calls_with_unknown_cost": sum(p is None for p in prices),
            "cost_note": "仅已完成且可计价调用的小计；不含未知缓存费用、失败用量、GPU、ECS或AgentCore费用，不是账单。",
        }
    audit = root / "call-audit.json"
    if audit.exists():
        calls = json.loads(audit.read_text())
        result["all_attempts"] = {
            "router_history_records": len(calls["records"]),
            "gpu_calls": calls["gpu_calls"],
            "target_attempts": calls["target_attempts"],
            "separate_probes": calls["separate_probes"],
            "note": "含早期失败、旧策略与所有补测；按真实历史run_id去重。",
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    result = summarize(args.directory)
    (args.directory / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result["standard_exercise"], ensure_ascii=False))


if __name__ == "__main__":
    main()
