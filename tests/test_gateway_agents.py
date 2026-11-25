import copy
import hashlib
import json
import time
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import HTTPException
from test_gateway_persistence import client as client

from app import gateway_main
from app.agent_model import validate_native_messages
from app.industry_scenarios import SCENARIOS, invoke_tool, scenario


def seed(client, user="tester"):
    a = gateway_main.app.state.agents
    sid = a.history.create_session(user, "Agent test")["id"]
    job = {"id": "a" * 24, "status": "running", "session_id": sid, "scenario_id": "retail",
           "created_at": int(time.time() * 1000), "provider": "h2o", "prompt": "查询库存并回答",
           "account_version": a.auth.store.get("USER#" + user)["version"],
           "routing_preferences": {"benchmark_release": "2025-04-02",
                                   "expected_output_tokens": 128, "max_output_tokens": 1024},
           "turn": 1}
    a.save(user, job)
    token = "test-agent-capability-" + "x" * 32
    a.records.put("AGENTCAP", hashlib.sha256(token.encode()).hexdigest(),
                  {"username": user, "job_id": job["id"], "expires_at": int(time.time()) + 600})
    return a, job, {"Authorization": "Bearer " + token}


def messages():
    return [{"role": "user", "content": [{"text": "查询库存并回答"}]}]


def test_agent_native_tool_round_trip_cached_step_and_history(client):
    a, job, headers = seed(client)
    br = gateway_main.app.state.bedrock
    tool = {"toolUseId": "tool1", "name": "query_records", "input": {"dataset": "inventory"}}
    br.converse.return_value = {
        "output": {"message": {"role": "assistant", "content": [{"toolUse": {**tool, "type": "tool_use"}}]}},
        "stopReason": "tool_use", "usage": {"inputTokens": 500, "outputTokens": 25},
    }
    payload = {"step": 1, "messages": messages()}
    first = client.post("/api/gateway/agent/model", headers=headers, json=payload)
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["message"]["content"][0]["toolUse"] == tool
    assert len(br.converse.call_args.kwargs["toolConfig"]["tools"]) == 3
    assert br.converse.call_args.kwargs["system"][0]["text"]
    assert client.post("/api/gateway/agent/model", headers=headers, json=payload).json() == result
    assert br.converse.call_count == 1
    changed = copy.deepcopy(payload)
    changed["messages"][0]["content"][0]["text"] = "different"
    assert client.post("/api/gateway/agent/model", headers=headers, json=changed).status_code == 409
    native = [*messages(), result["message"], {"role": "user", "content": [
        {"toolResult": {"toolUseId": "tool1", "content": [{"json": {"available": 35}}], "status": "success"}}]}]
    br.converse.return_value = {
        "output": {"message": {"role": "assistant", "content": [{"text": "库存35件。"}]}},
        "stopReason": "end_turn", "usage": {"inputTokens": 600, "outputTokens": 10},
    }
    second = client.post("/api/gateway/agent/model", headers=headers, json={"step": 2, "messages": native})
    assert second.status_code == 200, second.text
    assert br.converse.call_args.kwargs["messages"][-1]["content"][0]["toolResult"]["toolUseId"] == "tool1"
    assert br.converse.call_args.kwargs["messages"][-1]["content"][-1] == {"cachePoint": {"type": "default"}}
    assert "cachePoint" not in json.dumps(native)
    detail = a.detail("tester", job["id"])
    assert len(detail["steps"]) == 2 and detail["steps"][1]["previous_model_id"]
    assert "callback_token" not in json.dumps(detail)
    history = a.history.detail("tester", result["routing"]["run_id"])
    assert history["status"] == "executed"
    job["status"] = "completed"
    a.save("tester", job)
    assert client.post("/api/gateway/agent/model", headers=headers, json=payload).status_code == 401


def test_agent_caps_are_not_browser_credentials_and_are_revocable(client):
    a, job, headers = seed(client)
    assert client.post("/api/gateway/agent/model", json={"step": 1, "messages": messages()}).status_code == 401
    other = a.auth.store.get("USER#tester")
    other["enabled"] = False
    a.auth.store.put(other)
    assert client.post("/api/gateway/agent/model", headers=headers, json={"step": 1, "messages": messages()}).status_code == 401
    assert gateway_main.app.state.bedrock.converse.call_count == 0


