import copy
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app import routing
from app.main import app
from app.routing_balance import RoutingPreferences
from app.routing_profile import RequestProfile, bedrock_profile, parse_profile, profile_payload
from app.routing_selector import (
    SELECTOR_VERSION,
    choose,
    configuration_id,
    configurations,
    load_calibration,
)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("BEDROCK_ENABLED", raising=False)
    with TestClient(app, headers={"X-Decider-Client": "web"}) as client:
        assert (
            client.post(
                "/api/auth/login",
                json={
                    "username": "tester",
                    "password": "test-password-with-entropy",
                },
            ).status_code
            == 200
        )
        yield client


def profile(**changes):
    return RequestProfile(
        **{
            "workflow": "coding",
            "difficulty": "moderate",
            "stronger_model_benefit": "medium",
            "latency": "normal",
            "tools": "no",
            "structured_output": "no",
            "languages": "zh",
            **changes,
        }
    )


def answer(value=None):
    return {
        "model": "test-classifier",
        "answers": {k: {"choice": v} for k, v in (value or profile()).model_dump().items()},
        "usage": {"input_tokens": 100, "output_tokens": 20},
        "timings": {"backend_total_ms": 20},
    }


def body(**changes):
    return {
        "task": {"text": "编写支付 webhook，包含幂等控制"},
        "provider": "bedrock",
        "mode": "live",
        "catalog_version": routing.load_catalog()["version"],
        "ranking_version": routing.load_rankings()["version"],
        "policy_version": SELECTOR_VERSION,
        **changes,
    }


def synthetic_pool():
    task = routing.Task(text="test")
    ranks = routing.load_rankings()
    pool = configurations(routing.load_catalog(), ranks, task, "coding", routing.exclusion)
    by_model = {}
    for row in pool["candidates"]:
        by_model.setdefault(row["model"]["id"], row)
    pool["candidates"] = list(by_model.values())[:3]
    # An analytically known tradeoff: cheap=50, middle=75, premium=100.
    for row, score, price in zip(pool["candidates"], (50, 75, 100), (1, 2, 3), strict=True):
        row["benchmark"]["category_scores"] = {"Coding": score, "Agentic Coding": score}
        row["benchmark"].update(dataset_revision="synthetic-same-release", release="2026-01-01")
        row["benchmark"]["metrics"].update(input_price=price, output_price=price)
    return task, ranks, pool


