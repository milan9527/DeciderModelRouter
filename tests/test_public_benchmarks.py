import copy
import json
from types import SimpleNamespace

import pytest

from app.gateway_joint_decision import joint_payload
from app.gateway_model_pool import build_pool
from app.public_benchmarks import digest, evidence_for, load_public_benchmarks
from app.routing import Task, exclusion, load_catalog, load_rankings
from app.routing_profile import RequestProfile, profile_payload
from scripts.refresh_public_benchmarks import (
    compile_livebench_v1,
    compile_livebench_v2,
    compile_openrouter_page,
    compile_openrouter_runs,
    number,
)


def flight(data):
    # Split a JSON record across two Flight chunks, as the production site does.
    text = "x:" + json.dumps(data) + "\n"
    parts = [text[:17], text[17:]]
    return "".join("<script>self.__next_f.push(" + json.dumps([1, p]) + ")</script>" for p in parts).encode()


def test_public_scores_require_exact_model_and_version_and_preserve_zero():
    row = {"modelSlug": "vendor/example", "modelPermaslug": "vendor/example-v2",
           "median": {"accuracy": 0}, "providers": []}
    binding = {"bedrock.example": {"openrouter_id": "vendor/example", "canonical_slug": "vendor/example-v1"}}
    assert not compile_openrouter_page(flight(row), "gpqa", binding, "2026-11-26")
    binding["bedrock.example"]["canonical_slug"] = "vendor/example-v2"
    evidence = compile_openrouter_page(flight(row), "gpqa", binding, "2026-11-26")["bedrock.example"][0]
    assert evidence["scores"] == {"GPQA Diamond": 0}
    assert evidence["sample_counts"] == {"GPQA Diamond": None}
    assert evidence["configuration"] == "effort_unreported"
    assert evidence["release"] == "snapshot-2026-11-26"


@pytest.mark.parametrize("value", [True, "nan", "inf", -1, 101])
def test_invalid_public_scores_fail_closed(value):
    with pytest.raises(ValueError):
        number(value, maximum=100)


def test_livebench_two_partial_categories_keep_measured_zero_without_imputing_missing():
    raw = {"table.csv": b"model,reasoning,coding,data_analysis\nexample,0,n/a,\n",
           "cost.csv": b"model,nq_reasoning\nexample,202\n",
           "categories.json": b'{"Reasoning":["reasoning"],"Coding":["coding"],"Data Analysis":["data_analysis"],"_overall_weights":{"Coding":2}}',
           "notes.json": b'["highest supported effort"]', "breakdown.json": b"{}"}
    rows = compile_livebench_v2(raw, {"bedrock.example": {"livebench_v2": "example"}}, "2026-11-06")
    row = rows["bedrock.example"][0]
    assert row["scores"] == {"Reasoning": 0}
    assert row["sample_counts"] == {"Reasoning": 202}
    assert row["metric"] == "mean_task_score_0_100"
    assert "tested_request_fields" not in row


def test_livebench_one_uses_latest_release_not_best_score_or_unverified_configuration():
    row = {"source": "livebench", "source_id": "exact-model-max", "release": "2026-06-25",
           "source_url": "https://livebench.ai/", "category_scores": {"Reasoning": 80, "Coding": None},
           "request_fields": {"reasoning": {"effort": "max"}}}
    older = {**row, "release": "2025-04-25", "category_scores": {"Reasoning": 99}}
    unsafe = {**row, "source_id": "different-model-fallback", "configuration_status": "pending"}
    evidence = compile_livebench_v1({"entries": {"example": {**row, "configurations": [older, unsafe]}}})
    assert len(evidence["example"]) == 1
    assert evidence["example"][0]["scores"] == {"Reasoning": 80}


