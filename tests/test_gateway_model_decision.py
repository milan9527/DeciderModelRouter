import asyncio
import copy
import json
from types import SimpleNamespace

from app.bedrock_evidence import load_evidence, measured
from app.gateway_model_decision import select_all
from app.gateway_joint_decision import all_candidates, joint_payload
from app.gateway_model_pool import build_pool
from app.routing import Task, exclusion, load_catalog, load_rankings
from app.routing_profile import replay_profile
from scripts.compile_bedrock_evidence import normalize_answer, price


def pool_for(task=None):
    task = task or Task(text="写一个 Python 排序函数")
    return build_pool(task, replay_profile(task, "coding"), load_catalog(), load_rankings(), exclusion)


def test_every_compatible_default_configuration_reaches_pool_including_new_unmapped_models():
    pool = pool_for()
    by_id = {r["model"]["id"]: r for r in pool["candidates"]}
    assert "openai.gpt-6.2-sol" in by_id
    assert by_id["openai.gpt-6.2-sol"]["measured_evidence"]["samples"] >= 1
    assert not by_id["openai.gpt-6.2-sol"]["public_evidence"]
    assert len(by_id) > 60
    assert pool["filter_summary"]["benchmark_cohort_exclusion"] is False
    assert pool["filter_summary"]["quality_top_k_filter"] is False
    assert pool["selected_candidate_id"] is None
    assert len({r["public_evidence"][0]["release"] for r in pool["candidates"]
                if r["public_evidence"]}) > 1


def test_measured_scores_are_bound_to_exact_endpoint_and_configuration():
    evidence = load_evidence()
    row = next(r for r in pool_for()["candidates"] if r["model"]["id"] == "amazon.nova-micro-v1:0")
    assert measured(row["model"], evidence)
    assert not measured({**row["model"], "invoke_id": "another-endpoint"}, evidence)
    for candidate in pool_for()["candidates"]:
        if candidate["benchmark"]["request_fields"]:
            assert candidate["measured_evidence"] == {}
            assert candidate["measurement_configuration"] is None


def test_joint_request_keeps_all_configurations_evidence_and_complete_current_request():
    from app.routing_profile import profile_payload
    from deployment.open_models.gateway import validate
    task = Task(text=("处理订单的并发状态变更，提供代码与测试。" * 100)[:1400])
    body = SimpleNamespace(task=task, provider="liquid")
    request = SimpleNamespace(state=SimpleNamespace())
    rows, _ = all_candidates(body, request, load_catalog(), load_rankings(), exclusion)
    data, aliases = joint_payload(body, request, profile_payload(task), rows)
    validate(data)
    assert json.loads(data["state"])["request"] == task.text
    assert len(data["questions"]) == 8
    assert len(aliases) > 100
    assert {r["candidate_id"] for r in pool_for(task)["candidates"]} <= {
        r["candidate_id"] for r in aliases.values()}
    for key, row in aliases.items():
        original = row["workflow_evidence"].get("coding", {}).get("measured")
        if original:
            assert f"Coding correctness {original['content_passed']}/{original['content_scored']}" in data["questions"]["action"]["criteria"][key]


def test_output_limits_use_exact_endpoint_and_never_raise_caller_budget():
    from app.bedrock_limits import output_limit
    from app.gateway_adapter import request_args

    model = next(m for m in load_catalog()["models"] if m["id"] == "meta.llama4-scout-17b-instruct-v1:0")
    assert output_limit(model, 16384) == 8192
    assert output_limit(model, 1024) == 1024
    assert output_limit({**model, "invoke_id": "different-endpoint"}, 16384) == 16384
    runtime = SimpleNamespace(messages=[{"role": "user", "text": "hi"}],
                              options=SimpleNamespace(prompt_cache=False))
    task = Task(text="hi", routing_preferences={"max_output_tokens": 16384})
    args, _ = request_args({"model": model, "benchmark": {}}, task, runtime, {})
    assert args["inferenceConfig"]["maxTokens"] == 8192