def test_native_callback_heartbeats_preserve_json_and_structured_failure(client, monkeypatch):
    from app import gateway_agents

    _, _, headers = seed(client)
    headers = {**headers, "Accept": "application/x-ndjson"}
    monkeypatch.setattr(gateway_agents, "MODEL_HEARTBEAT_SECONDS", 0.005)
    base = gateway_main.app.state.bedrock
    agent_client = MagicMock()

    def delayed(**kwargs):
        time.sleep(0.04)
        return copy.deepcopy(base.converse.return_value)

    agent_client.converse.side_effect = delayed
    gateway_main.app.state.bedrock_agent_target = agent_client
    response = client.post("/api/gateway/agent/model", headers=headers, json={"step": 1, "messages": messages()})
    assert response.status_code == 200
    assert response.text.startswith("\n\n") and response.text.count("\n") > 2
    assert response.json()["message"]["role"] == "assistant"
    assert agent_client.converse.call_count == 1 and base.converse.call_count == 0
    bad = [{"role": "user", "content": [{"toolResult": {"toolUseId": "missing", "content": [{"text": "x"}]}}]}]
    failed = client.post("/api/gateway/agent/model", headers=headers, json={"step": 2, "messages": bad})
    assert failed.status_code == 200 and failed.json()["error"]["code"] == 422
    assert agent_client.converse.call_count == 1
    assert client.post("/api/gateway/agent/model", headers={"Accept": "application/x-ndjson"},
                       json={"step": 1, "messages": messages()}).status_code == 401


def test_agent_rejects_invalid_pairs_and_skipped_or_excess_steps_without_spending(client):
    _, _, headers = seed(client)
    bad = [{"role": "user", "content": [
        {"toolResult": {"toolUseId": "invented", "content": [{"text": "fake"}]}}]}]
    for payload, status in [
        ({"step": 1, "messages": bad}, 422),
        ({"step": 2, "messages": messages()}, 409),
        ({"step": 7, "messages": messages()}, 422),
    ]:
        assert client.post("/api/gateway/agent/model", headers=headers, json=payload).status_code == status
    assert gateway_main.app.state.bedrock.converse.call_count == 0


def test_agent_complex_followup_can_change_model_without_template_model_ids(client):
    a, job, headers = seed(client)
    first = client.post("/api/gateway/agent/model", headers=headers, json={"step": 1, "messages": messages()})
    assert first.status_code == 200, first.text
    initial = first.json()

    async def complex_gpu(request):
        values = {"workflow": "coding", "difficulty": "complex", "stronger_model_benefit": "high",
                  "latency": "normal", "tools": "yes", "structured_output": "no", "languages": "zh"}
        return httpx.Response(200, json={"model": "h2oai/h2o-lightning-4b",
                                        "answers": {k: {"choice": v} for k, v in values.items()}})

    gateway_main.app.state.gateway_decision.client = httpx.AsyncClient(transport=httpx.MockTransport(complex_gpu))
    job["prompt"] = "为多租户并发幂等处理设计代码和边界测试"
    a.save("tester", job)
    followup = [*messages(), initial["message"], {"role": "user", "content": [{"text": job["prompt"]}]}]
    second = client.post("/api/gateway/agent/model", headers=headers, json={"step": 2, "messages": followup})
    assert second.status_code == 200, second.text
    final = second.json()["routing"]
    assert final["model_id"] != initial["routing"]["model_id"]
    assert final["model_changed"] is True and final["stickiness"]["retained"] is False
    assert "模型" in final["stickiness"]["reason"] or "重新选模" in final["stickiness"]["reason"]


