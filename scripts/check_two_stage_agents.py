"""Post-release AgentCore V2 multi-turn smoke; no synthetic execution or forced target."""

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import boto3
import httpx

from app.gateway_store import clean
from app.industry_scenarios import SCENARIOS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--scenarios", nargs="+", default=["retail", "finance", "energy", "legal"])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    credentials = json.loads(boto3.client("secretsmanager", region_name="us-east-1").get_secret_value(
        SecretId="DeciderModelRouter/admin-credentials")["SecretString"])
    report = {"started_at": datetime.now(UTC).isoformat(), "turns": [],
              "scope": "live AgentCore V2 calls through deployed two-stage router; content reviewed separately"}
    with httpx.Client(base_url="https://d3eipg2g3l3an3.cloudfront.net",
                      headers={"X-Decider-Client": "web"}, timeout=60, trust_env=False) as client:
        client.post("/api/auth/login", json=credentials).raise_for_status()
        for scenario_id in args.scenarios:
            scenario = next(s for s in SCENARIOS if s["id"] == scenario_id)
            sid = None
            for number, prompt in enumerate(scenario["turns"], 1):
                response = client.post("/api/gateway/agent/jobs", json={
                    "scenario_id": scenario_id, "prompt": prompt, "session_id": sid,
                    "client_request_id": str(uuid4()), "routing_preferences": {
                        "cost_tier": "medium", "expected_output_tokens": 1024, "max_output_tokens": 16384}})
                response.raise_for_status()
                job = response.json()
                assert job["platform_version"] == "V2" and job["model"] == "model-router"
                path = args.output / f"{scenario_id}-{number}.json"
                path.write_text(json.dumps(clean(job), ensure_ascii=False))
                deadline = time.monotonic() + 680
                while job["status"] == "running" and time.monotonic() < deadline:
                    time.sleep(2)
                    response = client.get("/api/gateway/agent/jobs/" + job["id"])
                    response.raise_for_status()
                    job = response.json()
                    path.write_text(json.dumps(clean(job), ensure_ascii=False))
                sid = job["session_id"]
                item = {"scenario": scenario_id, "turn": number, "job_id": job["id"],
                        "session_id": sid, "status": job["status"],
                        "platform_version": job.get("platform_version"), "metrics": job.get("metrics"),
                        "models": [s.get("model_id") for s in job["steps"]],
                        "decision_ms": [s.get("model_decision_ms") for s in job["steps"]],
                        "two_stage": bool(job["steps"]) and all(
                            s.get("decision_mode") == "two_stage" and s.get("model_decision_calls") == 2
                            for s in job["steps"]),
                        "error": job.get("error")}
                report["turns"].append(item)
                args.output.joinpath("summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
                print(scenario_id, number, item["status"], item["models"], flush=True)
                # A failed business turn stays failed; later user turns may still run.
                if job["status"] == "running":
                    raise TimeoutError("Agent status unknown after wait; inspect saved job ID, do not resubmit")
                assert all(s.get("decision_mode") == "two_stage" for s in job["steps"])
        report["finished_at"] = datetime.now(UTC).isoformat()
        args.output.joinpath("summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        client.post("/api/auth/logout", json={}).raise_for_status()


if __name__ == "__main__":
    main()