def test_one_assessment_then_deterministic_selection_and_execution_receipt(client, monkeypatch):
    infer = AsyncMock(return_value=answer())
    old_infer = AsyncMock(side_effect=AssertionError("No second model call"))
    monkeypatch.setattr(routing, "infer_profile", infer)
    monkeypatch.setattr(routing, "infer", old_infer)
    result = client.post("/api/routing/auto", json=body())
    assert result.status_code == 200
    data = result.json()
    flow, selected = data["steps"]
    assert flow["source"] == "live" and flow["assessment_count"] == 7
    assert selected["source"] == "deterministic_policy" and selected["model"] is None
    assert selected["route_token"] and selected["selection"]["evidence_mode"] == "benchmark_proxy"
    assert selected["selected_model"]["id"] in {m["id"] for m in routing.load_catalog()["models"]}
    assert infer.call_count == 1
    old_infer.assert_not_called()
    payload = infer.call_args.args[2]
    assert "candidates" not in json.loads(payload["state"])
    assert len(payload["questions"]) == 7
    app.state.bedrock = MagicMock()
    app.state.bedrock.converse.return_value = {
        "output": {"message": {"content": [{"text": "code"}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 10, "outputTokens": 20},
    }
    response = client.post(
        "/api/routing/auto/execute", json={"route_token": selected["route_token"]}
    )
    assert response.status_code == 200
    assert response.json()["model_id"] == selected["selected_model"]["id"]
    assert infer.call_count == 1
    call = app.state.bedrock.converse.call_args.kwargs
    assert (
        call.get("additionalModelRequestFields", {})
        == selected["selection"]["selected_benchmark"]["request_fields"]
    )


def test_bedrock_classifier_uses_one_converse_request_for_every_attribute(client):
    app.state.bedrock = MagicMock()
    app.state.bedrock.converse.return_value = {
        "output": {"message": {"content": [{"text": json.dumps(profile().model_dump())}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 100, "outputTokens": 60},
    }
    response = client.post("/api/routing/auto", json=body())
    assert response.status_code == 200, response.text
    assert app.state.bedrock.converse.call_count == 1
    call = app.state.bedrock.converse.call_args.kwargs
    assert call["modelId"] == routing.ROUTER_MODEL
    assert len(json.loads(call["messages"][0]["content"][0]["text"])["questions"]) == 7


@pytest.mark.parametrize(
    "changes",
    [
        {"profile": profile().model_dump()},
        {"model_id": "arbitrary"},
        {"workflow": "coding"},
    ],
)
def test_caller_cannot_override_model_assessment(client, changes):
    assert client.post("/api/routing/auto", json=body(**changes)).status_code == 422


@pytest.mark.parametrize("field", ["catalog_version", "ranking_version", "policy_version"])
def test_stale_auto_versions_rejected_before_inference(client, monkeypatch, field):
    infer = AsyncMock()
    monkeypatch.setattr(routing, "infer_profile", infer)
    assert client.post("/api/routing/auto", json=body(**{field: "old"})).status_code == 409
    infer.assert_not_called()


def test_incomplete_or_invalid_profile_is_not_filled_in(client, monkeypatch):
    malformed = answer()
    del malformed["answers"]["difficulty"]
    monkeypatch.setattr(routing, "infer_profile", AsyncMock(return_value=malformed))
    assert client.post("/api/routing/auto", json=body()).status_code == 502
    with pytest.raises(ValueError):
        parse_profile({"answers": {"workflow": {"choice": "coding"}}})
    malformed = answer()
    malformed["answers"]["difficulty"]["choice"] = ["complex"]
    with pytest.raises(ValueError):
        parse_profile(malformed)


def test_bedrock_refuses_incomplete_generation_and_extra_profile_keys():
    runtime = MagicMock()
    runtime.converse.return_value = {"stopReason": "max_tokens"}
    with pytest.raises(ValueError):
        bedrock_profile(runtime, routing.ROUTER_MODEL, profile_payload(routing.Task(text="x")))


@pytest.mark.parametrize(
    "policy",
    [
        {"min_success_rate": 0.9},
        {"max_latency_ms": 500},
        {"min_context_tokens": 10000},
        {"allowed_models": []},
    ],
)
def test_unknown_quality_latency_context_or_empty_allowlist_returns_no_route(
    client, monkeypatch, policy
):
    monkeypatch.setattr(routing, "infer_profile", AsyncMock(return_value=answer()))
    response = client.post(
        "/api/routing/auto", json=body(task={"text": "x", "routing_preferences": policy})
    )
    assert response.status_code == 200
    assert response.json()["status"] == "no_route"
    assert response.json()["steps"][1]["route_token"] is None


def test_tools_are_not_assumed_supported(client, monkeypatch):
    monkeypatch.setattr(
        routing, "infer_profile", AsyncMock(return_value=answer(profile(tools="yes")))
    )
    response = client.post("/api/routing/auto", json=body())
    assert response.json()["status"] == "no_route"


def test_cost_tiers_are_numerical_and_order_independent():
    task, ranks, pool = synthetic_pool()
    ids = [r["candidate_id"] for r in pool["candidates"]]
    for tier, expected in zip(("low", "medium", "high"), ids, strict=True):
        task.routing_preferences = RoutingPreferences(cost_tier=tier)
        chosen = choose(task, profile(), copy.deepcopy(pool), ranks, load_calibration())
        assert chosen["selected_candidate_id"] == expected
        reversed_pool = copy.deepcopy(pool)
        reversed_pool["candidates"].reverse()
        assert (
            choose(task, profile(), reversed_pool, ranks, load_calibration())[
                "selected_candidate_id"
            ]
            == expected
        )
    task.routing_preferences = RoutingPreferences()
    simple = profile(difficulty="simple", stronger_model_benefit="low")
    complex_ = profile(difficulty="complex", stronger_model_benefit="high")
    assert (
        choose(task, simple, copy.deepcopy(pool), ranks, load_calibration())[
            "selected_candidate_id"
        ]
        == ids[0]
    )
    assert (
        choose(task, complex_, copy.deepcopy(pool), ranks, load_calibration())[
            "selected_candidate_id"
        ]
        == ids[2]
    )


def test_missing_category_evidence_cannot_win_on_price():
    task, ranks, pool = synthetic_pool()
    first = pool["candidates"][0]
    first["benchmark"]["category_scores"]["Agentic Coding"] = None
    task.routing_preferences = RoutingPreferences(cost_tier="low")
    result = choose(task, profile(), pool, ranks, load_calibration())
    assert result["selected_candidate_id"] != first["candidate_id"]
    assert "分项" in result["excluded"][first["candidate_id"]]


def test_configuration_variants_require_their_own_evidence_and_are_bedrock_only():
    catalog, ranks = routing.load_catalog(), routing.load_rankings()
    mid = "amazon.nova-micro-v1:0"
    original = ranks["entries"][mid]
    variant = copy.deepcopy(original)
    variant["benchmark_configuration"] = "test-low-effort"
    variant["request_fields"] = {"reasoning": {"effort": "low"}}
    variant["category_scores"] = {"Coding": 10, "Agentic Coding": 10}
    ranks["entries"][mid]["configurations"] = [variant]
    ranks["entries"]["not-in-bedrock"] = copy.deepcopy(variant)
    rows = configurations(catalog, ranks, routing.Task(text="x"), "coding", routing.exclusion)[
        "candidates"
    ]
    variants = [r for r in rows if r["model"]["id"] == mid]
    assert len(variants) == 2
    assert len({r["candidate_id"] for r in variants}) == 2
    assert variants[1]["benchmark"]["category_scores"]["Coding"] == 10
    assert all(r["model"]["id"] != "not-in-bedrock" for r in rows)
    assert configuration_id(mid, variant) != configuration_id(mid, original)


def test_embedding_language_is_respected_without_claiming_unknown_price_is_free():
    catalog, ranks, task = routing.load_catalog(), routing.load_rankings(), routing.Task(text="x")
    pool = configurations(catalog, ranks, task, "embedding", routing.exclusion)
    result = choose(
        task, profile(workflow="embedding", languages="en_zh"), pool, ranks, load_calibration()
    )
    assert result["cost_mode"] == "unknown_quality_only"
    chosen = next(
        r for r in result["candidates"] if r["candidate_id"] == result["selected_candidate_id"]
    )
    assert chosen["model"]["id"] != "cohere.embed-english-v3"
    assert chosen["cost_evidence"]["expected_usd"] is None
    task.routing_preferences = RoutingPreferences(max_reference_cost_usd=1)
    assert (
        choose(task, profile(workflow="embedding"), pool, ranks, load_calibration())[
            "selected_candidate_id"
        ]
        is None
    )


def test_receipts_reject_tampering_expiry_and_changed_availability(client, monkeypatch):
    monkeypatch.setattr(routing, "infer_profile", AsyncMock(return_value=answer()))
    response = client.post("/api/routing/auto", json=body()).json()
    step = response["steps"][1]
    token = step["route_token"]
    assert (
        client.post("/api/routing/auto/execute", json={"route_token": "a" * 43}).status_code == 409
    )
    assert (
        client.post(
            "/api/routing/auto/execute", json={"route_token": token, "model_id": "x"}
        ).status_code
        == 422
    )
    catalog = routing.load_catalog()
    for row in catalog["models"]:
        if row["id"] == step["selected_model"]["id"]:
            row["account_available"] = False
    monkeypatch.setattr(routing, "load_catalog", lambda: catalog)
    assert client.post("/api/routing/auto/execute", json={"route_token": token}).status_code == 409
    item = app.state.auth.store.get(routing.route_key(token))
    item["expires_at"] = 0
    app.state.auth.store.put(item)
    assert client.post("/api/routing/auto/execute", json={"route_token": token}).status_code == 409


def test_replay_has_no_live_timing_or_execution_token(client):
    result = client.post("/api/routing/auto", json=body(mode="replay")).json()
    assert result["status"] == "selected" and result["route_total_ms"] is None
    for step in result["steps"]:
        assert step["source"] == "rule_replay" and step["timings"] is None
    assert result["steps"][1]["route_token"] is None
