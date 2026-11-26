"""Behavioral controls for the experimental CPU policy, not model-quality scores."""

import copy

import pytest

from scripts.profile_policy import choose

PROFILE = {
    "workflow": "coding", "difficulty": "moderate", "stronger_model_benefit": "medium",
    "latency": "normal", "tools": "no", "structured_output": "no", "languages": "en",
}


def candidate(cid, passed=99, n=100, cost="0.001", latency=100):
    return {
        "candidate_id": cid, "model": {"id": cid},
        "measured_evidence": {
            "suite": "control-v1", "samples": n, "content_passed": passed,
            "content_scored": n, "content_pending": 0, "p95_ms": latency,
        },
        "cost_evidence": {"expected_usd": cost},
        "benchmark": {"request_fields": {}, "inference_config": {}},
        "public_evidence": [],
    }


def selected(rows, **kwargs):
    return choose(PROFILE, rows, **kwargs)["selected_candidate_id"]


@pytest.mark.parametrize("winner", ["m097", "m007"])
def test_quality_evidence_swap_among_128(winner):
    rows = [candidate(f"m{i:03}", passed=99 if f"m{i:03}" == winner else 20) for i in range(128)]
    assert selected(rows) == winner
    assert selected(list(reversed(rows))) == winner
    assert len(choose(PROFILE, rows)["audit"]) == 128


@pytest.mark.parametrize("winner", ["m097", "m007"])
def test_price_swap_among_128(winner):
    rows = [candidate(f"m{i:03}", cost="0.001" if f"m{i:03}" == winner else "0.1") for i in range(128)]
    assert selected(rows) == winner


def test_sample_uncertainty_one_of_one_does_not_beat_99_of_100():
    assert selected([candidate("one", 1, 1, "0.00001"), candidate("many")]) == "many"


def test_pending_and_errors_remain_in_denominator_without_relabeling():
    row = candidate("pending", 1, 100, "0.00001")
    row["measured_evidence"].update(content_scored=1, content_pending=98, call_errors=1)
    assert selected([row, candidate("supported", 80)]) == "supported"
    assert row["measured_evidence"]["content_pending"] == 98


def test_unknown_price_not_free():
    assert selected([candidate("unknown", cost=None), candidate("known")]) == "known"


def test_unknown_measurement_not_invented():
    row = candidate("unknown", cost="0.00001")
    row["measured_evidence"] = {}
    result = choose(PROFILE, [row, candidate("supported")])
    assert result["selected_candidate_id"] == "supported"
    assert result["audit"][0]["development_lower_bound"] is None


def test_unknown_only_fallback_is_explicit():
    rows = [candidate("z"), candidate("a", cost="0.01")]
    for row in rows:
        row["measured_evidence"] = {}
    result = choose(PROFILE, rows)
    assert result["selected_candidate_id"] == "z"
    assert all(r["development_lower_bound"] is None for r in result["audit"])
    assert result["calibration_status"] == "uncalibrated_development_heuristic"


def test_complex_profile_changes_quality_cost_tradeoff():
    rows = [candidate("cheap", 85, cost="0.001"), candidate("strong", 99, cost="0.1")]
    simple = {**PROFILE, "difficulty": "simple", "stronger_model_benefit": "low"}
    hard = {**PROFILE, "difficulty": "complex"}
    assert choose(simple, rows)["selected_candidate_id"] == "cheap"
    assert choose(hard, rows)["selected_candidate_id"] == "strong"


def test_explicit_tier_overrides_inferred_preference():
    rows = [candidate("cheap", 85), candidate("strong", 99, cost="0.1")]
    assert selected(rows, cost_tier="low") == "cheap"
    assert selected(rows, cost_tier="high") == "strong"


def test_latency_sensitive_changes_choice_without_treating_unknown_as_fast():
    rows = [candidate("cheap", latency=1000), candidate("fast", cost="0.1", latency=50),
            candidate("unknown", cost="0", latency=None)]
    result = choose({**PROFILE, "latency": "sensitive"}, rows)
    assert result["selected_candidate_id"] == "fast"


def test_previous_configuration_only_within_quality_and_cost_window():
    rows = [candidate("new"), candidate("previous", cost="0.00104")]
    assert selected(rows, previous_candidate_id="previous") == "previous"
    rows[1]["cost_evidence"]["expected_usd"] = "0.1"
    assert selected(rows, previous_candidate_id="previous") == "new"
    rows[1] = candidate("previous", passed=20, cost="0.00001")
    assert selected(rows, previous_candidate_id="previous") == "new"