def test_single_gpu_call_sees_every_candidate_and_changes_winner_with_evidence():
    base = pool_for()
    sample = base["candidates"][0]
    rows = [{**copy.deepcopy(sample), "candidate_id": f"candidate-{i:02}",
             "model": {**sample["model"], "name": f"Model {i}"},
             "measured_evidence": {"samples": 50, "content_passed": i + 1, "content_scored": 50}}
            for i in range(37)]
    body = SimpleNamespace(task=Task(text="Implement correct Python"), provider="liquid")
    runtime = SimpleNamespace(options=SimpleNamespace(stickiness=True),
                              context={"history_turns": 1},
                              previous={"configuration_id": "candidate-00"}, messages=[],
                              classify=lambda payload: payload)
    request = SimpleNamespace(state=SimpleNamespace(gateway_runtime=runtime))

    class Service:
        async def infer(self, body, request, payload, *, selection=False):
            assert selection
            criteria = payload["questions"]["action"]["criteria"]
            import re
            cards = {key: value for key, value in criteria.items() if key != "none"}
            assert len(cards) == 37
            key = max(cards, key=lambda k: int(re.search(r"Coding correctness (\d+)/", cards[k])[1]))
            return {"answers": {"action": {"choice": key}}, "model": "test-gpu",
                    "timings": {"backend_total_ms": 1}, "usage": None}

    async def check():
        first = await select_all(Service(), body, request, {**base, "candidates": rows})
        assert first["selected_candidate_id"] == "candidate-36"
        assert first["stickiness"]["retained"] is False
        seen = {cid for call in first["decision_calls"] for cid in call["candidate_ids"]}
        assert seen == {r["candidate_id"] for r in rows}
        assert first["evaluated_configurations"] == 37
        assert len(first["decision_calls"]) == 1
        rows[0]["measured_evidence"]["content_passed"] = 50
        second = await select_all(Service(), body, request, {**base, "candidates": rows})
        assert second["selected_candidate_id"] == "candidate-00"
    asyncio.run(check())


def test_agent_pool_uses_observed_portable_protocol_instead_of_fixed_allowlist():
    from app.agent_model import TOOL_MODELS, AgentConversation
    from app.gateway_conversation import SessionOptions

    runtime = object.__new__(AgentConversation)
    runtime.agent_tools = True
    runtime.options, runtime.health = SessionOptions(), {}
    runtime.messages = [{"role": "user", "text": "Use the supplied tool"}]
    runtime.context = {"text_bytes": 30}
    task = Task(text="Read inventory with a tool")
    profile = replay_profile(task, "general_text")
    pool = build_pool(task, profile, load_catalog(), load_rankings(), exclusion, runtime)
    ids = {r["model"]["id"] for r in pool["candidates"]}
    assert ids - set(TOOL_MODELS)
    assert all(r["model"]["routing_capabilities"]["tools"] is True for r in pool["candidates"])
    assert all(not r["benchmark"]["request_fields"] for r in pool["candidates"])


def test_explicit_constraints_still_exclude_models_before_gpu_calls():
    task = Task(text="code", routing_preferences={"allowed_models": ["amazon.nova-micro-v1:0"],
                                                "excluded_models": ["amazon.nova-micro-v1:0"]})
    assert not pool_for(task)["candidates"]
    task = Task(text="code", routing_preferences={"max_reference_cost_usd": "0"})
    assert not pool_for(task)["candidates"]


def test_normalization_does_not_guess_answers_from_explanations():
    assert normalize_answer("The answer may be 42 or 43", "number") is None
    assert normalize_answer("Explanation\n42", "number") == "42"
    assert normalize_answer("```python\nreturn 3\n```", "code") == "return 3"
    assert normalize_answer("```python\nx=3\n```\n```python\nx=4\n```", "code") is None


def test_price_requires_matching_endpoint_scope_and_unambiguous_units():
    pricing = {"products": {}, "terms": {"OnDemand": {}}, "publicationDate": "2026-11-26"}
    for kind, rate in [("input", "0.0001"), ("output", "0.0004")]:
        pricing["products"][kind] = {"attributes": {"feature": "On-demand Inference",
            "model": "Example", "inferenceType": kind + " tokens", "usagetype": "USE1-Example-" + kind}}
        pricing["terms"]["OnDemand"][kind] = {"term": {"priceDimensions": {"dim": {
            "unit": "1K tokens", "beginRange": "0", "endRange": "Inf", "pricePerUnit": {"USD": rate}}}}}
    model = {"name": "Example", "invoke_id": "us.example"}
    result = price(model, pricing)
    assert result["input_price"] == 0.1 and result["output_price"] == 0.4
    pricing["products"]["input"]["attributes"]["usagetype"] += "-mantle"
    assert price(model, pricing) is None