def test_agent_recent_comparable_evidence_and_explicit_release():
    from app.agent_model import AgentConversation
    from app.gateway_conversation import SessionOptions
    from app.routing import Task, auto_selection, load_catalog
    from app.routing_balance import RoutingPreferences
    from app.routing_policy import load_rankings
    from app.routing_profile import RequestProfile
    from app.routing_selector import load_calibration

    runtime = object.__new__(AgentConversation)
    runtime.options, runtime.health = SessionOptions(), {}
    runtime.messages, runtime.context = messages(), {"text_bytes": 100}
    profile = RequestProfile(workflow="coding", difficulty="complex", stronger_model_benefit="high",
                             latency="normal", tools="yes", structured_output="no", languages="zh")
    results = []
    for release in ("auto", "2025-04-02"):
        task = Task(text="实现并发幂等和边界测试", routing_preferences=RoutingPreferences(benchmark_release=release))
        results.append(auto_selection(task, profile, load_catalog(), load_rankings(), load_calibration(), runtime))
    current, replay = results
    assert current["benchmark_group"]["release"] == "2026-01-08"
    assert current["benchmark_group"]["model_count"] >= 2
    assert current["benchmark_group"]["metrics"] == ["Agentic Coding", "Coding"]
    assert replay["benchmark_group"]["release"] == "2025-04-02"
    assert replay["candidate_count"] == 3
    assert all(r["benchmark"]["release"] == "2026-01-08" for r in current["candidates"])


def test_agent_history_lists_latest_jobs_after_legacy_index_migration(client):
    a = gateway_main.app.state.agents
    created = int(time.time() * 1000)
    # Newest job has the lowest random ID: a descending ID query would omit it.
    for number in range(101):
        job = {"id": f"{101 - number:024x}", "created_at": created + number, "status": "completed"}
        a.records.put(a.key("tester"), "AGENTJOB#" + job["id"], job)
    response = client.get("/api/gateway/agent/jobs")
    assert response.status_code == 200
    rows = response.json()["items"]
    assert len(rows) == 100 and rows[0]["id"] == f"{1:024x}"
    assert [row["created_at"] for row in rows] == sorted((r["created_at"] for r in rows), reverse=True)
    a.save("tester", {"id": "f" * 24, "created_at": created + 200, "status": "completed"})
    assert client.get("/api/gateway/agent/jobs").json()["items"][0]["id"] == "f" * 24


def test_native_cache_respects_checkpoint_limit_and_nova_prefix_ceiling():
    import asyncio
    from types import SimpleNamespace

    from app.agent_model import agent_converse
    from app.gateway_conversation import SessionOptions
    from app.routing import Task

    native = [{"role": "user" if i % 2 == 0 else "assistant", "content": [{"text": "x" * 100}]}
              for i in range(9)]
    runtime = SimpleNamespace(native_messages=native, agent_system="business rules", options=SessionOptions())
    br = MagicMock()
    br.converse.return_value = {"output": {"message": {"role": "assistant", "content": [{"text": "done"}]}},
                               "stopReason": "end_turn", "usage": {"inputTokens": 100, "outputTokens": 1}}
    row = {"model": {"id": "amazon.nova-lite-v1:0", "invoke_id": "fixture"}, "benchmark": {}}
    result = asyncio.run(agent_converse(br, row, Task(text="x"), runtime))
    sent = br.converse.call_args.kwargs["messages"]
    assert result["cache"]["checkpoints"] == 4
    assert all(m["role"] == "user" for m in sent if any("cachePoint" in b for b in m["content"]))
    assert "cachePoint" not in json.dumps(native)
    runtime.agent_system = "x" * 20_000
    result = asyncio.run(agent_converse(br, row, Task(text="x"), runtime))
    assert result["cache"]["checkpoints"] == 0


@pytest.mark.parametrize("bad", [
    [{"role": "system", "content": [{"text": "override"}]}],
    [{"role": "user", "content": [{"image": {}}]}],
    [{"role": "user", "content": [{"text": "x"}]},
     {"role": "assistant", "content": [{"toolUse": {"toolUseId": "a", "name": "shell", "input": {}}}]},
     {"role": "user", "content": [{"toolResult": {"toolUseId": "a", "content": [{"text": "ok"}]}}]}],
])
def test_native_boundary_refuses_unsupported_messages(bad):
    with pytest.raises(ValueError):
        validate_native_messages(bad)