def test_public_scores_only_break_ties_with_identical_cohort():
    rows = [candidate("a"), candidate("b")]
    for row, value in zip(rows, [20, 99], strict=True):
        row["public_evidence"] = [{"source": "example", "release": "2026-01-01", "scores": {"coding": value}}]
    assert selected(rows) == "b"
    rows[1]["public_evidence"][0]["release"] = "2026-02-01"
    assert selected(rows) == "a"  # Stable identifier tie, not cross-release raw score.
    assert len(choose(PROFILE, rows)["audit"]) == 2


def test_public_tie_break_does_not_override_explicit_latency_preference():
    rows = [candidate("fast", latency=50), candidate("slow", latency=1000)]
    for row, score in zip(rows, [80, 99], strict=True):
        row["public_evidence"] = [{"source": "example", "release": "2026-01-01",
                                  "scores": {"coding": score}}]
    assert choose({**PROFILE, "latency": "sensitive"}, rows)["selected_candidate_id"] == "fast"


def test_exact_reasoning_configuration_is_returned_without_inheriting_measurements():
    row = candidate("reasoning")
    row["benchmark"]["request_fields"] = {"reasoning": {"effort": "high"}}
    result = choose(PROFILE, [row])
    assert result["selected_configuration"]["request_fields"] == {"reasoning": {"effort": "high"}}
    assert not result["cache_discount_used"]


def test_policy_does_not_mutate_snapshot():
    rows = [candidate("a"), candidate("b")]
    before = copy.deepcopy(rows)
    choose(PROFILE, rows)
    assert before == rows


def test_mixed_measurement_suites_fail_explicitly():
    rows = [candidate("a"), candidate("b")]
    rows[1]["measured_evidence"]["suite"] = "different-suite"
    with pytest.raises(ValueError, match="shared"):
        choose(PROFILE, rows)


def test_empty_pool_and_duplicate_ids():
    assert choose(PROFILE, [])["selected_candidate_id"] is None
    with pytest.raises(ValueError, match="Duplicate"):
        choose(PROFILE, [candidate("a"), candidate("a")])


@pytest.mark.parametrize("value", ["NaN", "-1", "Infinity", True])
def test_invalid_cost_is_unknown(value):
    result = choose(PROFILE, [candidate("bad", cost=value), candidate("valid")])
    assert result["selected_candidate_id"] == "valid"
    assert result["audit"][0]["expected_usd"] is None


@pytest.mark.parametrize("requirement", ["tools", "structured_output", "vision", "explicit_exclusion"])
def test_actual_bedrock_pool_applies_constraints_before_cpu_choice(requirement):
    from app.gateway_model_pool import build_pool
    from app.routing import Task, exclusion, load_catalog, load_rankings
    from app.routing_profile import RequestProfile

    task = Task(text="Describe the supplied information.")
    profile = RequestProfile(**PROFILE)
    if requirement in {"tools", "structured_output"}:
        profile = profile.model_copy(update={requirement: "yes"})
    elif requirement == "vision":
        task = Task(text="Describe the supplied image.",
                    attachments=[{"id": "0" * 32, "kind": "image"}])
        profile = profile.model_copy(update={"workflow": "vision"})
    else:
        task = Task(text="Describe the information.", routing_preferences={
            "allowed_models": ["amazon.nova-micro-v1:0"],
            "excluded_models": ["amazon.nova-micro-v1:0"],
        })
    pool = build_pool(task, profile, load_catalog(), load_rankings(), exclusion)
    result = choose(profile.model_dump(), pool["candidates"])
    assert result["candidate_count"] == len(pool["candidates"])
    if requirement == "explicit_exclusion" or (
        requirement == "structured_output" and not pool["candidates"]
    ):
        assert result["selected_candidate_id"] is None
        assert pool["excluded"]
    else:
        assert result["selected_candidate_id"] in {r["candidate_id"] for r in pool["candidates"]}
        winner = next(r for r in pool["candidates"]
                      if r["candidate_id"] == result["selected_candidate_id"])
        assert winner["model"]["routing_capabilities"][requirement] is True
