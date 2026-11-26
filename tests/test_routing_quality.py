import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.gateway_model_decision import select_all
from app.quality_feedback import evidence_for, load_feedback
from app.routing import Task
from app.routing_quality import accepted, attach_quality, quality_policy


def candidate(cid, scores=None, *, price="0.01", benchmark="LiveBench 2", release="2026-11-06"):
    return {
        "candidate_id": cid,
        "model": {"id": cid, "name": cid, "invoke_id": cid, "routing_capabilities": {}},
        "benchmark": {"request_fields": {}, "inference_config": {}},
        "cost_evidence": {"expected_usd": price},
        "measured_evidence": {"suite": "six", "samples": 6, "content_passed": 6,
                              "content_scored": 6, "strict_passed": 6},
        "public_benchmarks": [{
            "id": "evidence-" + cid, "source": "livebench", "benchmark": benchmark,
            "release": release, "metric": "mean_task_score_0_100",
            "configuration": "highest_supported_effort", "status": "completed",
            "scores": scores, "sample_counts": {k: 200 for k in scores},
        }] if scores is not None else [],
    }


def fixture():
    rows = [candidate("cheap", price="0.0001"),
            candidate("strong-a", {"Reasoning": 93, "Data Analysis": 81}),
            candidate("strong-b", {"Reasoning": 91, "Data Analysis": 84}),
            candidate("weak", {"Reasoning": 70, "Data Analysis": 60})]
    profile = {"workflow": "reasoning", "difficulty": "complex", "stronger_model_benefit": "high"}
    task = Task(text="Compare causal explanations from supplied records.",
                routing_preferences={"cost_tier": "high"})
    return task, profile, rows


def test_quality_is_not_cheapest_six_of_six_or_a_cpu_winner():
    task, profile, rows = fixture()
    policy = quality_policy(task, profile, rows)
    assert set(policy["allowed_candidate_ids"]) == {"strong-a", "strong-b"}
    assert not accepted(policy, "cheap")
    assert not accepted(policy, "weak")
    assert policy["assessments"]["cheap"]["evidence"] is None
    assert policy["comparison"]["scope"] == "external_same_model_not_current_Bedrock_configuration"
    reversed_rows = copy.deepcopy(rows[::-1])
    for row in reversed_rows:
        row["cost_evidence"]["expected_usd"] = "9999"
    assert set(quality_policy(task, profile, reversed_rows)["allowed_candidate_ids"]) == {"strong-a", "strong-b"}
    assert "selected_candidate_id" not in policy


def test_quality_follows_changed_evidence_not_model_names():
    task, profile, rows = fixture()
    rows[0]["public_benchmarks"] = copy.deepcopy(rows[1]["public_benchmarks"])
    rows[0]["public_benchmarks"][0]["scores"] = {"Reasoning": 99, "Data Analysis": 99}
    assert quality_policy(task, profile, rows)["allowed_candidate_ids"] == ["cheap"]


def test_public_model_score_does_not_validate_an_unmeasured_reasoning_configuration():
    task, profile, rows = fixture()
    rows[1]["measured_evidence"]["p95_ms"] = 600
    high_effort = copy.deepcopy(rows[1])
    high_effort.update(candidate_id="strong-a-max", measured_evidence={})
    high_effort["benchmark"]["request_fields"] = {"reasoning": {"effort": "max"}}
    rows.append(high_effort)
    policy = quality_policy(task, profile, rows)
    assert accepted(policy, "strong-a")
    assert not accepted(policy, "strong-a-max")
    assert policy["assessments"]["strong-a-max"]["unmeasured_configuration"]
    # Direct measurement of this exact configuration changes its eligibility.
    high_effort["measured_evidence"] = copy.deepcopy(rows[1]["measured_evidence"])
    assert accepted(quality_policy(task, profile, rows), "strong-a-max")


def test_no_cross_release_averaging_or_unknown_as_zero():
    task, profile, _ = fixture()
    rows = [candidate("a", {"Reasoning": 99, "Data Analysis": 99}, release="2026-01-01"),
            candidate("b", {"Reasoning": 1, "Data Analysis": 1}, release="2026-02-01")]
    for row in rows:
        row["measured_evidence"] = {}
    policy = quality_policy(task, profile, rows)
    assert policy["comparison"] is None and policy["evidence_gap"]
    assert set(policy["allowed_candidate_ids"]) == {"a", "b"}


def test_agent_comparison_requires_agent_evidence_too():
    task, profile, rows = fixture()
    for row in rows[1:]:
        row["public_benchmarks"][0]["scores"]["Long Agentic"] = 90 if row["candidate_id"] == "strong-b" else 40
    policy = quality_policy(task, profile, rows, agent=True)
    assert policy["comparison"]["dimensions"] == ["Reasoning", "Data Analysis", "Long Agentic"]
    assert accepted(policy, "strong-b")
    assert not accepted(policy, "cheap")


def test_non_quality_preferences_keep_every_original_candidate():
    task, profile, rows = fixture()
    for tier in ("medium", "low"):
        task.routing_preferences.cost_tier = tier
        policy = quality_policy(task, profile, rows)
        assert not policy["active"]
        assert len(policy["allowed_candidate_ids"]) == len(rows)


