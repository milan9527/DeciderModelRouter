import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.agent_model import AgentConversation, agent_converse
from app.gateway_conversation import SessionOptions
from app.gateway_decision import GatewayDecisionService
from app.gateway_joint_decision import joint_payload
from app.gateway_model_decision import select_all
from app.gateway_model_pool import build_pool
from app.routing import Task, exclusion, load_catalog, load_rankings
from app.routing_profile import profile_payload, replay_profile


def test_default_and_rollback_mode(monkeypatch):
    monkeypatch.delenv("GATEWAY_DECISION_MODE", raising=False)
    assert GatewayDecisionService(None).public_config()["routing_mode"] == "two_stage"
    monkeypatch.setenv("GATEWAY_DECISION_MODE", "joint")
    assert GatewayDecisionService(None).routing_mode == "joint"
    monkeypatch.setenv("GATEWAY_DECISION_MODE", "typo")
    with pytest.raises(RuntimeError):
        GatewayDecisionService(None)


def test_no_candidates_preserves_first_stage_and_never_invokes_second():
    async def check():
        service = MagicMock()
        pool = {"candidates": [], "assessment": {
            "mode": "profile", "timings": {"backend_total_ms": 123},
        }}
        result = await select_all(service, None, SimpleNamespace(state=SimpleNamespace()), pool)
        assert result["decision_mode"] == "two_stage"
        assert result["decision_ms"] == 123 and result["selection_ms"] == 0
        assert len(result["decision_calls"]) == 1
        service.infer.assert_not_called()
    asyncio.run(check())


def test_agent_routing_sees_current_tool_result_prior_constraint_and_first_stage_profile():
    runtime = object.__new__(AgentConversation)
    runtime.messages = [
        {"role": "user", "text": "Use invoice ID to deduplicate; never say payment was sent."},
        {"role": "assistant", "text": "Reading invoices."},
        {"role": "user", "text": "tool result"},
    ]
    runtime.native_messages = [
        {"role": "user", "content": [{"text": runtime.messages[0]["text"]}]},
        {"role": "assistant", "content": [{"text": "Please supply the invoice."}]},
        {"role": "user", "content": [{"text": "Prepare a short handoff."}]},
        {"role": "assistant", "content": [{"toolUse": {
            "toolUseId": "id1", "name": "query_records", "input": {"dataset": "invoices"},
        }}]},
        {"role": "user", "content": [{
        "toolResult": {"toolUseId": "id1", "content": [{"json": {"invoice": "I32", "amount": 37}}]},
    }]}]
    runtime.agent_system = "Only read records; do not send payments."
    runtime.options = SessionOptions()
    runtime.context = {"history_turns": 1}
    runtime.previous = None
    task = Task(text="Prepare a short handoff.")
    profile = replay_profile(task, "reasoning")
    payload = runtime.classify(profile_payload(task))
    state = json.loads(payload["state"])
    assert "I32" in state["current_tool_results"]
    assert "deduplicate" in state["prior_user_requirements"][0]
    assert "I32" not in json.dumps(state["prior_user_requirements"])
    assert state["agent_execution"]["current_user_request"] == task.text
    state["task_profile"] = profile.model_dump()
    body = SimpleNamespace(task=task)
    request = SimpleNamespace(state=SimpleNamespace(gateway_runtime=runtime))
    result, _ = joint_payload(body, request, {"state": json.dumps(state), "questions": {}},
                              [], assessed=True)
    selected_state = json.loads(result["state"])
    assert selected_state["task_profile"] == profile.model_dump()
    assert selected_state["current_tool_results"] == state["current_tool_results"]
    assert "simultaneously" not in result["questions"]["action"]["instructions"]


def test_endpoint_denial_is_bound_to_exact_invocation_and_keeps_usable_legacy():
    from app.bedrock_evidence import load_evidence

    task = Task(text="Explain retries.")
    profile = replay_profile(task, "general_text")
    catalog, ranks, evidence = load_catalog(), load_rankings(), load_evidence()
    mid = "amazon.nova-micro-v1:0"
    original = copy.deepcopy(evidence)
    evidence["entries"][mid]["availability"] = {"available": False, "reason": "probe denial"}
    pool = build_pool(task, profile, catalog, ranks, exclusion, evidence=evidence)
    assert "probe denial" in pool["excluded"][mid]
    evidence["entries"][mid]["invoke_id"] = "another endpoint"
    pool = build_pool(task, profile, catalog, ranks, exclusion, evidence=evidence)
    assert mid in {r["model"]["id"] for r in pool["candidates"]}
    pool = build_pool(task, profile, catalog, ranks, exclusion, evidence=original)
    assert any(r["model"]["lifecycle"] == "LEGACY" for r in pool["candidates"])


def test_measured_image_quality_does_not_invent_an_execution_adapter():
    from app.bedrock_evidence import load_evidence
    from app.routing_media import adapter

    task = Task(text="Create an image.", output_modality="image")
    profile = replay_profile(task, "image")
    catalog, ranks, evidence = load_catalog(), load_rankings(), load_evidence()
    models = []
    for mid in ("amazon.nova-canvas-v1:0", "test.no-image-adapter"):
        models.append({**catalog["models"][0], "id": mid, "name": mid, "invoke_id": mid,
                       "input_modalities": ["TEXT"], "output_modalities": ["IMAGE"],
                       "account_available": True, "lifecycle": "ACTIVE", "destination_regions": ["us-east-1"]})
        evidence["entries"][mid] = {"invoke_id": mid, "capabilities": {"image": True}}
    catalog["models"] = models
    pool = build_pool(task, profile, catalog, ranks, lambda *args: None, evidence=evidence)
    assert pool["candidates"]
    assert all(adapter(r["model"], task, "image") for r in pool["candidates"])


@pytest.mark.parametrize("invalid", [False, True])
def test_blank_text_with_tool_call_is_portable_but_invalid_tool_is_rejected(invalid):
    async def check():
        client = MagicMock()
        message = {"role": "assistant", "content": [{"text": "\n"}, {"toolUse": {
            "toolUseId": "tool1", "name": "send_payment" if invalid else "read_policy", "input": {},
        }}]}
        client.converse.return_value = {"output": {"message": message}, "stopReason": "tool_use"}
        runtime = SimpleNamespace(native_messages=[{"role": "user", "content": [{"text": "Read policy."}]}],
                                  agent_system="Read only.", options=SessionOptions(prompt_cache=False))
        row = {"model": {"id": "test", "invoke_id": "test"}, "benchmark": {}}
        if invalid:
            with pytest.raises(HTTPException):
                await agent_converse(client, row, Task(text="Read policy."), runtime)
        else:
            result = await agent_converse(client, row, Task(text="Read policy."), runtime)
            assert result["complete"] and len(result["message"]["content"]) == 1
            assert result["provider_metadata_omitted"]["empty_text"] == 1
        assert client.converse.call_count == 1
        assert message["content"][0] == {"text": "\n"}
    asyncio.run(check())