def test_router_results_are_never_attributed_to_single_models_and_latest_failure_is_visible():
    row = {"id": "one", "workflow_id": "work", "benchmark_type": "swe_atlas_qa",
           "baseline": True, "model_permaslug": "vendor/example-v1", "accuracy": .8,
           "reasoning_effort": "high", "timestamp": "2026-11-20", "success": True,
           "total_questions": 100, "provider_name": "Provider"}
    failed = {**row, "id": "two", "timestamp": "2026-11-21", "accuracy": .2, "success": False}
    router = {**row, "id": "router", "baseline": False, "accuracy": 1}
    ensemble = {**row, "id": "ensemble", "route_models": ["vendor/example", "vendor/another"]}
    wrong = {**row, "id": "wrong", "model_permaslug": "vendor/example-v2"}
    bindings = {"bedrock.example": {"openrouter_id": "vendor/example", "canonical_slug": "vendor/example-v1"}}
    rows = compile_openrouter_runs(flight([row, failed, router, ensemble, wrong]), bindings)["bedrock.example"]
    assert len(rows) == 1
    assert rows[0]["scores"] == {"SWE Atlas QA": 20}
    assert rows[0]["status"] == "incomplete"
    assert rows[0]["sample_counts"] == {"SWE Atlas QA": 100}


def test_live_snapshot_reaches_default_candidates_without_authorizing_source_effort():
    from deployment.open_models.gateway import validate

    task = Task(text="比较产线和供应商两个解释，设计复验方案，分析统计不确定性。",
                routing_preferences={"cost_tier": "high"})
    profile = RequestProfile(workflow="reasoning", difficulty="complex",
                             stronger_model_benefit="high", latency="normal",
                             tools="no", structured_output="no", languages="zh")
    pool = build_pool(task, profile, load_catalog(), load_rankings(), exclusion)
    rows = [{**r, "workflow": "reasoning"} for r in pool["candidates"]]
    sol = next(r for r in rows if r["model"]["id"] == "openai.gpt-6.2-sol")
    assert sol["benchmark"]["request_fields"] == {}
    assert any(e["benchmark"] == "LiveBench 2" for e in sol["public_benchmarks"])
    assert not sol["public_evidence"]  # Legacy exact-configuration mapping stays distinct.
    payload = profile_payload(task)
    state = json.loads(payload["state"])
    state["task_profile"] = profile.model_dump()
    payload = {"state": json.dumps(state), "questions": {}}
    request = SimpleNamespace(state=SimpleNamespace())
    data, aliases = joint_payload(SimpleNamespace(task=task), request, payload, rows, assessed=True)
    validate(data)
    assert len(aliases) == len(rows)
    assert json.loads(data["state"])["request"] == task.text
    sol_key = next(k for k, r in aliases.items() if r["candidate_id"] == sol["candidate_id"])
    text = data["questions"]["action"]["criteria"][sol_key]
    assert "Reasoning=90.6(n=202)" in text and "Data Analysis=81.1(n=150)" in text
    assert "tested=highest_supported_effort" in text
    assert "LiveBench 2" in data["questions"]["action"]["instructions"]
    assert "does not establish equal capability" in data["questions"]["action"]["instructions"]
    # Changing the actual evidence must change what the GPU receives, not just UI metadata.
    changed = copy.deepcopy(rows)
    r = next(r for r in changed if r["candidate_id"] == sol["candidate_id"])
    e = next(e for e in r["public_benchmarks"] if e["benchmark"] == "LiveBench 2")
    e["scores"]["Reasoning"] = 12.3
    new, _ = joint_payload(SimpleNamespace(task=task), request, payload, changed, assessed=True)
    assert "Reasoning=12.3" in new["questions"]["action"]["criteria"][sol_key]
    assert new != data


def test_agent_metrics_follow_agent_context_and_never_become_bedrock_protocol_proof():
    snapshot = load_public_benchmarks()
    assert digest(snapshot) == snapshot["version"]
    ordinary = evidence_for("openai.gpt-6.2-sol", "reasoning")
    agent = evidence_for("openai.gpt-6.2-sol", "reasoning", agent=True)
    assert not any("Long Agentic" in e["scores"] for e in ordinary)
    assert any("Long Agentic" in e["scores"] for e in agent)
    assert not evidence_for("unknown-model", "coding")
    assert not evidence_for("openai.gpt-6.2-sol", "speech")
    assert all(e["scope"] == "external_same_model_not_current_Bedrock_configuration" for e in agent)
