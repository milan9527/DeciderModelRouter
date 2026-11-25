import asyncio
import hashlib
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app import gateway_main
from app.auth import save_user
from app.gateway_effect_cases import REFERENCE_ERRATA, suite
from app.gateway_effect_judge import grade, summarize
from app.gateway_effects import ASSETS, EffectEvaluations, execution_key
from app.routing import Task
from tests.test_gateway_persistence import client as client


def finished(client, payload):
    response = client.post("/api/gateway/effects", json=payload)
    assert response.status_code == 202, response.text
    jid = response.json()["id"]
    for _ in range(200):
        job = client.get("/api/gateway/effects/" + jid).json()
        if job["status"] != "running":
            assert job["status"] == "completed", job
            return job
        time.sleep(.01)
    pytest.fail("Evaluation did not finish")


def test_catalog_is_broad_and_fixture_integrity():
    catalog = suite()
    assert len(catalog["items"]) >= 80
    assert len({c["family"] for c in catalog["items"]}) >= 25
    assert catalog["version"] == suite()["version"]
    manifest = json.loads((ASSETS / "manifest.json").read_text())
    for case in catalog["items"]:
        Task(**case["task"])
        if case["fixture"]:
            raw = (ASSETS / case["fixture"]).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == manifest[case["fixture"]]["sha256"]


@pytest.mark.parametrize("kind,expected,text,passed", [
    ("number", 904, "904", True), ("number", 904, "It is 904 or 903", False),
    ("number", 3, "NaN", False), ("number", 3, "3.0", True),
    ("exact", "UNKNOWN", "UNKNOWN", True), ("exact", "red", "not red", False),
    ("json", {"a": 1}, '{"a":1}', True), ("json", {"a": 1}, '{"a":true}', False),
    ("json", {"a": 1}, '{"a":2,"a":1}', False), ("json", {"a": 1}, '{"a":NaN}', False),
    ("transcript", "Hello world", "Hello, world!", True),
    ("transcript", "Hello world", "Hello world goodbye", False),
    ("fraction", "1/3", r"\(\frac{1}{3}\)", True),
    ("fraction", "1/3", "⅓", True), ("fraction", "1/3", "1 / 3", True),
    ("fraction", "1/3", "2/6", False), ("fraction", "1/3", "1/0", False),
    ("fraction", "1/3", "Answer: 1/3", False),
])
def test_conservative_judges(kind, expected, text, passed):
    case = {"judge": {"kind": kind, "expected": expected}}
    assert (grade(case, {"complete": True, "text": text})["status"] == "pass") == passed
    assert grade(case, {"complete": False, "text": text})["status"] == "fail"


def test_open_ended_and_artifacts_do_not_automatically_pass():
    c = {"judge": {"kind": "manual", "rubric": "Listen to the words"}}
    assert grade(c, {"complete": True, "artifacts": [{"kind": "audio"}]})["status"] == "pending"
    c = {"judge": {"kind": "rerank", "expected": 1}}
    assert grade(c, {"complete": True, "rerank_results": [{"index": 1}]})["status"] == "pass"
    assert grade(c, {"complete": True, "rerank_results": [{"index": 0}]})["status"] == "fail"


def test_baseline_dedup_key_includes_effective_configuration_and_output_limit():
    row = {"model": {"invoke_id": "test-model"}, "benchmark": {"request_fields": {"effort": "high"}}}
    task = Task(text="hi", routing_preferences={"max_output_tokens": 1024})
    low = task.model_copy(update={"routing_preferences": task.routing_preferences.model_copy(update={"cost_tier": "low"})})
    assert execution_key(row, task, "general_text") == execution_key(row, low, "general_text")
    smaller = Task(text="hi", routing_preferences={"max_output_tokens": 512})
    assert execution_key(row, task, "general_text") != execution_key(row, smaller, "general_text")
    assert execution_key(row, task, "coding") != execution_key(row, task, "general_text")
    other = {**row, "benchmark": {"request_fields": {"effort": "low"}}}
    assert execution_key(other, task, "general_text") != execution_key(row, task, "general_text")


def test_live_pipeline_persists_outputs_deduplicates_and_never_sends_gold(client):
    job = finished(client, {
        "providers": ["h2o"], "custom_cases": [{
            "title": "秘密评分参考", "task": {"text": "只输出一个测试句子", "routing_preferences": {
                "allowed_models": ["amazon.nova-micro-v1:0"], "benchmark_release": "2025-04-02",
            }}, "rubric": "评分秘密不应进入提示词", "expected_text": "重复请求只处理一次。",
        }], "baselines": True,
    })
    assert job["target_model_calls"] == 1 and job["reused_observations"] == 2
    assert len(job["results"]) == 3
    assert all(r["grade"]["status"] == "pass" for r in job["results"])
    assert job["summary"][0]["quality_pass"] == 1
    assert job["summary"][0]["paired_quality_cases"] == 1
    assert "route_token" not in json.dumps(job)
    sent = json.dumps(gateway_main.app.state.bedrock.converse.call_args.kwargs, ensure_ascii=False)
    assert "秘密" not in sent
    assert gateway_main.app.state.bedrock.converse.call_count == 1
    assert job["max_output_tokens"] == 16384
    assert gateway_main.app.state.bedrock.converse.call_args.kwargs["inferenceConfig"]["maxTokens"] == 16384
    assert client.get("/api/gateway/effects").json()["items"][0]["id"] == job["id"]


