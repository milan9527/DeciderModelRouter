"""Summarize the frozen quality-priority suite without rerunning failed cases."""

import argparse
from collections import Counter
from pathlib import Path

from app.gateway_store import percentile
from scripts.run_profile_quality import read, save
from scripts.summarize_latency_quality import counts, summarize


def report(directory):
    folder = directory / "quality/optimized"
    summary = summarize(folder)
    routes = [row["route"] for path in sorted((folder / "base").glob("*.json.gz"))
              if (row := read(path)).get("route")]
    for path in sorted((folder / "industries").glob("*.json.gz")):
        for turn in read(path)["turns"]:
            routes.extend(step["route"] for step in turn.get("steps", []) if step.get("route"))
    high = [r for r in routes if r["selection"].get("quality_policy", {}).get("active")]
    return {
        "suite_version": summary["suite_version"],
        "overall": summary["overall"], "categories": summary["categories"],
        "regression44": counts([r for r in summary["rows"] if r["split"] != "new_holdout"]),
        "new12": counts([r for r in summary["rows"] if r["split"] == "new_holdout"]),
        "routes": len(routes), "high_preference_routes": len(high),
        "high_preference_quality_checks_passed": sum(
            r["selection"]["selected_candidate_id"] in r["selection"]["quality_policy"]["allowed_candidate_ids"]
            for r in high),
        "quality_reselections": sum(r["selection"].get("quality_reselection_count", 0) for r in routes),
        "all_candidates_in_first_selection": all(
            r["selection"]["candidate_count"] == r["selection"]["evaluated_configurations"]
            == len(r["selection"]["decision_calls"][1]["candidate_ids"])
            == len(r["gpu_calls"][1]["payload"]["questions"]["action"]["criteria"])
            for r in routes),
        "gpu_call_counts": dict(Counter(len(r["gpu_calls"]) for r in routes)),
        "selected_models": dict(Counter(r["selection"]["selected_model_id"] for r in routes)),
        "timings": {
            key: {"p50": percentile([r["selection"].get(key) for r in routes], .5),
                  "p95": percentile([r["selection"].get(key) for r in routes], .95)}
            for key in ["assessment_ms", "selection_ms", "decision_ms"]
        },
        "failures": [
            {"case_id": r["case_id"], "content_status": r["content_status"],
             "execution_status": r["execution_status"],
             "reason": r["review"].get("reason", r["review"].get("source"))}
            for r in summary["rows"] if r["status"] != "pass"
        ],
        "limits": summary["limits"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    result = report(args.input)
    save(args.input / "results.json", result)
    print(result["overall"])
