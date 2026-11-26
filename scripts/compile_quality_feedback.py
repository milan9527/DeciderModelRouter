"""Compile frozen, reviewed standalone outputs; never label mixed-model Agent answers per model."""

import argparse
import hashlib
import json
from pathlib import Path

from app.quality_feedback import digest
from scripts.run_profile_quality import read
from scripts.summarize_latency_quality import summarize


def compile_feedback(directory):
    records = []
    for arm in ("baseline", "optimized"):
        folder = directory / "quality" / arm
        summary = summarize(folder)
        for row in summary["rows"]:
            if row["category"] == "Agent多轮":
                continue
            raw_path = folder / "base" / (row["case_id"] + ".json.gz")
            raw = read(raw_path)
            if row["status"] not in {"pass", "fail"} or raw.get("status") != "completed":
                continue
            route = raw["route"]
            chosen = route["selected_candidate"]
            records.append({
                "case_id": row["case_id"], "arm": arm, "candidate_id": chosen["candidate_id"],
                "model_id": chosen["model"]["id"], "invoke_id": chosen["model"]["invoke_id"],
                "workflow": route["profile"]["workflow"],
                "status": row["status"], "content_status": row["content_status"],
                "failure_category": row["category"] if row["content_status"] == "fail" else "输出格式",
                "output_cap": route["task"]["routing_preferences"]["max_output_tokens"],
                "output_sha256": hashlib.sha256(raw["output"]["text"].encode()).hexdigest(),
                "artifact": str(raw_path), "artifact_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
                "review_source": row["review"].get("source", "automatic"),
                "scope": "visible_development_regression",
            })
    result = {"schema_version": 1, "records": records,
              "note": "Different tasks and budgets remain individual observations; Agent multi-model outcomes excluded."}
    return {**result, "version": digest(result)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("app/quality_feedback.json"))
    args = parser.parse_args()
    data = compile_feedback(args.input)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"records": len(data["records"]), "version": data["version"]}))


if __name__ == "__main__":
    main()
