"""Fresh two-stage deployment checks; local API or the authenticated public service."""

import argparse
import json
import os
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import boto3
import httpx

from app.gateway_store import clean


def check(client, output, *, local=False):
    catalog = client.get("/api/gateway/catalog").json()
    assert catalog["decision_service"]["routing_mode"] == "two_stage"
    report = {"started_at": datetime.now(UTC).isoformat(), "runs": [],
              "scope": "fresh deployment smoke; not the 159-case quality benchmark"}

    def run(name, text, expected=None, *, sid=None, execute=True, task_options=None, provider="liquid"):
        body = {**{k: catalog[k] for k in ("catalog_version", "ranking_version", "policy_version")},
                "task": {"text": text, "routing_preferences": {
                    "cost_tier": "medium", "expected_output_tokens": 512, "max_output_tokens": 16384,
                }, **(task_options or {})},
                "provider": provider, "execute_target": execute, "session_id": sid, "client_request_id": str(uuid4()),
                "session": {"stream": True, "fallback": False}}
        started = time.monotonic()
        response = client.post("/api/gateway/runs", json=body)
        response.raise_for_status()
        events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
        rid = events[0]["request_id"]
        record = client.get("/api/gateway/history/" + rid)
        record.raise_for_status()
        output.joinpath(name + ".json").write_text(json.dumps(clean(record.json()), ensure_ascii=False))
        flow = next((e["data"] for e in events if e.get("stage") == "classification"
                     and e.get("status") == "completed"), {})
        selected = next((e["data"] for e in events if e.get("stage") == "selection"
                         and e.get("status") == "completed"), {})
        selection = selected.get("selection", {})
        calls = selection.get("decision_calls", [])
        result = next((e["data"] for e in events if e.get("stage") == "execution"
                       and e.get("status") == "completed"), {})
        item = {"case": name, "run_id": rid, "session_id": record.json()["session_id"],
                "provider": provider,
                "status": record.json()["status"], "profile": flow.get("profile"),
                "model_id": (selected.get("selected_model") or {}).get("id"),
                "mode": selection.get("decision_mode"), "calls": len(calls),
                "forward_passes": [c["timings"].get("forward_passes") for c in calls],
                "assessment_ms": selection.get("assessment_ms"), "selection_ms": selection.get("selection_ms"),
                "decision_ms": selection.get("decision_ms"), "route_ms": record.json().get("route_ms"),
                "target_ms": result.get("backend_total_ms"),
                "wall_ms": (time.monotonic() - started) * 1000,
                "answer": result.get("text"), "complete": result.get("complete"),
                "all_candidates_supplied": bool(calls) and
                    {r["candidate_id"] for r in selection.get("candidates", [])}
                    == set(calls[-1].get("candidate_ids", [])),
                "errors": [e["data"] for e in events if e["type"] == "error"]}
        if expected is not None:
            try:
                actual = json.loads(result.get("text", ""))
                item["quality_pass"] = type(actual) is type(expected) and actual == expected
            except (ValueError, TypeError):
                item["quality_pass"] = False
        report["runs"].append(item)
        output.joinpath("summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(name, item["status"], item["model_id"], "decision_ms", item["decision_ms"],
              "quality", item.get("quality_pass"), flush=True)
        assert item["status"] == ("executed" if execute else "selected"), item["errors"]
        assert item["mode"] == "two_stage" and item["calls"] == 2 and item["all_candidates_supplied"]
        if provider == "liquid":
            assert item["forward_passes"] == [1, 1]
        assert flow["assessment_count"] == 7 and calls[1]["assessment_count"] == 1
        assert calls[1]["task_profile"] == flow["profile"]
        if all(type(value) is int for value in item["forward_passes"]):
            assert record.json()["decision_forward_passes"] == sum(item["forward_passes"])
        # Quality remains separately reported; a wrong answer is not an infrastructure success.
        return item

    run("json-format", '从“客户姓名林雨，订单号Q73，数量4”提取信息。只返回合法JSON，键为name、order、quantity，quantity为整数。不要Markdown。',
        {"name": "林雨", "order": "Q73", "quantity": 4})
    run("invoice-dedup", '按发票号去重后计算总额：发票A金额120元，发票A金额120元，发票B金额85元。只返回JSON：{"total":整数}，不要解释。',
        {"total": 205})
    first = run("memory-store", '记住：项目代号 MAPLE-482，预算87万元。只回复“已记录”。')
    second = run("memory-correct", '预算改为93万元，项目代号不变。只回复“已更正”。', sid=first["session_id"])
    run("memory-recall", '根据前文，只返回JSON，project为项目代号，budget为最新预算数值，单位万元。不要解释。',
        {"project": "MAPLE-482", "budget": 93}, sid=second["session_id"])
    run("coding", "用Python实现闭区间合并函数。只合并相交或共享端点的区间，不能把[1,2]与[3,4]合并。给出代码及这两个边界测试。")
    run("legacy-denied", "解释重试需要注意什么。", execute=False,
        task_options={"routing_preferences": {"allowed_models": ["amazon.nova-micro-v1:0", "deepseek.r1-v1:0"]}})
    if not local:
        # These checks invoke the actual configured comparison services, without target calls.
        for provider in ("decider", "h2o"):
            run(provider + "-route", "实现Python闭区间合并并检查端点边界。", execute=False, provider=provider)
    content = Path("app/evaluation_assets/red.png").read_bytes()
    uploaded = client.post("/api/gateway/media", content=content, headers={"Content-Type": "image/png"})
    uploaded.raise_for_status()
    run("vision", 'What is the main color? Return only JSON {"color":"English color name"}.',
        {"color": "red"}, task_options={
            "attachments": [{"id": uploaded.json()["id"], "kind": "image"}], "output_modality": "text"})
    report["performance"] = client.get("/api/gateway/performance",
                                        params={"session_id": first["session_id"]}).json()
    assert report["performance"]["two_stage_decision_samples"] == 3
    assert report["performance"]["joint_decision_samples"] == 0
    report["finished_at"] = datetime.now(UTC).isoformat()
    output.joinpath("summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    client.post("/api/auth/logout", json={}).raise_for_status()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--url", default="https://d3eipg2g3l3an3.cloudfront.net")
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--gpu-url", default="http://127.0.0.1:23124")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    sm = boto3.client("secretsmanager", region_name="us-east-1")
    headers = {"X-Decider-Client": "web"}
    if args.local:
        from fastapi.testclient import TestClient

        from app.auth import SQLiteStore, save_user

        os.environ.update(AWS_REGION="us-east-1", BEDROCK_ENABLED="true",
            AUTH_COOKIE_SECURE="false", AUTH_DB_PATH=str(args.output / "private-auth.sqlite"),
            GATEWAY_DB_PATH=str(args.output / "private-history.sqlite"),
            GATEWAY_MEDIA_PATH=str(args.output / "private-media"),
            OPEN_MODELS_URL=args.gpu_url, GATEWAY_DECISION_MODE="two_stage",
            OPEN_MODELS_API_KEY=sm.get_secret_value(SecretId="DeciderModelRouter/gpu-service-key")["SecretString"])
        for key in ("AUTH_TABLE", "AUTH_ORIGIN", "GATEWAY_DATA_TABLE", "GATEWAY_MEDIA_BUCKET"):
            os.environ.pop(key, None)
        credentials = {"username": "release-test", "password": secrets.token_urlsafe(32)}
        save_user(SQLiteStore(os.environ["AUTH_DB_PATH"]), **credentials)
        from app.gateway_main import app
        with TestClient(app, headers=headers) as client:
            client.post("/api/auth/login", json=credentials).raise_for_status()
            check(client, args.output, local=True)
    else:
        credentials = json.loads(sm.get_secret_value(SecretId="DeciderModelRouter/admin-credentials")["SecretString"])
        with httpx.Client(base_url=args.url, headers=headers, timeout=240, trust_env=False) as client:
            client.post("/api/auth/login", json=credentials).raise_for_status()
            check(client, args.output)


if __name__ == "__main__":
    main()