def test_gaps_budget_and_intentional_no_route_not_semantic_passes(client):
    job = finished(client, {
        "providers": ["h2o"], "case_ids": ["web-search", "arithmetic", "empty-allowlist"],
        "max_target_calls": 0,
    })
    assert job["started_gpu_calls"] == 2 and job["target_model_calls"] == 0
    assert [r["status"] for r in job["results"]] == ["capability_gap", "budget_exhausted", "no_route"]
    assert job["summary"][0]["quality_graded"] == 0
    assert job["summary"][0]["quality_pass_rate"] is None
    assert job["summary"][0]["boundary_pass"] == 1
    assert job["summary"][0]["cost_known_samples"] == 0
    assert not gateway_main.app.state.bedrock.converse.called


def test_throttle_retained_and_not_automatically_retried(client, monkeypatch):
    failing = AsyncMock(side_effect=HTTPException(429, "throttled"))
    monkeypatch.setattr("app.routing.auto_execute", failing)
    job = finished(client, {"providers": ["h2o"], "case_ids": ["arithmetic"]})
    assert job["target_model_calls"] == 1 and failing.await_count == 1
    assert job["results"][0]["error"]["code"] == 429
    assert job["summary"][0]["quality_pass_rate"] == 0


def test_reviews_preserve_automatic_grade_and_are_user_scoped(client):
    job = finished(client, {"providers": ["h2o"], "case_ids": ["greeting"]})
    rid = job["results"][0]["id"]
    assert job["results"][0]["grade"]["status"] == "pending"
    path = f"/api/gateway/effects/{job['id']}/reviews/{rid}"
    review = {"status": "fail", "reason": "实际回答谈论幂等，没有回应问候，未完成任务。"}
    assert client.post(path, json=review).status_code == 200
    assert client.post(path, json=review).status_code == 409
    saved = client.get("/api/gateway/effects/" + job["id"]).json()
    assert saved["results"][0]["grade"]["status"] == "pending"
    assert saved["results"][0]["review"]["status"] == "fail"
    assert saved["summary"][0]["quality_graded"] == 1
    save_user(gateway_main.app.state.auth.store, "another", "other-long-password")
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"username": "another", "password": "other-long-password"})
    assert client.get("/api/gateway/effects/" + job["id"]).status_code == 404
    assert client.post(path, json=review).status_code == 404
    assert client.get("/api/gateway/effects").json()["items"] == []


def test_custom_cases_and_call_limits_are_validated(client):
    assert client.post("/api/gateway/effects", json={"providers": ["h2o"]}).status_code == 422
    assert client.post("/api/gateway/effects", json={"providers": ["h2o"], "case_ids": ["not-real"]}).status_code == 422
    assert client.post("/api/gateway/effects", json={"providers": ["h2o"], "case_ids": ["greeting"], "max_target_calls": 999}).status_code == 422
    assert client.post("/api/gateway/effects", json={"providers": ["h2o", "h2o"], "case_ids": ["greeting"]}).status_code == 422
    assert client.post("/api/gateway/effects", json={"providers": ["h2o"], "case_ids": ["greeting"], "max_output_tokens": 16385}).status_code == 422
    job = finished(client, {"providers": ["h2o"], "case_ids": ["greeting"], "max_output_tokens": 16384, "max_target_calls": 0})
    assert job["results"][0]["task"]["routing_preferences"]["max_output_tokens"] == 16384


@pytest.mark.parametrize("case_fields,expected_cap", [
    ({"case_ids": ["greeting"]}, 16384),
    ({"custom_cases": [{"title": "自定义", "task": {"text": "hi"}, "rubric": "回应问候"}]}, 16384),
    ({"custom_cases": [{"title": "自定义", "task": {"text": "hi", "routing_preferences": {
        "max_output_tokens": 2048,
    }}, "rubric": "回应问候"}]}, 2048),
])
def test_new_budget_applies_without_overriding_explicit_case_limit(client, case_fields, expected_cap):
    job = finished(client, {"providers": ["h2o"], "max_target_calls": 0, **case_fields})
    assert job["max_output_tokens"] == 16384
    assert job["results"][0]["task"]["routing_preferences"]["max_output_tokens"] == expected_cap


