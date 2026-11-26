import asyncio
import json
from types import SimpleNamespace

import pytest

from app.bedrock_evidence import load_evidence
from app.gateway_decision import GatewayDecisionService
from app.routing import Task, load_catalog, load_rankings
from scripts.paired_quality_cases import cases
from scripts.profile_quality_cases import catalog
from scripts.review_paired_chinese_audio import cleanup_prefix
from scripts.review_paired_quality import anonymous, review_tool
from scripts.run_paired_quality import ARMS, PairedEvaluation, ordered_arms
from scripts.run_profile_quality import Evaluation, save
from scripts.summarize_paired_quality import build


def evaluator(tmp_path, arm, profile):
    ev = object.__new__(PairedEvaluation)
    ev.arm, ev.router_lock = arm, asyncio.Lock()
    ev.args = SimpleNamespace(output=tmp_path)
    ev.catalog, ev.rankings, ev.evidence = load_catalog(), load_rankings(), load_evidence()
    ev.state = SimpleNamespace()
    ev.decision = GatewayDecisionService(None)

    async def fake(body, request, payload, **kwargs):
        answers = {k: {"choice": profile[k]} for k in payload["questions"] if k != "action"}
        if "action" in payload["questions"]:
            answers["action"] = {"choice": next(iter(payload["questions"]["action"]["criteria"]))}
        return {"model": "fixture", "answers": answers, "usage": None,
                "timings": {"forward_passes": 1, "service_reported_ms": 1}}

    ev.decision.infer = fake
    return ev


PROFILE = {
    "workflow": "coding", "difficulty": "complex", "stronger_model_benefit": "high",
    "latency": "normal", "tools": "no", "structured_output": "no", "languages": "zh",
}


def test_second_stage_reads_actual_first_stage_and_all_eligible_candidates(tmp_path):
    ev = evaluator(tmp_path, "two_stage", PROFILE)
    task = Task(text="实现Python函数")
    runtime = Evaluation.runtime(ev, task, [])
    record, selected, *_ = asyncio.run(ev.route(task, runtime))
    assert selected
    first, second = record["gpu_calls"]
    assert len(first["payload"]["questions"]) == 7
    assert set(second["payload"]["questions"]) == {"action"}
    assert json.loads(second["payload"]["state"])["task_profile"] == PROFILE
    assert len(second["payload"]["questions"]["action"]["criteria"]) == record["selection"]["candidate_count"]
    assert record["selection"]["candidate_count"] > 60


def test_empty_pool_never_calls_second_gpu_or_forces_a_winner(tmp_path):
    ev = evaluator(tmp_path, "two_stage", PROFILE)
    task = Task(text="写代码", routing_preferences={"max_reference_cost_usd": "0"})
    runtime = Evaluation.runtime(ev, task, [])
    record, selected, *_ = asyncio.run(ev.route(task, runtime))
    assert selected is None
    assert len(record["gpu_calls"]) == 1


def test_joint_uses_one_real_assessment_function_and_does_not_cpu_substitute(tmp_path):
    ev = evaluator(tmp_path, "joint", {**PROFILE, "structured_output": "yes"})
    task = Task(text="代码")
    runtime = Evaluation.runtime(ev, task, [])
    record, selected, *_ = asyncio.run(ev.route(task, runtime))
    assert len(record["gpu_calls"]) == 1
    assert len(record["gpu_calls"][0]["payload"]["questions"]) == 8
    assert selected is None
    assert record["selection"]["decision_rejection"] == "incompatible_joint_answers"


def test_rotating_order_and_new_cases_have_no_target_model_override():
    assert {ordered_arms(i)[0] for i in range(3)} == set(ARMS)
    assert len(cases()) == len({c["id"] for c in cases()}) == 16
    for row in cases():
        Task(**row["task"])
        assert not row["task"].get("routing_preferences")


def test_blind_review_hides_arm_labels_and_flat_fields_preserve_mapping():
    data, mapping = anonymous("case", {"task": "Question"}, {"joint": "A", "two_stage": "B"})
    assert "joint" not in json.dumps(data) and "two_stage" not in json.dumps(data)
    assert {item["text"] for item in data["answers"]} == {"A", "B"}
    schema = review_tool(mapping)["toolSpec"]["inputSchema"]["json"]
    assert len(schema["required"]) == 10
    assert all(value["type"] == "string" for value in schema["properties"].values())
    assert anonymous("case", {"task": "Question"}, {"two_stage": "B", "joint": "A"}) == (data, mapping)