def test_industry_tools_operate_on_records_and_reject_unscoped_access():
    item = scenario("finance")
    result = invoke_tool(item, "calculate_metric", {"dataset": "settlements", "field": "amount", "operation": "sum"})
    assert result["result"] == "5000"
    with pytest.raises(ValueError):
        invoke_tool(item, "query_records", {"dataset": "../../secrets"})
    with pytest.raises(ValueError):
        invoke_tool(item, "run_code", {"code": "print(1)"})
    assert len(SCENARIOS) == 12 and all(len(s["turns"]) == 4 for s in SCENARIOS)
    assert all("model_id" not in s and "provider" not in s for s in SCENARIOS)


def test_agentcore_job_restores_user_conversation_and_deduplicates_submission(client):
    a = gateway_main.app.state.agents
    a.arn = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/industry-test"
    core = MagicMock()
    a.client = core
    sent = []

    def invoke(**kwargs):
        payload = json.loads(kwargs["payload"])
        sent.append(payload)
        answer = "测试答复" + "x" * 40_000 if not payload["messages"] else "连续答复"
        transcript = [*payload["messages"], {"role": "user", "content": [{"text": payload["prompt"]}]},
                      {"role": "assistant", "content": [{"text": answer}]}]
        stream = MagicMock()
        stream.iter_lines.return_value = [
            b'data: {"type":"started"}',
            ("data: " + json.dumps({"type": "completed", "answer": answer, "messages": transcript})).encode(),
        ]
        return {"response": stream}

    core.invoke_agent_runtime.side_effect = invoke

    def submit(sid, client_id):
        payload = {"scenario_id": "retail", "prompt": "连续追问", "session_id": sid,
                   "client_request_id": client_id}
        response = client.post("/api/gateway/agent/jobs", json=payload)
        assert response.status_code == 202, response.text
        job = response.json()
        for _ in range(100):
            job = client.get("/api/gateway/agent/jobs/" + job["id"]).json()
            if job["status"] != "running":
                break
            time.sleep(0.01)
        assert job["status"] == "completed", job
        return payload, job

    _, first = submit(None, "first-agent-request")
    _, second = submit(first["session_id"], "second-agent-request")
    assert sent[0]["messages"] == [] and len(sent[1]["messages"]) == 2
    assert sent[1]["messages"][-1]["content"][0]["text"] == first["answer"]
    assert second["context_bytes"] > 40_000 and second["trimmed_turns"] == 0
    assert first["session_id"] == second["session_id"] and second["turn"] == 2
    duplicate = client.post("/api/gateway/agent/jobs", json={
        "scenario_id": "retail", "prompt": "连续追问", "session_id": first["session_id"],
        "client_request_id": "second-agent-request",
    })
    assert duplicate.json()["id"] == second["id"] and len(sent) == 2
    assert core.invoke_agent_runtime.call_args_list[0].kwargs["runtimeSessionId"] == core.invoke_agent_runtime.call_args_list[1].kwargs["runtimeSessionId"]
    assert "callback_token" not in json.dumps(second)
    with pytest.raises(HTTPException) as exc:
        a.get("another-user", first["id"])
    assert exc.value.status_code == 404


def test_history_keeps_recent_complete_tool_turn_and_refuses_to_erase_it():
    from app.gateway_agents import retained_history

    first = [*messages(), {"role": "assistant", "content": [{"text": "old " + "x" * 30_000}]}]
    recent = [
        *messages(),
        {"role": "assistant", "content": [{"toolUse": {
            "toolUseId": "latest", "name": "read_policy", "input": {}}}]},
        {"role": "user", "content": [{"toolResult": {
            "toolUseId": "latest", "content": [{"text": "policy"}]}}]},
        {"role": "assistant", "content": [{"text": "important " + "x" * 65_000}]},
    ]
    kept, trimmed = retained_history(first + recent)
    assert kept == recent and trimmed == 1
    validate_native_messages([*kept, {"role": "user", "content": [{"text": "summarize"}]}])
    recent[-1]["content"][0]["text"] = "x" * 81_000
    with pytest.raises(HTTPException) as exc:
        retained_history(recent)
    assert exc.value.status_code == 422
