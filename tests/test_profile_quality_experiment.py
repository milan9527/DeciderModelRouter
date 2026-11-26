import copy

import pytest

from scripts.profile_quality_cases import catalog
from scripts.review_profile_quality import parse


def test_suite_preserves_original_denominator_and_preregisters_extensions():
    data = catalog()
    assert len(data["base_cases"]) == 87
    assert len(data["additional_cases"]) == 8
    assert sum(len(s["turns"]) for s in data["industry_scenarios"]) == 48
    assert data["max_output_tokens"] == 16384
    cases = {c["id"]: c for c in data["base_cases"]}
    assert cases["web-search"]["gap"]
    assert cases["action-tool"]["gap"]
    assert not cases["multi-turn"]["gap"]
    assert not cases["multi-image"]["gap"]
    assert not cases["retrieval-quality"]["gap"]
    assert cases["multi-turn"]["history"][0]["role"] == "user"
    assert all(c["expected_boundary_statuses"] is None
               for c in data["base_cases"] if c["id"] in {"tts-zh", "image-generate", "video-generate"})


def test_catalog_generation_does_not_mutate_existing_production_suite():
    from app.gateway_effect_cases import suite
    from app.industry_scenarios import SCENARIOS

    old, industries = copy.deepcopy(suite()), copy.deepcopy(SCENARIOS)
    catalog()
    assert old == suite()
    assert industries == SCENARIOS


def test_low_confidence_review_cannot_become_a_confirmed_pass():
    assert parse('{"status":"pass","reason":"uncertain","issues":[],"confidence":"low"}')["status"] == "pending"


def test_issue_annotation_can_be_preserved_without_inventing_json_repairs():
    import json

    raw = '[{"claim": "contains "unescaped" quotes"}]'
    result = parse(json.dumps({"status": "fail", "reason": "concrete issue", "issues": raw}))
    assert result["issues_raw"] == raw
    assert result["issues"] == []
    assert result["status"] == "fail"


@pytest.mark.parametrize("text", [
    '{"status":"ok","reason":"done","issues":[]}',
    '{"status":"pass","issues":[]}',
    'not JSON',
])
def test_invalid_judgments_fail_explicitly(text):
    with pytest.raises(ValueError):
        parse(text)
