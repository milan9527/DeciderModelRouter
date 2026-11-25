"""Conservative output checks: completion is not semantic correctness."""

import json
import math
import re
from decimal import Decimal, InvalidOperation
from fractions import Fraction


def grade(case, output):
    if not output.get("complete"):
        return {"status": "fail", "reason": "目标输出未完成或被截断", "source": "automatic"}
    kind = case["judge"]["kind"]
    if kind == "manual":
        return {"status": "pending", "reason": case["judge"]["rubric"], "source": "manual_required"}
    text = output.get("text", "").strip()
    expected = case["judge"]["expected"]
    passed = False
    if kind == "exact":
        passed = text == str(expected).strip()
    elif kind == "number":
        # Require the entire response to be the requested number, not an incidental match.
        try:
            value = Decimal(text)
            passed = bool(re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text)) and value.is_finite() and value == Decimal(str(expected))
        except InvalidOperation:
            pass
    elif kind == "json":
        try:
            def unique_keys(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError("Duplicate JSON key")
                    value[key] = item
                return value
            value = json.loads(text, object_pairs_hook=unique_keys)
            passed = json.dumps(value, sort_keys=True, allow_nan=False) == json.dumps(expected, sort_keys=True, allow_nan=False)
        except (ValueError, TypeError, RecursionError):
            pass
    elif kind == "fraction":
        value = text
        for left, right in (("\\(", "\\)"), ("$$", "$$"), ("$", "$")):
            if value.startswith(left) and value.endswith(right):
                value = value[len(left):-len(right)].strip()
                break
        value = {"⅓": "1/3", "⅔": "2/3", "½": "1/2", "¼": "1/4", "¾": "3/4"}.get(value, value)
        match = re.fullmatch(r"([+-]?\d+)\s*/\s*(\d+)", value)
        if not match:
            match = re.fullmatch(r"\\frac\s*\{\s*([+-]?\d+)\s*\}\s*\{\s*(\d+)\s*\}", value)
        if match:
            try:
                numerator, denominator = map(int, match.groups())
                passed = denominator > 0 and math.gcd(numerator, denominator) == 1 and Fraction(numerator, denominator) == Fraction(expected)
            except (ValueError, ZeroDivisionError):
                pass
    elif kind == "transcript":
        def normalize(s):
            return " ".join(re.findall(r"\w+", s.casefold()))
        passed = normalize(text) == normalize(expected)
    elif kind == "rerank":
        results = output.get("rerank_results") or []
        passed = bool(results) and results[0].get("index") == expected
    return {
        "status": "pass" if passed else "fail",
        "reason": f"{kind} 对照检查；只代表此用例的明确验收条件",
        "source": "automatic",
    }


def effective_grade(row):
    return row.get("review") or row.get("grade", {"status": "unmeasured"})


def summarize(rows, providers, planned):
    from app.gateway_store import percentile

    summaries = []
    for provider in providers:
        sample = [r for r in rows if r["provider"] == provider and r["arm"] == "routed"]
        passed = sum(effective_grade(r)["status"] == "pass" for r in sample)
        graded = sum(effective_grade(r)["status"] in {"pass", "fail"} for r in sample)
        measured_costs = [
            float(r["output"]["observed_reference_cost_usd"])
            for r in sample if r.get("output", {}).get("observed_reference_cost_usd") is not None
        ]
        target_rows = [r for r in sample if r.get("observation_id")]
        pairs = []
        for row in sample:
            other = [r for r in rows if r["provider"] == provider and r["case_id"] == row["case_id"] and r["arm"] != "routed"]
            scored = [r for r in other if effective_grade(r)["status"] in {"pass", "fail"}]
            if effective_grade(row)["status"] in {"pass", "fail"} and scored:
                pairs.append((row, scored))
        avoidable_failures = sum(
            effective_grade(row)["status"] == "fail" and any(effective_grade(r)["status"] == "pass" for r in others)
            for row, others in pairs
        )
        cost_pairs = []
        for row, others in pairs:
            cost = row.get("output", {}).get("observed_reference_cost_usd")
            passing = [
                float(r["output"]["observed_reference_cost_usd"]) for r in others
                if effective_grade(r)["status"] == "pass"
                and r.get("output", {}).get("observed_reference_cost_usd") is not None
            ]
            if effective_grade(row)["status"] == "pass" and cost is not None and passing:
                cost_pairs.append((float(cost), min(passing)))
        summaries.append({
            "provider": provider, "recorded": len(sample), "planned": planned,
            "route_selected": sum(r.get("route_status") == "selected" for r in sample),
            "workflow_fit": sum(r.get("workflow_fit") is True for r in sample),
            "workflow_checked": sum(r.get("workflow_fit") is not None for r in sample),
            "target_completed": sum(r.get("output", {}).get("complete") is True for r in sample),
            "answer_failed": sum(
                r.get("output", {}).get("complete") is True and effective_grade(r)["status"] == "fail"
                for r in sample
            ),
            "output_incomplete": sum(r["status"] == "incomplete" for r in sample),
            "quality_pass": passed, "quality_graded": graded,
            "quality_pass_rate": passed / graded if graded else None,
            "quality_coverage": graded / len(sample) if sample else None,
            "proven_success_over_recorded": passed / len(sample) if sample else None,
            "pending": sum(effective_grade(r)["status"] == "pending" for r in sample),
            "review_methods": {
                method: sum(r.get("review", {}).get("method") == method for r in sample)
                for method in ("user_review", "assistant_review", "isolated_tests")
            },
            "gaps": sum(r["status"] in {"capability_gap", "no_route", "execution_blocked"} for r in sample),
            "errors": sum(r["status"] == "error" for r in sample),
            "budget_skipped": sum(r["status"] == "budget_exhausted" for r in sample),
            "boundary_checks": sum(r.get("boundary_check") is not None for r in sample),
            "boundary_pass": sum(r.get("boundary_check") is True for r in sample),
            "route_p50_ms": percentile([r.get("route_ms") for r in sample], .5),
            "route_p95_ms": percentile([r.get("route_ms") for r in sample], .95),
            "target_p50_ms": percentile([r.get("output", {}).get("backend_total_ms") for r in target_rows], .5),
            "target_p95_ms": percentile([r.get("output", {}).get("backend_total_ms") for r in target_rows], .95),
            "known_reference_cost_usd": round(math.fsum(measured_costs), 9),
            "cost_known_samples": len(measured_costs), "target_observations": len(target_rows),
            "paired_quality_cases": len(pairs), "avoidable_failures_vs_tested_baselines": avoidable_failures,
            "cost_comparable_passing_pairs": len(cost_pairs),
            "lower_cost_passing_alternative_cases": sum(b < a for a, b in cost_pairs),
            "reference_cost_above_tested_passing_alternative_usd": round(sum(max(0, a - b) for a, b in cost_pairs), 9),
        })
    return summaries
