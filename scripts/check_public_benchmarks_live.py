"""Authenticated release checks with real routing and AgentCore; no fixed target model."""

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import boto3
import httpx

from app.gateway_templates import load_templates
from app.public_benchmarks import load_public_benchmarks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true", help="Reuse saved calls; never repeat a recorded invocation")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=args.resume)
    version = load_public_benchmarks()["version"]
    credentials = json.loads(boto3.client("secretsmanager", region_name="us-east-1").get_secret_value(
        SecretId="DeciderModelRouter/admin-credentials")["SecretString"])
    report = {"started_at": datetime.now(UTC).isoformat(), "public_benchmark_version": version,
              "scope": "release integration checks, not an overall quality evaluation", "runs": []}

    def save(name, data):
        (args.output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    with httpx.Client(base_url="https://d3eipg2g3l3an3.cloudfront.net",
                      headers={"X-Decider-Client": "web"}, timeout=240, trust_env=False) as client:
        client.post("/api/auth/login", json=credentials).raise_for_status()
        try:
            response = client.get("/api/gateway/catalog")
            response.raise_for_status()
            catalog = response.json()
            assert catalog["model_routing"]["public_benchmark_version"] == version
            assert catalog["decision_service"]["routing_mode"] == "two_stage"
            report["inventory"] = catalog["evidence_inventory"]["public_benchmarks"]
            manufacturing = next(t for t in load_templates()["items"] if t["id"] == "industry-manufacturing")
            cases = [
                ("manufacturing", manufacturing["task"], "high", 4096),
                ("manufacturing-large", manufacturing["task"], "high", 16384),
                ("general", "用两句话解释什么是幂等，并给一个订单接口例子。", "low", 4096),
            ]
            for name, text, preference, cap in cases:
                body = {**{k: catalog[k] for k in ("catalog_version", "ranking_version", "policy_version")},
                        "task": {"text": text, "routing_preferences": {
                            "cost_tier": preference, "expected_output_tokens": 1024,
                            "max_output_tokens": cap}},
                        "execute_target": True, "client_request_id": str(uuid4()),
                        "session": {"use_history": False, "stickiness": False, "prompt_cache": False,
                                    "stream": False, "fallback": False}}
                saved = args.output / (name + ".json")
                if args.resume and saved.exists():
                    record = json.loads(saved.read_text())
                    events, rid = record["events"], record["id"]
                else:
                    response = client.post("/api/gateway/runs", json=body)
                    response.raise_for_status()
                    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
                    rid = events[0]["request_id"]
                    response = client.get("/api/gateway/history/" + rid)
                    response.raise_for_status()
                    record = response.json()
                    save(name + ".json", record)
                selected = next(e["data"] for e in events
                                if e.get("stage") == "selection" and e.get("status") == "completed")
                pool = selected["selection"]
                calls = pool["decision_calls"]
                assert len(calls) == 2 and pool["decision_mode"] == "two_stage"
                assert pool["public_benchmark_version"] == record["public_benchmark_version"] == version
                assert calls[-1]["public_benchmark_version"] == version
                supplied = calls[-1]["public_evidence_records"]
                assert set(supplied) == {r["candidate_id"] for r in pool["candidates"]}
                for row in pool["candidates"]:
                    assert supplied[row["candidate_id"]] == [r["id"] for r in row["public_benchmarks"]]
                assert any(supplied.values())
                item = {"case": name, "run_id": rid, "status": record["status"],
                        "model_id": (selected.get("selected_model") or {}).get("id"),
                        "profile": record.get("profile"), "decision_mode": pool["decision_mode"],
                        "decision_calls": len(calls), "candidate_count": len(supplied),
                        "candidates_with_public_evidence": sum(bool(v) for v in supplied.values()),
                        "public_evidence_ids_match_saved_candidates": True,
                        "assessment_ms": pool["assessment_ms"], "selection_ms": pool["selection_ms"],
                        "route_ms": record.get("route_ms"), "max_output_tokens": cap,
                        "content_quality": "not scored"}
                report["runs"].append(item)
                save("summary.json", report)
                print(json.dumps(item, ensure_ascii=False), flush=True)
                assert record["status"] in {"executed", "incomplete"}, record.get("error")

            saved = args.output / "agent-finance.json"
            if args.resume and saved.exists():
                job = json.loads(saved.read_text())
            else:
                response = client.post("/api/gateway/agent/jobs", json={
                    "scenario_id": "finance", "prompt": "查询结算记录，用计算工具计算去重前的结算净额。"
                    '金额单位为分，最后只输出JSON：{"raw_net_cents":整数}。',
                    "client_request_id": str(uuid4()), "routing_preferences": {
                        "cost_tier": "high", "expected_output_tokens": 1024, "max_output_tokens": 16384}})
                response.raise_for_status()
                job = response.json()
            deadline = time.monotonic() + 660
            while True:
                save("agent-finance.json", job)
                if job["status"] != "running" or time.monotonic() >= deadline:
                    break
                time.sleep(2)
                response = client.get("/api/gateway/agent/jobs/" + job["id"])
                response.raise_for_status()
                job = response.json()
            assert job["platform_version"] == "V2" and job["model"] == "model-router"
            assert job["status"] == "completed", job.get("error")
            assert job["steps"]
            for step in job["steps"]:
                assert step["public_benchmark_version"] == version
                assert step["decision_mode"] == "two_stage" and step["model_decision_calls"] == 2
                assert step["selected_at"] <= step["target_started_at"] <= step["target_finished_at"]
                response = client.get("/api/gateway/history/" + step["run_id"])
                response.raise_for_status()
                history = response.json()
                save(f"agent-step-{step['step']}.json", history)
                pool = next(e["data"]["selection"] for e in history["events"]
                            if e.get("stage") == "selection" and e.get("status") == "completed")
                supplied = pool["decision_calls"][-1]["public_evidence_records"]
                assert set(supplied) == {r["candidate_id"] for r in pool["candidates"]}
                assert any("Long Agentic" in e["scores"]
                           for r in pool["candidates"] for e in r["public_benchmarks"])
                for row in pool["candidates"]:
                    assert supplied[row["candidate_id"]] == [r["id"] for r in row["public_benchmarks"]]
            try:
                passed = json.loads(job["answer"]) == {"raw_net_cents": 5000}
            except (ValueError, TypeError):
                passed = False
            report["agent"] = {"job_id": job["id"], "status": job["status"],
                               "platform_version": "V2", "calls": len(job["steps"]),
                               "models": [s["model_id"] for s in job["steps"]],
                               "public_benchmark_version": version, "selected_before_target": True,
                               "agent_benchmarks_in_every_step": True,
                               "content_pass": passed, "content_method": "exact JSON oracle"}
            report["finished_at"] = datetime.now(UTC).isoformat()
            save("summary.json", report)
            print(json.dumps(report["agent"], ensure_ascii=False), flush=True)
        finally:
            client.post("/api/auth/logout", json={}).raise_for_status()


if __name__ == "__main__":
    main()
