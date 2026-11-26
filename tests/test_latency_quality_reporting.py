import hashlib

import pytest

from scripts.run_profile_quality import save
from scripts.summarize_latency_quality import summarize


def records(tmp_path, *, status="completed", workflow_fit=True, text="answer", review=None):
    save(tmp_path / "manifest.json", {
        "arm": "optimized", "version": "frozen", "industries": [],
        "cases": [{"id": "one", "split": "regression"}, {"id": "untested"}],
    })
    save(tmp_path / "base" / "one.json.gz", {
        "status": status, "workflow_fit": workflow_fit,
        "grade": {"status": "pending"}, "output": {"text": text},
    })
    if review:
        save(tmp_path / "reviews.json", [{
            "case_id": "one", "output_sha256": hashlib.sha256(text.encode()).hexdigest(), **review,
        }])


def test_format_failures_and_unmeasured_items_remain_in_overall_denominator(tmp_path):
    records(tmp_path, review={"status": "fail", "content_status": "pass"})
    result = summarize(tmp_path)
    assert result["overall"]["planned"] == 2
    assert result["overall"]["passed"] == 0
    assert result["overall"]["content_passed"] == 1
    assert result["overall"]["status_counts"] == {"fail": 1, "unmeasured": 1}


@pytest.mark.parametrize("status,fit", [("incomplete", True), ("completed", False)])
def test_content_review_cannot_hide_incomplete_execution_or_wrong_workflow(tmp_path, status, fit):
    records(tmp_path, status=status, workflow_fit=fit, review={"status": "pass"})
    assert summarize(tmp_path)["overall"]["passed"] == 0


def test_reviews_cannot_be_reused_for_a_different_target_output(tmp_path):
    records(tmp_path, review={"status": "pass", "output_sha256": "wrong"})
    with pytest.raises(ValueError, match="different output"):
        summarize(tmp_path)
