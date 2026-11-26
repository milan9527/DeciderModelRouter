"""Call the exact three experimental winners once; keep quality review separate."""

import argparse
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import boto3
from botocore.config import Config

from app.gateway_adapter import request_args
from app.routing import Task
from app.routing_balance import observed_reference_cost
from scripts.benchmark_profile_policy import read_json, write_json


def main():
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inputs, results = read_json(args.inputs), read_json(args.results)
    client = boto3.client("bedrock-runtime", region_name="us-east-1",
                          config=Config(connect_timeout=10, read_timeout=120,
                                        retries={"total_max_attempts": 1}))
    report = {"created_at": datetime.now(UTC).isoformat(), "target_calls_only": True,
              "quality_review": "separate; execution success is not answer correctness", "rows": []}
    for case in inputs["cases"]:
        cid = results["audits"][case["case"]]["selected_candidate_id"]
        row = next(r for r in case["candidates"] if r["candidate_id"] == cid)
        state = json.loads(case["profile_payload"]["state"])
        task = Task(text=state["request"])
        runtime = SimpleNamespace(
            messages=state.get("recent_conversation", []) + [{"role": "user", "text": task.text}],
            options=SimpleNamespace(prompt_cache=False),
        )
        request, _ = request_args(row, task, runtime, {})
        record = {"case": case["case"], "candidate_id": cid, "model_id": row["model"]["id"],
                  "request": request, "quality_status": "pending"}
        started = time.perf_counter()
        try:
            response = client.converse(**request)
            record.update(
                stop_reason=response.get("stopReason"), usage=response.get("usage"),
                text="\n".join(c["text"] for c in response["output"]["message"]["content"] if "text" in c),
                cost_reference_usd=observed_reference_cost(
                    row["benchmark"], response.get("usage"), case["expected_profile"]["workflow"]),
                price_scope=row["price_scope"],
            )
            record["complete"] = bool(record["text"]) and record["stop_reason"] == "end_turn"
        except Exception as exc:
            record.update(complete=False, error_type=type(exc).__name__, error=str(exc))
        record["target_ms"] = (time.perf_counter() - started) * 1000
        report["rows"].append(record)
        write_json(args.output, report)
        print(json.dumps({k: record.get(k) for k in
                          ("case", "model_id", "complete", "stop_reason", "target_ms", "error")}), flush=True)


if __name__ == "__main__":
    main()
