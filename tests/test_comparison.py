import asyncio
import copy
import hashlib
import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import crypto, routing
from app.comparison import CASES, CompareRequest, OutputError, gpt_decide, prepare, selected_answers
from app.comparison_workflows import Workflow
from app.main import app
from deployment.jev_proxy import valid_request
from deployment.open_models import gateway


def test_shared_initial_facts_and_custom_tasks_do_not_leak_reference():
    case, task = prepare(CompareRequest(case_id="payment-pending"))
    left, right = Workflow(case, task), Workflow(case, task)
    payload, expected = left.prepare("intake")
    assert set(payload) == {"state", "questions"} and expected
    assert payload == right.prepare("intake")[0]
    left.apply("intake", expected)
    assert right.state["ticket_status"] == "new"
    with pytest.raises(HTTPException):
        prepare(CompareRequest(case_id="payment-pending", task="请只告诉我当前状态，不要入账。"))
    with pytest.raises(HTTPException):
        prepare(CompareRequest(case_id="routing-support", variant="reversed"))


@pytest.mark.parametrize("case_id", [c["id"] for c in CASES.values() if c["group"] != "routing"])
def test_workflow_can_achieve_goal_and_sends_valid_provider_requests(case_id):
    case = CASES[case_id]
    workflow = Workflow(case, case["task"])
    for stage in case["steps"]:
        payload, expected = workflow.prepare(stage["id"])
        assert valid_request({"model": "jev-latest", **payload})
        gateway.validate(payload)
        workflow.apply(stage["id"], expected)
    assert workflow.finish()["complete"] is (case["group"] != "payment")


def test_finished_payment_steps_do_not_imply_funds_were_processed():
    case = CASES["payment-pending"]
    workflow = Workflow(case, case["task"])
    for stage in case["steps"]:
        payload, expected = workflow.prepare(stage["id"])
        if stage["id"] == "execute":
            expected = {"action": "hold"}
        if stage["id"] == "close":
            state = json.loads(payload["state"])
            assert "funding_requirements_met" not in state
            assert state["business_complete"] is False
            assert "execution" not in state
            assert expected == {"action": "follow_up"}
        workflow.apply(stage["id"], expected)
    assert len(workflow.state["completed"]) == 5
    assert workflow.finish()["complete"] is False
    assert workflow.state["order_status"] == "open"
    assert workflow.state["ticket_status"] == "waiting"


def test_wrong_device_action_and_wrong_preset_operation_remain_visible():
    case = CASES["device-relative"]
    workflow = Workflow(case, case["task"])
    workflow.apply("power", {"power": "unchanged"})
    workflow.apply("temperature", {"temperature": "18"})
    workflow.apply("mode", {"mode": "unchanged"})
    result = workflow.finish()
    assert not result["complete"] and result["state"]["temperature_c"] == 18
    case = CASES["temperature-presets"]
    workflow = Workflow(case, case["task"])
    workflow.apply("operation", {"action": "add"})
    workflow.apply("selection", {f"t{t}": "yes" if 22 <= t <= 25 else "no" for t in range(16, 31)})
    payload, expected = workflow.prepare("confirm")
    assert json.loads(payload["state"])["current_presets"] == [20, 22, 23, 24, 25, 26]
    assert expected == {"action": "needs_correction"}
    workflow.apply("confirm", {"action": "complete"})
    assert not workflow.finish()["complete"]


def test_routing_executes_the_selected_model_and_checks_task_type(monkeypatch):
    case = CASES["routing-retrieval"]
    workflow = Workflow(case, case["task"])
    workflow.apply("workflow", {"action": "embedding"})
    payload, expected = workflow.prepare("model")
    assert expected is None
    assert len(payload["questions"]["action"]["criteria"]) >= 2
    selected = workflow.pool["candidates"][-1]
    workflow.apply("model", {"action": selected["candidate_id"]})
    calls = []

    def execute(client, model, task, kind, benchmark):
        calls.append((model["id"], kind, task.text))
        return {"text": "已生成 1024 维向量", "dimensions": 1024, "complete": True}

    monkeypatch.setattr("app.routing.execute_target", execute)
    workflow.execute_target(MagicMock())
    assert calls == [(selected["model"]["id"], "embedding", case["task"])]
    assert workflow.finish()["complete"]
    workflow.selected_workflow = "general_text"
    assert workflow.finish()["complete"]  # Route completion is separate from task suitability.


