import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.gateway_decision import GatewayDecisionService
from app.routing import Task
from app.routing_profile import profile_payload, replay_profile
from deployment.open_models.liquid_adapter import selection_input


def test_adapter_is_only_enabled_for_the_second_text_evidence_request():
    state = {"candidates": {"m000": "public score"}, "primary_benchmark": {"source": "livebench"}}
    assert selection_input({"questions": {"action": {}}, "state": json.dumps(state)})
    assert not selection_input({"questions": {"action": {}, "workflow": {}}, "state": json.dumps(state)})
    assert not selection_input({"questions": {"workflow": {}}, "state": json.dumps(state)})
    state["primary_benchmark"] = None
    assert not selection_input({"questions": {"action": {}}, "state": json.dumps(state)})


@pytest.mark.parametrize("state", [
    None, "not json", "[]", "null", '""', "{}",
    '{"request":"{\\"candidates\\":{},\\"primary_benchmark\\":{\\"source\\":\\"livebench\\"}}"}',
    '{"candidates":{"m000":"score"},"primary_benchmark":{"source":"another_source"}}',
])
def test_request_text_or_malformed_metadata_cannot_activate_adapter(state):
    assert not selection_input({"questions": {"action": {}}, "state": state})


def inference(adapter, *, selection=True):
    async def run():
        task = Task(text="Calculate the total")
        payload = {"state": "request", "questions": {"action": {
            "type": "choice", "instructions": "Choose", "criteria": {"a": "A", "b": "B"}}}}
        answers = {"action": {"choice": "a"}}
        if not selection:
            payload = profile_payload(task)
            answers = {k: {"choice": v} for k, v in replay_profile(task, "reasoning").model_dump().items()}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={
            "model": "LiquidAI/d1-2B", "answers": answers, "selection_adapter": adapter,
        }))) as client:
            service = GatewayDecisionService(client)
            service.services["liquid"]["base"] = "http://gpu"
            return await service.infer(SimpleNamespace(provider="liquid"),
                                       SimpleNamespace(), payload, selection=selection)
    return asyncio.run(run())


def metadata():
    return {"version": "public-evidence-lora-v1", "sha256": "a" * 64,
            "base_revision": "b" * 40, "active": True, "scope": "text_selection_only"}


def test_adapter_provenance_is_preserved_and_base_assessment_remains_explicit():
    assert inference(metadata())["selection_adapter"] == metadata()
    inactive = {**metadata(), "active": False}
    assert inference(inactive, selection=False)["selection_adapter"] == inactive
    assert inference(None)["selection_adapter"] is None


@pytest.mark.parametrize("change", [
    {"active": 1}, {"sha256": "unverified"}, {"base_revision": "unknown"},
    {"scope": "all_calls"}, {"version": None},
])
def test_invalid_adapter_provenance_is_rejected(change):
    with pytest.raises(HTTPException) as error:
        inference({**metadata(), **change})
    assert error.value.status_code == 502


def test_a_service_cannot_silently_apply_selection_weights_to_the_first_assessment():
    with pytest.raises(HTTPException) as error:
        inference(metadata(), selection=False)
    assert error.value.status_code == 502