def test_summary_separates_bad_answers_incomplete_outputs_and_call_errors():
    def row(cid, status, grade_status, complete=None):
        result = {"case_id": cid, "provider": "h2o", "arm": "routed", "status": status,
                  "grade": {"status": grade_status}}
        if complete is not None:
            result["output"] = {"complete": complete}
        return result

    rows = [
        row("good", "completed", "pass", True),
        row("wrong", "completed", "fail", True),
        row("truncated", "incomplete", "fail", False),
        row("timeout", "error", "fail"),
        row("review-later", "completed", "pending", True),
        row("gap", "capability_gap", "unmeasured"),
    ]
    # A reviewed complete answer uses the recorded review, not its stale auto grade.
    rows.append({**row("corrected", "completed", "fail", True), "review": {"status": "pass"}})
    rows.append({**row("baseline", "incomplete", "fail", False), "arm": "low"})
    result = summarize(rows, ["h2o"], 7)[0]
    assert result["answer_failed"] == result["output_incomplete"] == result["errors"] == 1
    assert result["quality_pass"] == 2 and result["quality_graded"] == 5
    assert result["quality_graded"] == (
        result["quality_pass"] + result["answer_failed"] + result["output_incomplete"] + result["errors"]
    )
    assert result["pending"] == result["gaps"] == 1


def test_summary_compares_only_graded_baselines_with_known_costs():
    rows = [
        {"case_id": "one", "provider": "h2o", "arm": "routed", "status": "completed",
         "grade": {"status": "fail"}, "output": {"complete": True}},
        {"case_id": "one", "provider": "h2o", "arm": "high", "status": "completed",
         "grade": {"status": "pass"}, "output": {"complete": True}},
        {"case_id": "two", "provider": "h2o", "arm": "routed", "status": "completed",
         "grade": {"status": "pass"}, "output": {"complete": True, "observed_reference_cost_usd": ".02"}},
        {"case_id": "two", "provider": "h2o", "arm": "low", "status": "completed",
         "grade": {"status": "pass"}, "output": {"complete": True, "observed_reference_cost_usd": ".01"}},
    ]
    summary = summarize(rows, ["h2o"], 2)[0]
    assert summary["avoidable_failures_vs_tested_baselines"] == 1
    assert summary["lower_cost_passing_alternative_cases"] == 1
    assert summary["reference_cost_above_tested_passing_alternative_usd"] == .01


def test_cancellation_retains_started_attempt_and_stops_future_calls(client, monkeypatch):
    async def blocked(*args, **kwargs):
        await asyncio.sleep(60)

    infer = AsyncMock(side_effect=blocked)
    monkeypatch.setattr(gateway_main.app.state.gateway_decision, "infer", infer)
    response = client.post("/api/gateway/effects", json={
        "providers": ["h2o"], "case_ids": ["greeting", "arithmetic"],
    })
    assert response.status_code == 202
    jid = response.json()["id"]
    for _ in range(100):
        if infer.await_count:
            break
        time.sleep(.01)
    response = client.post(f"/api/gateway/effects/{jid}/cancel")
    assert response.status_code == 200
    job = response.json()
    assert job["status"] == "interrupted"
    assert job["started_gpu_calls"] == 1 and job["target_model_calls"] == 0
    assert infer.await_count == 1
    assert "未知" in job["note"]


def test_result_reads_follow_dynamodb_byte_limit_and_empty_expired_pages():
    records = MagicMock()
    records.query.side_effect = [
        {"items": [{"id": "0000"}], "next_cursor": "first"},
        {"items": [], "next_cursor": "second"},  # expired records can leave an empty page
        {"items": [{"id": "0001"}], "next_cursor": None},
    ]
    effects = EffectEvaluations(SimpleNamespace(records=records), None)
    assert effects.rows("alice", "job") == [{"id": "0000"}, {"id": "0001"}]
    assert [call.kwargs["cursor"] for call in records.query.call_args_list] == [None, "first", "second"]
    assert all(call.args[:2] == ("USER#alice", "EROW#job#") for call in records.query.call_args_list)


def test_reference_erratum_preserves_old_snapshot_and_is_backed_by_original_fixture():
    entry = REFERENCE_ERRATA[0]
    assert hashlib.sha256((ASSETS / "flower.mp4").read_bytes()).hexdigest() == entry["fixture_sha256"]
    effects = EffectEvaluations(None, None)
    old_case = {"judge": {"rubric": entry["original"]}}
    effects.header = lambda *args: {"dataset_version": entry["affected_version"], "providers": [], "cases": [old_case]}
    effects.rows = lambda *args: []
    result = effects.get("alice", "old-job")
    assert result["cases"][0]["judge"]["rubric"] == entry["original"]
    assert result["reference_errata"] == [entry]
    current = next(c for c in suite()["items"] if c["id"] == "video-caption")
    assert current["judge"]["rubric"] == entry["corrected"]
    assert suite()["version"] != entry["affected_version"]