def test_gpt_invalid_json_and_missing_question_are_not_repaired():
    payload = {"state": "Turn on.", "questions": {
        "power": {"type": "choice", "criteria": {"on": "On", "off": "Off"}},
        "mode": {"type": "choice", "criteria": {"cool": "Cooling", "heat": "Heating"}},
    }}
    client = MagicMock()
    client.converse.return_value = {
        "stopReason": "end_turn",
        "output": {"message": {"content": [{"text": '{"answers":{"power":{"choice":"on"}}'}]}},
    }
    with pytest.raises(OutputError) as error:
        gpt_decide(client, payload)
    assert error.value.raw.endswith("}}")
    assert client.converse.call_count == 1
    assert client.converse.call_args.kwargs["modelId"] == "us.openai.gpt-6.1-sol"
    assert client.converse.call_args.kwargs["additionalModelRequestFields"] == {"reasoning": {"effort": "low"}}
    with pytest.raises(ValueError):
        selected_answers({"answers": {"power": {"choice": "on"}}}, payload)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("BEDROCK_ENABLED", raising=False)
    with TestClient(app, headers={"X-Decider-Client": "web"}) as c:
        assert c.post("/api/auth/login", json={"username": "tester", "password": "test-password-with-entropy"}).status_code == 200
        yield c


def test_runs_serially_with_isolated_state_failure_and_exact_step_totals(client, monkeypatch):
    calls, inflight, peak = [], 0, 0

    async def infer(request, provider, payload, step):
        nonlocal inflight, peak
        inflight += 1
        peak = max(peak, inflight)
        calls.append((provider, step["id"], copy.deepcopy(payload)))
        await asyncio.sleep(.005)
        inflight -= 1
        step["timings"] = {"processing_ms": 40, "queue_ms": 0, "load_ms": 0}
        if provider == "h2o" and step["id"] == "temperature":
            raise HTTPException(503, "GPU failed")
        choices = {"power": "unchanged", "temperature": "18" if provider == "decider" else "24", "mode": "unchanged"}
        return {"model": provider, "answers": {step["id"]: {"choice": choices[step["id"]]}}}

    monkeypatch.setattr(app.state.comparison, "infer", infer)
    reply = client.post("/api/comparison/runs", json={"case_id": "device-relative", "variant": "reversed"})
    assert reply.status_code == 202
    job_id = reply.json()["id"]
    assert client.post("/api/comparison/runs", json={"case_id": "device-comfort"}).status_code == 409
    for _ in range(100):
        result = client.get("/api/comparison/runs/" + job_id).json()
        if result["status"] == "done":
            break
        time.sleep(.01)
    assert result["status"] == "done" and peak == 1
    assert list(dict.fromkeys(row[0] for row in calls)) == ["decider", "liquid", "h2o", "jev", "gpt"]
    initial = [r[2] for r in calls if r[1] == "power"]
    assert all(r == initial[0] for r in initial)
    assert list(initial[0]["questions"]["power"]["criteria"]) == ["unchanged", "off", "on"]
    decider, liquid, h2o = (result["outcomes"][k] for k in ("decider", "liquid", "h2o"))
    assert not decider["final"]["complete"] and liquid["final"]["complete"]
    assert h2o["status"] == "error" and h2o["steps"][-1]["status"] == "skipped"
    assert h2o["decision_ms"] == 80 and liquid["decision_ms"] == 120
    assert all(o["decision_ms"] == sum(s.get("decision_ms", 0) for s in o["steps"]) for o in result["outcomes"].values())
    assert "_owner" not in result
    app.state.comparison.jobs[job_id]["_owner"] = "another-user"
    assert client.get("/api/comparison/runs/" + job_id).status_code == 404


def test_resident_gateway_identity_check_and_no_model_switch():
    with TestClient(gateway.app) as client:
        async def response(*args, **kwargs):
            return httpx.Response(200, json={"model": "wrong-model", "answers": {}}, request=httpx.Request("POST", args[0]))
        gateway.app.state.client.post = response
        case = CASES["device-comfort"]
        payload, _ = Workflow(case, case["task"]).prepare("power")
        assert client.post("/v1/systemone/liquid", json=payload).status_code == 503
        assert client.post("/v1/systemone/another-model", json=payload).status_code == 404
        oversized = copy.deepcopy(payload)
        q = next(iter(payload["questions"].values()))
        oversized["questions"] = {f"q{i}": q for i in range(17)}
        assert client.post("/v1/systemone/liquid", json=oversized).status_code == 422


def test_routing_never_executes_targets_automatically(client, monkeypatch):
    async def infer(request, provider, payload, step):
        step["timings"] = {"processing_ms": 10}
        choice = "embedding" if step["id"] == "workflow" else next(iter(payload["questions"]["action"]["criteria"]))
        return {"model": provider, "answers": {"action": {"choice": choice}}}

    execute = MagicMock(side_effect=AssertionError("Target must be optional"))
    monkeypatch.setattr(app.state.comparison, "infer", infer)
    monkeypatch.setattr("app.routing.execute_target", execute)
    response = client.post("/api/comparison/runs", json={"case_id": "routing-retrieval"})
    result = wait_run(client, response.json()["id"])
    assert result["status"] == "done"
    execute.assert_not_called()
    for outcome in result["outcomes"].values():
        assert len(outcome["steps"]) == 2 and outcome["final"]["complete"]
        assert outcome["target_request"]["strategy"] == "original"
        assert outcome["decision_ms"] == 20