def test_summary_preserves_missing_cases_code_format_and_failed_gpu_attempts(tmp_path):
    suite = {**catalog(), "holdout_cases": cases()}
    save(tmp_path / "manifest.json", {})
    for arm in ARMS:
        save(tmp_path / arm / "manifest.json", suite)
    save(tmp_path / "joint" / "base" / "greeting.json.gz", {"status": "completed",
         "grade": {"status": "pending"}})
    save(tmp_path / "joint" / "base" / "python-dedup.json.gz", {"status": "completed",
         "grade": {"status": "pending"}})
    save(tmp_path / "joint" / "code-checks" / "python-dedup.json",
         {"status": "fail", "functional_pass": True, "bare_code_format": False})
    save(tmp_path / "joint" / "route-errors" / "failed.json.gz", {
        "route_ms": 90, "experiment_queue_ms": 12,
        "gpu_calls": [{"number": 1, "status": "error", "error": "timeout"}]})
    save(tmp_path / "assistant-audits.json",
         {"joint:base-greeting": {"status": "pass", "reason": "source audit"}})
    result = build(tmp_path)
    assert result["arms"]["joint"]["all159"] == {
        "planned": 159, "passed": 1, "failed": 1, "pending": 0, "unmeasured": 157,
        "pass_rate": 1 / 159, "completed": 2}
    assert result["arms"]["joint"]["gpu_attempts"] == 1
    assert result["arms"]["joint"]["gpu_completed"] == 0
    assert result["arms"]["joint"]["route_p50_ms"] == 90
    assert result["pairwise"]["joint_vs_cpu_policy"]["counts"] == {"pending_or_unmeasured": 159}


def test_audio_cleanup_rejects_shared_prefix_and_removes_versioned_test_objects():
    with pytest.raises(ValueError):
        cleanup_prefix(None, "bucket", "temporary-evaluation/")
    prefix = "temporary-evaluation/paired-audio-" + "a" * 32 + "/"
    removed = []

    class FakeS3:
        def get_paginator(self, name):
            assert name == "list_object_versions"
            return self

        def paginate(self, **kwargs):
            assert kwargs["Prefix"] == prefix
            yield {"Versions": [{"Key": prefix + "audio.wav", "VersionId": "v1"},
                                {"Key": prefix + ".write_access_check_file.temp", "VersionId": "v2"}],
                   "DeleteMarkers": [{"Key": prefix + "audio.wav", "VersionId": "d1"}]}

        def delete_objects(self, **kwargs):
            removed.extend(kwargs["Delete"]["Objects"])
            return {}

    result = cleanup_prefix(FakeS3(), "bucket", prefix)
    assert result == {"versions_and_markers_deleted": 3, "errors": []}
    assert {row["VersionId"] for row in removed} == {"v1", "v2", "d1"}


def test_explicit_industry_constraints_override_a_generous_llm_grade(tmp_path):
    suite = {**catalog(), "holdout_cases": cases()}
    save(tmp_path / "manifest.json", {})
    for arm in ARMS:
        save(tmp_path / arm / "manifest.json", suite)
    for cid in ("saas", "retail"):
        save(tmp_path / "paired-reviews" / f"industry-{cid}-4.json.gz", {
            "status": "completed", "method": "fixture",
            "reviews": {"joint": {"status": "pass", "reason": "fixture"}}})
    save(tmp_path / "joint" / "industries" / "saas.json.gz",
         {"turns": [{"number": 4, "status": "completed", "answer": "字" * 101}]})
    route = {"selection": {"candidate_count": 0}, "gpu_calls": [], "route_ms": 1,
             "experiment_queue_ms": 0}
    save(tmp_path / "joint" / "industries" / "retail.json.gz", {"turns": [{
        "number": 4, "status": "completed", "answer": "Three instructions", "steps": [{
            "route": route, "output": {"backend_total_ms": 1, "tool_calls": [
                {"name": "read_policy", "input": {}}]},
            "tool_results": [{"toolResult": {"status": "success"}}],
        }],
    }]})
    rows = {row["id"]: row for row in build(tmp_path)["arms"]["joint"]["results"]}
    assert rows["industry-saas-4"]["outcome"]["method"] == "deterministic_han_count"
    assert rows["industry-retail-4"]["outcome"]["method"] == "native_tool_trace"
    assert rows["industry-retail-4"]["outcome"]["status"] == "fail"
