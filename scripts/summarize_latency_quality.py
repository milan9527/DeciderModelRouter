"""Report all planned items, keeping format failures and content checks distinct."""

import argparse
import hashlib
import math
from collections import Counter, defaultdict
from pathlib import Path

from scripts.run_profile_quality import read, save


def verify_review(review, text):
    expected = review.get("output_sha256")
    if expected and expected != hashlib.sha256(text.encode()).hexdigest():
        raise ValueError("Review belongs to a different output")


def category(case):
    cid = case["id"]
    if cid.startswith("heldout-"):
        return "12行业业务题"
    if cid == "manufacturing-causal-regression":
        return "复杂产线分析"
    if cid.startswith(("python-", "new-code-")):
        return "代码"
    if case.get("fixture") or case.get("fixtures"):
        return "多模态"
    if case.get("retrieval"):
        return "检索"
    return "通用文本与历史"


def counts(rows):
    n = len(rows)
    passed = sum(r["status"] == "pass" for r in rows)
    content = sum(r["content_status"] == "pass" for r in rows)
    z = 1.95996398454
    rate = passed / n if n else 0
    center = (rate + z * z / (2 * n)) / (1 + z * z / n) if n else 0
    half = z * math.sqrt(rate * (1 - rate) / n + z * z / (4 * n * n)) / (1 + z * z / n) if n else 0
    return {
        "planned": n, "passed": passed, "pass_percent": round(rate * 100, 2),
        "content_passed": content, "content_percent": round(content / n * 100, 2) if n else 0,
        "status_counts": dict(Counter(r["status"] for r in rows)),
        "completed": sum(r["execution_status"] == "completed" for r in rows),
        "wilson_95_percent": [round((center - half) * 100, 2), round((center + half) * 100, 2)],
    }


def summarize(folder):
    manifest = read(folder / "manifest.json")
    reviews = {r["case_id"]: r for r in read(folder / "reviews.json")} if (folder / "reviews.json").exists() else {}
    rows = []
    for case in manifest["cases"]:
        cid = case["id"]
        path = folder / "base" / (cid + ".json.gz")
        raw = read(path) if path.exists() else {}
        review = reviews.get(cid, raw.get("grade", {"status": "unmeasured"}))
        verify_review(review, raw.get("output", {}).get("text", ""))
        content = review.get("content_status", review["status"])
        status = review["status"]
        checks = folder / "code-checks" / (cid + ".json")
        if checks.exists():
            review = read(checks)
            verify_review(review, raw.get("output", {}).get("text", ""))
            status = review["status"]
            content = "pass" if review["functional_pass"] else "fail"
        if raw.get("workflow_fit") is False or (
            status == "pass" and raw.get("status") != "completed"
        ):
            status = "fail"
        if content == "pass" and raw.get("status") != "completed":
            content = "fail"
        rows.append({
            "case_id": cid, "category": category(case), "split": case.get("split"),
            "status": status, "content_status": content,
            "execution_status": raw.get("status", "unmeasured"),
            "workflow_fit": raw.get("workflow_fit"),
            "model": raw.get("route", {}).get("selection", {}).get("selected_model_id"),
            "review": review,
        })
    for scenario in manifest["industries"]:
        path = folder / "industries" / (scenario["id"] + ".json.gz")
        report = read(path) if path.exists() else {}
        turns = {t["number"]: t for t in report.get("turns", [])}
        for i in range(len(scenario["turns"])):
            cid = f"agent-{scenario['id']}-{i + 1}"
            raw = turns.get(i + 1, {})
            review = reviews.get(cid, raw.get("grade", {"status": "unmeasured"}))
            verify_review(review, raw.get("answer", ""))
            status = review["status"]
            if status == "pass" and raw.get("status") != "completed":
                status = "fail"
            content = review.get("content_status", review["status"])
            if content == "pass" and raw.get("status") != "completed":
                content = "fail"
            rows.append({
                "case_id": cid, "category": "Agent多轮", "split": "regression",
                "status": status, "content_status": content,
                "execution_status": raw.get("status", "unmeasured"),
                "models": [s.get("route", {}).get("selection", {}).get("selected_model_id")
                           for s in raw.get("steps", [])],
                "review": review,
            })
    groups = defaultdict(list)
    for row in rows:
        groups[row["category"]].append(row)
    return {
        "arm": manifest["arm"], "suite_version": manifest["version"],
        "overall": counts(rows), "categories": {k: counts(v) for k, v in groups.items()},
        "new_industry_holdout": counts([r for r in rows if r["split"] == "new_holdout"]),
        "workflow_fit": {
            "passed": sum(r.get("workflow_fit") is True for r in rows),
            "scored": sum(r.get("workflow_fit") is not None for r in rows),
            "scope": "resolved workflow in frozen allowed set; not seven-label classifier accuracy",
        },
        "rows": rows,
        "limits": [
            "Targeted regression plus 12 new industry tasks; not a random population sample.",
            "The new_holdout label records initial suite construction; these inputs were subsequently reused for performance and fidelity regression, so final results are not blind holdout validation.",
            "Open answers reviewed by an unblinded assistant against frozen rubrics.",
            "Industry runs use the local production native adapter; AgentCore live checks are separate.",
            "Content-only checks retain wrong-format responses as failures in overall acceptance.",
            "Confidence interval describes this item sample and does not establish noninferiority.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.input)
    save(args.input / "summary.json", result)
    print(result["overall"])


if __name__ == "__main__":
    main()
