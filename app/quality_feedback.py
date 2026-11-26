"""Reviewed development regressions, separate from public and original endpoint tests."""

import hashlib
import json
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).with_name("quality_feedback.json")


def digest(data):
    return hashlib.sha256(json.dumps(
        {k: v for k, v in data.items() if k != "version"},
        sort_keys=True, ensure_ascii=False,
    ).encode()).hexdigest()[:24]


@lru_cache(maxsize=1)
def load_feedback():
    data = json.loads(PATH.read_text())
    if data["version"] != digest(data):
        raise ValueError("Reviewed quality feedback checksum mismatch")
    return data


def evidence_for(row, workflow, snapshot=None):
    data = snapshot or load_feedback()
    found = [r for r in data["records"]
             if r["candidate_id"] == row["candidate_id"]
             and r["invoke_id"] == row["model"]["invoke_id"]
             and r["workflow"] == workflow]
    if not found:
        return None
    return {
        "version": data["version"], "samples": len(found),
        "content_passed": sum(r["content_status"] == "pass" for r in found),
        "strict_passed": sum(r["status"] == "pass" for r in found),
        "failure_categories": sorted({r["failure_category"] for r in found if r["status"] == "fail"}),
        "records": found,
        "scope": "reviewed development regression; nonrandom; budgets and prompts differ; not a success probability",
    }
