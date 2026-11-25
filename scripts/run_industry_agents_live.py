"""Run industry conversations through the authenticated deployed AgentCore integration."""

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import boto3
import httpx

from app.gateway_store import clean


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://d3eipg2g3l3an3.cloudfront.net")
    parser.add_argument("--scenarios", default="all")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-output-tokens", type=int, default=16384)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    credentials = json.loads(boto3.client("secretsmanager", region_name="us-east-1").get_secret_value(
        SecretId="DeciderModelRouter/admin-credentials")["SecretString"])
    report = {"started_at": datetime.now(UTC).isoformat(), "url": args.url,
              "fixture": False, "data_source": "synthetic_business_records",
              "scope": "industry_agent_four_turns", "quality_scoring": "manual_review_required",
              "results": []}
    with httpx.Client(base_url=args.url, headers={"X-Decider-Client": "web"}, timeout=60, trust_env=False) as client:
        client.post("/api/auth/login", json=credentials).raise_for_status()
        response = client.get("/api/gateway/agent/scenarios")
        response.raise_for_status()
        catalog = response.json()
        assert catalog["configured"] and catalog["platform_version"] == "V2"
        report["dataset_version"] = catalog["version"]
        selected = set(args.scenarios.split(","))
        cases = [s for s in catalog["items"] if args.scenarios == "all" or s["id"] in selected]
        assert cases
        report["planned_turns"] = len(cases) * 4
        report["scenarios"] = [s["id"] for s in cases]
        for item in cases:
            session_id = None
            for turn, prompt in enumerate(item["turns"], 1):
                response = client.post("/api/gateway/agent/jobs", json={
                    "scenario_id": item["id"], "prompt": prompt, "session_id": session_id,
                    "client_request_id": str(uuid4()), "routing_preferences": {
                        "cost_tier": "medium", "expected_output_tokens": 1024,
                        "max_output_tokens": args.max_output_tokens,
                    },
                })
                response.raise_for_status()
                job = response.json()
                session_id = job["session_id"]
                deadline = time.monotonic() + 670
                observed = 0
                while job["status"] == "running" and time.monotonic() < deadline:
                    time.sleep(2)
                    response = client.get("/api/gateway/agent/jobs/" + job["id"])
                    response.raise_for_status()
                    job = response.json()
                    if len(job["steps"]) != observed:
                        observed = len(job["steps"])
                        print(item["id"], f"turn {turn}", f"model calls {observed}", flush=True)
                path = args.output / f"{item['id']}-turn-{turn}.json"
                path.write_text(json.dumps(clean(job), ensure_ascii=False, indent=2) + "\n")
                report["results"].append({
                    "scenario_id": item["id"], "turn": turn, "job_id": job["id"],
                    "status": job["status"], "session_id": session_id,
                    "models": [s.get("model_id") for s in job["steps"]],
                    **job["metrics"], "artifact": path.name,
                })
                report.update(completed_turns=sum(r["status"] == "completed" for r in report["results"]),
                              finished_at=datetime.now(UTC).isoformat())
                (args.output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
                print(item["id"], f"turn {turn}", job["status"], job["metrics"], flush=True)
                if job["status"] != "completed":
                    break
        client.post("/api/auth/logout", json={}).raise_for_status()


if __name__ == "__main__":
    main()
