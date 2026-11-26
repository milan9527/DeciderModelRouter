import math

import pytest

from app.decision_uncertainty import choice_answer, distribution


def test_full_distribution_and_close_margin_survive_alias_mapping_without_changing_choice():
    raw = {"choice": "a", "confidence": 0.99,
           "probabilities": {"a": 0.34, "b": 0.335, "c": 0.325}}
    answer = choice_answer(raw, {"a": "A", "b": "B", "c": "C"})
    result = distribution(answer, {k: {"candidate_id": "id-" + k} for k in "abc"})
    assert answer["choice"] == "a"
    assert result["selected_probability"] == 0.34  # Computed from the distribution, not a stale field.
    assert result["margin"] == pytest.approx(0.005)
    assert result["probabilities"] == {"id-a": 0.34, "id-b": 0.335, "id-c": 0.325}
    assert result["top_candidates"][0]["candidate_id"] == "id-a"


@pytest.mark.parametrize("probabilities", [
    {"a": math.nan, "b": 0.3}, {"a": math.inf, "b": 0.3},
    {"a": True, "b": 0}, {"a": -0.1, "b": 1.1},
    {"a": 0.8, "b": 0.8}, {"a": 0, "b": 0},
    {"a": 0.5, "foreign": 0.5}, {"a": 1}, {"a": 0.2, "b": 0.8}, [0.7, 0.3],
])
def test_invalid_optional_distribution_never_changes_a_valid_gpu_choice(probabilities):
    answer = choice_answer({"choice": "a", "probabilities": probabilities}, {"a": "A", "b": "B"})
    assert answer == {"choice": "a", "distribution_status": "invalid"}
    assert distribution(answer, {})["status"] == "invalid"


def test_missing_probabilities_are_unknown_and_zero_is_preserved():
    assert choice_answer({"choice": "a"}, {"a": "A"})["distribution_status"] == "unreported"
    answer = choice_answer({"choice": "a", "probabilities": {"a": 1, "b": 0}}, {"a": "A", "b": "B"})
    assert distribution(answer, {"a": {"candidate_id": "id"}})["runner_up_probability"] == 0


def test_rounded_provider_probabilities_are_preserved_not_renormalized():
    criteria = {str(i): "" for i in range(106)}
    values = {k: 0.0094 for k in criteria}
    result = choice_answer({"choice": "0", "probabilities": values}, criteria)
    assert result["distribution_status"] == "reported"
    assert result["probabilities"] == values
    assert result["probability_sum"] == pytest.approx(0.9964)
