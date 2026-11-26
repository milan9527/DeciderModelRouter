"""Experimental profile -> CPU policy. Not imported by the live gateway.

Input candidates must come from gateway_model_pool.build_pool for the actual
profile. This module does not replace capability, budget, context or health checks.
Only stdlib dependencies so the identical policy can be timed on the GPU host.
"""

import math
from decimal import Decimal, InvalidOperation

VERSION = "profile-cpu-policy-experiment-v1"
TOLERANCE = {"low": 0.20, "medium": 0.08, "high": 0.0}


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        return None
    return result if result.is_finite() and result >= 0 else None


def lower_bound(evidence):
    """Conservative development-evidence score, NOT production success probability."""
    n, passed = evidence.get("samples"), evidence.get("content_passed")
    if type(n) is not int or n <= 0 or type(passed) is not int or not 0 <= passed <= n:
        return None
    z, p = 1.96, passed / n
    return (p + z * z / (2 * n) - z * math.sqrt(
        p * (1 - p) / n + z * z / (4 * n * n)
    )) / (1 + z * z / n)


def public_vector(row):
    # Scores stay keyed by dataset/source, release AND metric.
    values = {}
    for bench in row.get("public_evidence", []):
        if not bench.get("source") or not bench.get("release"):
            continue
        for metric, value in bench.get("scores", {}).items():
            score = number(value)
            if score is not None:
                values[(bench["source"], bench["release"], metric)] = score
    return values


def public_dominates(a, b):
    """Only resolve otherwise equal evidence/cost using identical public cohorts."""
    if a["quality"] != b["quality"] or a["cost"] is None or a["cost"] != b["cost"]:
        return False
    av, bv = a["public"], b["public"]
    return bool(av) and av.keys() == bv.keys() and all(av[k] >= bv[k] for k in av) and any(
        av[k] > bv[k] for k in av
    )


def choose(profile, candidates, *, cost_tier="medium", previous_candidate_id=None):
    """Return selected exact configuration and an audit row for EVERY candidate."""
    if cost_tier not in TOLERANCE:
        raise ValueError("Invalid cost tier")
    ids = [r["candidate_id"] for r in candidates]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate candidate configuration")
    # Comparing Wilson values from different test suites would silently conflate tasks.
    suites = {r.get("measured_evidence", {}).get("suite") for r in candidates
              if lower_bound(r.get("measured_evidence", {})) is not None}
    if None in suites or len(suites) > 1:
        raise ValueError("Direct measurements require one shared, identified development suite")
    tier = cost_tier
    if cost_tier == "medium":
        if profile["difficulty"] == "complex" or profile["stronger_model_benefit"] == "high":
            tier = "high"
        elif profile["difficulty"] == "simple" and profile["stronger_model_benefit"] == "low":
            tier = "low"
    rows = []
    for candidate in candidates:
        evidence = candidate.get("measured_evidence", {})
        rows.append({
            "candidate": candidate,
            "quality": lower_bound(evidence),
            "cost": number(candidate.get("cost_evidence", {}).get("expected_usd")),
            "latency": number(evidence.get("p95_ms")),
            "public": public_vector(candidate),
        })
    best = max((r["quality"] or 0 for r in rows), default=0)
    floor = max(0, best - TOLERANCE[tier])
    # Unknown is preserved as null in the audit, not presented as a measured failure.
    band = [r for r in rows if (r["quality"] or 0) + 1e-12 >= floor
            and (best <= 1e-12 or (r["quality"] is not None and r["quality"] > 0))]
    sensitive = profile["latency"] == "sensitive"
    shortlist = [r for r in band if not any(
        public_dominates(other, r) and (not sensitive or other["latency"] == r["latency"])
        for other in band
    )]

    def known(value):
        return (value is None, value if value is not None else Decimal(0))

    def key(row):
        cost, latency = known(row["cost"]), known(row["latency"])
        return ((latency, cost) if sensitive else (cost,)) + (
            -(row["quality"] or 0), row["candidate"]["candidate_id"],
        )

    selected = min(shortlist, key=key) if shortlist else None
    sticky = False
    previous = next((r for r in shortlist if r["candidate"]["candidate_id"] == previous_candidate_id), None)
    # Keep an adequate previous configuration only within a declared 5% cost window.
    # Unknown prices or latencies cannot demonstrate that this condition holds.
    if previous and selected and previous["cost"] is not None and selected["cost"] is not None:
        close_cost = previous["cost"] <= selected["cost"] * Decimal("1.05")
        close_latency = not sensitive or (
            previous["latency"] is not None and selected["latency"] is not None
            and previous["latency"] <= selected["latency"] * Decimal("1.05")
        )
        if close_cost and close_latency:
            selected, sticky = previous, True
    band_ids = {r["candidate"]["candidate_id"] for r in band}
    shortlist_ids = {r["candidate"]["candidate_id"] for r in shortlist}
    selected_row = selected["candidate"] if selected else None
    return {
        "policy_version": VERSION, "requested_tier": cost_tier, "effective_tier": tier,
        "profile": profile, "candidate_count": len(rows), "quality_floor": floor,
        "quality_tolerance": TOLERANCE[tier], "quality_band_count": len(band),
        "selected_candidate_id": selected_row["candidate_id"] if selected_row else None,
        "selected_model_id": selected_row["model"]["id"] if selected_row else None,
        "selected_configuration": {
            k: selected_row["benchmark"].get(k, {})
            for k in ("request_fields", "inference_config")
        } if selected_row else None,
        "previous_configuration_retained": sticky,
        "cache_discount_used": False,
        "calibration_status": "uncalibrated_development_heuristic",
        "audit": [{
            "candidate_id": r["candidate"]["candidate_id"],
            "model_id": r["candidate"]["model"]["id"],
            "development_lower_bound": r["quality"],
            "expected_usd": str(r["cost"]) if r["cost"] is not None else None,
            "p95_ms": str(r["latency"]) if r["latency"] is not None else None,
            "public_comparable_metrics": len(r["public"]),
            "status": (
                "selected" if r is selected else
                "outside_development_quality_band" if r["candidate"]["candidate_id"] not in band_ids else
                "public_dominated_at_equal_quality_and_cost" if r["candidate"]["candidate_id"] not in shortlist_ids else
                "eligible_alternative"
            ),
        } for r in rows],
    }
