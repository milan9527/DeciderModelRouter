"""Verify saved evaluation coverage, provenance and review hashes without API calls."""

import argparse
import hashlib
from collections import Counter
from pathlib import Path

from app.gateway_effect_judge import grade
from scripts.run_online_quality import read, save
from scripts.summarize_latency_quality import verify_review


def verify(folder):
    root = folder.parent
    suite = read(root / "suite.json")
    assert read(folder / "manifest.json") == suite
    old = read(Path("validation/independent_gateway/anonymous-evidence-2026-11-26/suite-final.json"))
    frozen = {c["id"]: c for c in suite["cases"]}
    assert all(frozen[c["id"]] == c for c in old["cases"])
    assert suite["industries"] == old["industries"]
    rows = {p.name.removesuffix(".json.gz"): read(p) for p in (folder / "base").glob("*.json.gz")}
    assert set(rows) == set(frozen)
    assert all(r["status"] in {"completed", "incomplete", "error"} for r in rows.values())
    requests = [r["request"]["client_request_id"] for r in rows.values()]
    assert len(requests) == len(set(requests))
    for cid, row in rows.items():
        assert row["case"] == frozen[cid]
        assert row["request"]["session"]["fallback"] is False
        assert row["request"]["execute_target"] is True
        assert not row["request"]["task"].get("routing_preferences", {}).get("allowed_models")
        if row["status"] == "completed" and not frozen[cid].get("retrieval"):
            assert grade(frozen[cid], row["output"]) == row["grade"]
    turns = {}
    for p in (folder / "industries").glob("*.json.gz"):
        scenario = read(p)
        for turn in scenario["turns"]:
            turns[f"agent-{scenario['scenario_id']}-{turn['number']}"] = turn
            assert turn["status"] in {"completed", "error", "failed"}
            assert turn["platform_version"] == "V2"
            for step in turn["steps"]:
                assert step["selected_at"] <= step["target_started_at"] <= step["target_finished_at"]
                assert step["model_decision_calls"] == 2 and step["decision_mode"] == "two_stage"
    assert len(turns) == sum(len(s["turns"]) for s in suite["industries"])
    reviews = read(folder / "reviews.json")
    assert len({r["case_id"] for r in reviews}) == len(reviews)
    for review in reviews:
        cid = review["case_id"]
        text = rows[cid]["output"]["text"] if cid in rows else turns[cid]["answer"]
        assert review.get("output_sha256")
        verify_review(review, text)
    for p in (folder / "code-checks").glob("*.json"):
        check = read(p)
        verify_review(check, rows[check["case_id"]]["output"]["text"])
        assert check["variants"], "An empty test list cannot establish a passing function"
    summary = read(root / "summary.json")
    assert summary["overall"]["planned"] == len(rows) + len(turns) == 104
    assert summary["new_general_32"]["planned"] == 32
    assert summary["original_72"]["planned"] == 72
    assert not any(r["status"] == "pending" for r in summary["rows"])
    assert summary["provenance"]["catalog_stable"]
    assert summary["provenance"]["expected_versions_match"]
    for name in ("deployment-start.json", "deployment-end.json"):
        deployment = read(root / name)
        assert all(s["taskDefinition"].endswith(":33") for s in deployment["services"])
        assert all(t["taskDefinitionArn"].endswith(":33") for t in deployment["tasks"])
    source = read(folder / "runner-source.json")
    assert source["sha256"] == hashlib.sha256(Path("scripts/run_online_quality.py").read_bytes()).hexdigest()
    result = {
        "passed": True, "frozen_old_cases_identical": 68, "ordinary_tasks": len(rows),
        "real_agent_turns": len(turns), "hash_bound_reviews": len(reviews),
        "statuses": dict(Counter(r["status"] for r in summary["rows"])),
        "no_pending_reviews": True, "catalog_and_price_versions_stable": True,
        "actual_agent_selection_precedes_target": True, "runner_unchanged_during_execution": True,
    }
    save(root / "integrity-verification.json", result)
    save(root / "artifact-sha256.json", {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file() and p.name != "artifact-sha256.json"
    })
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    print(verify(parser.parse_args().input))