def wait_run(client, job_id):
    for _ in range(150):
        result = client.get("/api/comparison/runs/" + job_id).json()
        if result["status"] == "done":
            return result
        time.sleep(.01)
    raise AssertionError("Run did not finish")


def test_inputs_match_captured_live_original_modules(monkeypatch):
    captured = json.loads(Path("tests/fixtures/original_comparison_inputs.json").read_text())
    # This fixture predates quota evidence. Reconstruct its exact pinned catalog
    # to test prompt compatibility; current zero-quota eligibility has separate tests.
    historical = routing.load_catalog()
    for model in historical["models"]:
        model.pop("inference_quotas", None)
    digest = hashlib.sha256(
        json.dumps(
            {"region": historical["region"], "models": historical["models"]}, sort_keys=True
        ).encode()
    ).hexdigest()[:16]
    assert digest == captured["routing_catalog_version"]
    historical["version"] = digest
    monkeypatch.setattr(routing, "load_catalog", lambda: copy.deepcopy(historical))
    from app import comparison_workflows
    historical_rankings = json.loads(
        Path("tests/fixtures/routing_rankings_20261013.json").read_text()
    )
    assert historical_rankings["version"] == captured["ranking_version"]
    monkeypatch.setattr(
        comparison_workflows, "load_rankings", lambda: copy.deepcopy(historical_rankings)
    )
    for row in captured["cases"]:
        case = CASES[row["case_id"]]
        workflow = Workflow(case, case["task"])
        if row.get("workflow"):
            workflow.apply("workflow", {"action": row["workflow"]})
        payload, _ = workflow.prepare(row["stage"])
        assert json.dumps(payload, ensure_ascii=False) == json.dumps(row["model_input"], ensure_ascii=False)
        assert crypto.fingerprint(payload) == row["input_hash"]


def test_payment_decisions_are_frozen_and_all_precede_execution(client, monkeypatch):
    events = []
    original_apply = Workflow.apply
    async def infer(request, provider, payload, step):
        state = json.loads(payload["state"])
        assert state["confirmations"] == 3 and not state["confirmed"]
        assert "execution" not in state and "funding_requirements_met" not in state
        events.append((provider, "decision", step["id"]))
        step["timings"] = {"processing_ms": 10, "backend_total_ms": 12}
        choice = crypto.reference(crypto.Snapshot(**CASES["payment-pending"]["initial"]), step["id"])
        return {"model": provider, "answers": {"action": {"choice": choice}}}
    def apply(self, stage, choices):
        events.append((None, "execution", stage))
        return original_apply(self, stage, choices)
    monkeypatch.setattr(app.state.comparison, "infer", infer)
    monkeypatch.setattr(Workflow, "apply", apply)
    result = wait_run(client, client.post("/api/comparison/runs", json={"case_id":"payment-pending"}).json()["id"])
    assert [r[1] for r in events] == (["decision"]*5 + ["execution"]*5)*5
    for outcome in result["outcomes"].values():
        assert outcome["decision_ms"] == 60 and outcome["matched"] == 5
        assert outcome["final"]["state"]["order_status"] == "open"
        assert outcome["final"]["state"]["ticket_status"] == "waiting"


def test_original_gpt_prompt_and_model_are_reused(client):
    bedrock = MagicMock()
    def reply(**kwargs):
        payload = json.loads(kwargs["messages"][0]["content"][0]["text"])
        choice = next(iter(payload["questions"]["action"]["criteria"]))
        return {"stopReason":"end_turn", "output":{"message":{"content":[{"text":json.dumps({"choice":choice})}]}},
                "usage":{"inputTokens":100,"outputTokens":5}, "metrics":{"latencyMs":2}}
    bedrock.converse.side_effect = reply
    app.state.bedrock = bedrock
    async def run_original(case_id):
        from types import SimpleNamespace
        case = CASES[case_id]
        workflow = Workflow(case, case["task"])
        stage = case["steps"][0]["id"]
        payload, _ = workflow.prepare(stage)
        step = {"original_request":workflow.original_request(stage,"bedrock"),"input_source":case["input_source"]}
        result = await app.state.comparison.infer(SimpleNamespace(app=app),"gpt",payload,step)
        assert result["original_response"]["model_input"] == payload
    client.portal.call(run_original, "payment-pending")
    client.portal.call(run_original, "routing-support")
    calls = [call.kwargs for call in bedrock.converse.call_args_list]
    assert [c["modelId"] for c in calls] == ["us.openai.gpt-6.1-sol","us.openai.gpt-6-sol"]
    for c, purpose in zip(calls, ["payment workflow","model routing"], strict=True):
        assert c["system"][0]["text"].startswith("You are a " + purpose + " classifier.")
        assert '{"choice":"candidate_id"}' in c["system"][0]["text"]
        assert c["inferenceConfig"] == {"maxTokens":1024}
        assert c["additionalModelRequestFields"] == {"reasoning":{"effort":"low"}}
