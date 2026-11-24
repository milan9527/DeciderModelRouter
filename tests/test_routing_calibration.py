import copy

import pytest

from app.routing import Task, exclusion, load_catalog, load_rankings
from app.routing_balance import RoutingPreferences
from app.routing_calibration import build_calibration, verify_snapshot, wilson_lower
from app.routing_profile import RequestProfile
from app.routing_selector import choose, configurations


def fixture():
    catalog, ranks = load_catalog(), load_rankings()
    task = Task(text="x")
    profile = RequestProfile(
        workflow="coding",
        difficulty="moderate",
        stronger_model_benefit="medium",
        latency="normal",
        tools="no",
        structured_output="no",
        languages="zh",
    )
    pool = configurations(catalog, ranks, task, profile.workflow, exclusion)
    ids = [row["candidate_id"] for row in pool["candidates"][:2]]
    known = {cid: {"workflows": ["coding"]} for cid in ids}
    observations = []
    for split in ("calibration", "validation"):
        for case in range(2):
            for idx, cid in enumerate(ids):
                observations.append(
                    {
                        "case_id": f"{split}-{case}",
                        "split": split,
                        "configuration_id": cid,
                        "profile": profile.model_dump(),
                        "input_tokens": 1000,
                        "max_output_tokens": 4096,
                        "passed": idx == 1 or case == 0,
                        "latency_ms": 100 if idx == 0 else 200,
                        "total_cost_usd": "0.001" if idx == 0 else "0.005",
                        "cost_scope": "reference_usd",
                    }
                )
    return catalog, ranks, task, profile, pool, ids, known, observations


def build(observations=None):
    catalog, ranks, _, _, _, _, known, default = fixture()
    return build_calibration(
        default if observations is None else observations,
        known,
        ranks,
        catalog_version=catalog["version"],
        minimum_samples=2,
    )


def test_business_evidence_replaces_benchmark_proxy_and_enforces_quality_and_latency():
    _, ranks, task, profile, pool, ids, _, _ = fixture()
    calibration = build()
    assert verify_snapshot(calibration) == calibration
    assert wilson_lower(2, 2) == pytest.approx(0.34237, abs=0.00001)
    task.routing_preferences = RoutingPreferences(cost_tier="low")
    result = choose(task, profile, copy.deepcopy(pool), ranks, calibration)
    assert result["selected_candidate_id"] == ids[0]
    assert result["evidence_mode"] == "business_validation"
    task.routing_preferences.min_success_rate = 0.3
    assert (
        choose(task, profile, copy.deepcopy(pool), ranks, calibration)["selected_candidate_id"]
        == ids[1]
    )
    task.routing_preferences.max_latency_ms = 150
    assert (
        choose(task, profile, copy.deepcopy(pool), ranks, calibration)["selected_candidate_id"]
        is None
    )


def test_heldout_results_are_reported_without_tuning_on_them():
    *_, observations = fixture()
    original = build(observations)
    for row in observations:
        if row["split"] == "validation":
            row["passed"] = False
            row["total_cost_usd"] = "100"
    changed = build(observations)
    key = next(iter(original["cohorts"]))
    for cid, before in original["cohorts"][key]["configurations"].items():
        after = changed["cohorts"][key]["configurations"][cid]
        assert before["success_lower_bound"] == after["success_lower_bound"]
        assert before["mean_cost_usd"] == after["mean_cost_usd"]
        assert after["heldout"]["success_rate"] == 0


@pytest.mark.parametrize(
    "change", ["duplicates", "leakage", "coverage", "cost_scope", "unknown_model", "profile", "few"]
)
def test_biased_or_invalid_observations_cannot_be_published(change):
    *_, observations = fixture()
    if change == "duplicates":
        observations.append(copy.deepcopy(observations[0]))
    elif change == "leakage":
        # Leak one task across splits using a different configuration.
        observations[-1]["case_id"] = observations[0]["case_id"]
        observations = [r for i, r in enumerate(observations) if i != 1]
    elif change == "coverage":
        observations.pop()
    elif change == "cost_scope":
        observations[0]["cost_scope"] = "actual_usd"
    elif change == "unknown_model":
        observations[0]["configuration_id"] = "f" * 24
    elif change == "profile":
        observations[0]["profile"]["difficulty"] = "complex"
    else:
        observations = observations[:4]
    with pytest.raises(ValueError):
        build(observations)


def test_unmatched_versions_expired_evidence_and_changed_output_cap_do_not_claim_calibration():
    _, ranks, task, profile, pool, _, _, _ = fixture()
    task.routing_preferences = RoutingPreferences(min_success_rate=0.1)
    for field, value in (
        ("ranking_version", "other"),
        ("catalog_version", "other"),
        ("expires_at", 0),
    ):
        calibration = build()
        calibration[field] = value
        result = choose(task, profile, copy.deepcopy(pool), ranks, calibration)
        assert result["evidence_mode"] == "benchmark_proxy"
        assert result["selected_candidate_id"] is None
    task.routing_preferences.max_output_tokens = 2048
    result = choose(task, profile, copy.deepcopy(pool), ranks, build())
    assert result["selected_candidate_id"] is None


def test_checksum_prevents_silent_business_score_edits():
    data = build()
    key = next(iter(data["cohorts"]))
    cid = next(iter(data["cohorts"][key]["configurations"]))
    data["cohorts"][key]["configurations"][cid]["success_lower_bound"] = 1
    with pytest.raises(ValueError, match="checksum"):
        verify_snapshot(data)


def test_empty_data_cannot_create_a_calibrated_policy():
    with pytest.raises(ValueError):
        build([])