def test_repair_is_a_bounded_real_gpu_choice_with_full_first_call_audit():
    async def check():
        task, profile, rows = fixture()
        pool = {"candidates": rows, "profile": profile, "reason": "original",
                "selection_source": "gpu_decision_model"}
        body = SimpleNamespace(task=task, provider="liquid")
        request = SimpleNamespace(state=SimpleNamespace())

        class Service:
            payloads = []

            async def infer(self, body, request, payload, **kwargs):
                self.payloads.append(payload)
                desired = "cheap" if len(self.payloads) == 1 else "strong-b"
                key = next(k for k, text in payload["questions"]["action"]["criteria"].items()
                           if text.startswith(desired + ";"))
                return {"model": "actual-gpu", "answers": {"action": {"choice": key}},
                        "timings": {"backend_total_ms": 1}, "usage": None}
        service = Service()
        result = await select_all(service, body, request, pool)
        assert result["selected_candidate_id"] == "strong-b"
        assert result["quality_reselection_count"] == 1
        assert len(service.payloads) == 2
        assert len(service.payloads[0]["questions"]["action"]["criteria"]) == 4
        assert len(service.payloads[1]["questions"]["action"]["criteria"]) == 2
        assert result["decision_calls"][0]["quality_check"]["accepted"] is False
        assert result["decision_calls"][1]["quality_check"]["accepted"] is True
        assert result["decision_calls"][1]["mode"] == "quality_reselection"
        assert "QUALITY FIRST" in service.payloads[0]["questions"]["action"]["instructions"]
        assert json.loads(service.payloads[1]["state"])["quality_reselection"]["rejected_candidate"] == "cheap"
    asyncio.run(check())


def test_rejected_selection_never_silently_downgrades_on_execution_fallback():
    async def check():
        task, profile, rows = fixture()
        original = {"candidates": rows, "profile": profile}
        policy = attach_quality(original, task)
        # All policy-compliant endpoints have already failed.
        pool = {**original, "candidates": rows[:1], "quality_policy": policy, "reason": "fallback"}

        class Service:
            calls = 0

            async def infer(self, body, request, payload, **kwargs):
                self.calls += 1
                key = next(k for k, text in payload["questions"]["action"]["criteria"].items()
                           if text.startswith("cheap;"))
                return {"model": "gpu", "answers": {"action": {"choice": key}},
                        "timings": {}, "usage": None}
        service = Service()
        result = await select_all(service, SimpleNamespace(task=task, provider="liquid"),
                                  SimpleNamespace(state=SimpleNamespace()), pool)
        assert service.calls == 1
        assert result["selected_candidate_id"] is None and not result["execution_ready"]
        assert result["decision_rejection"] == "quality_requirement_not_met"
    asyncio.run(check())


def test_reviewed_feedback_is_configuration_endpoint_and_workflow_specific():
    data = load_feedback()
    record = next(r for r in data["records"] if r["case_id"] == "manufacturing-causal-regression")
    row = {"candidate_id": record["candidate_id"], "model": {"invoke_id": record["invoke_id"]}}
    evidence = evidence_for(row, record["workflow"])
    assert evidence and evidence["content_passed"] < evidence["samples"]
    failures = [r for r in evidence["records"] if r["case_id"] == record["case_id"]]
    assert len(failures) == 2 and all(r["content_status"] == "fail" for r in failures)
    assert "复杂产线分析" in evidence["failure_categories"]
    assert evidence_for(row, "coding") is None
    row["model"]["invoke_id"] = "other-endpoint"
    assert evidence_for(row, record["workflow"]) is None


def test_direct_media_evidence_is_separate_and_preserves_small_sample_scope():
    task, profile, rows = fixture()
    profile["workflow"] = "audio_understanding"
    rows[0]["measured_evidence"]["content_passed"] = 0
    rows[0]["measured_evidence"]["strict_passed"] = 0
    policy = quality_policy(task, profile, rows)
    assert policy["comparison"]["source"] == "bedrock_development"
    assert policy["comparison"]["scope"] == "small_visible_development_sample"
    assert not accepted(policy, "cheap")


@pytest.mark.parametrize("stale", [
    "measurement_version", "routing_version", "quality_feedback_version", "public_benchmark_version", None,
])
def test_execution_rechecks_evidence_versions_and_quality_before_target(monkeypatch, stale):
    from app import gateway_model_pool, routing
    from app.routing_profile import replay_profile
    from app.routing_selector import SELECTOR_VERSION, load_calibration

    task, profile, rows = fixture()
    versions = dict(measurement_version="measured", routing_version="policy",
                    quality_feedback_version="reviewed", public_benchmark_version="public")
    pool = {"candidates": rows, "profile": replay_profile(task, profile["workflow"]).model_dump(), **versions}
    record = {
        "task": task.model_dump(), "profile": pool["profile"],
        "configuration_id": "cheap", "routing_source": "gpu_decision_model",
        "catalog_version": routing.load_catalog()["version"],
        "ranking_version": routing.load_rankings()["version"],
        "calibration_version": load_calibration()["version"],
        "policy_version": SELECTOR_VERSION, **versions,
    }
    if stale:
        record[stale] = "previous-version"
    item = {"username": "tester", "expires_at": routing.unix_time() + 60,
            "record": json.dumps(record)}
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(
            auth=SimpleNamespace(store=SimpleNamespace(get=lambda key: item)))),
        state=SimpleNamespace(user={"username": "tester"}))
    monkeypatch.setattr(gateway_model_pool, "build_pool", lambda *args: pool)

    async def forbidden(*args, **kwargs):
        pytest.fail("Rejected receipt must not invoke Bedrock")
    monkeypatch.setattr(routing, "execute_selected", forbidden)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(routing.auto_execute(routing.AutoExecute(route_token="a" * 32), request))
    assert caught.value.status_code == 409
    assert ("已更新" if stale else "能力优先校验") in caught.value.detail
