"""Build comparable, version-bound business evidence from independently split outcomes."""

import hashlib
import json
import math
import time
from collections import defaultdict
from decimal import Decimal
from typing import Literal

from pydantic import Field

from app.routing_profile import RequestProfile
from app.schemas import StrictModel


class Observation(StrictModel):
    case_id: str = Field(min_length=1, max_length=160)
    split: Literal["calibration", "validation"]
    configuration_id: str = Field(pattern=r"^[a-f0-9]{24}$")
    profile: RequestProfile
    input_tokens: int = Field(ge=1, le=2_000_000, strict=True)
    max_output_tokens: int = Field(ge=0, le=4096, strict=True)
    passed: bool = Field(strict=True)
    latency_ms: float = Field(ge=0, le=3_600_000, allow_inf_nan=False)
    total_cost_usd: Decimal = Field(ge=0, le=1_000_000, max_digits=25, decimal_places=18)
    cost_scope: Literal["actual_usd", "reference_usd"]


def wilson_lower(successes, count):
    z = 1.96
    rate = successes / count
    return (
        rate
        + z * z / (2 * count)
        - z * math.sqrt(rate * (1 - rate) / count + z * z / (4 * count * count))
    ) / (1 + z * z / count)


def summarize(rows):
    count = len(rows)
    successes = sum(row.passed for row in rows)
    times = sorted(row.latency_ms for row in rows)
    return {
        "samples": count,
        "successes": successes,
        "success_rate": successes / count,
        "success_lower_bound": wilson_lower(successes, count),
        "p95_ms": times[math.ceil(0.95 * count) - 1],
        "mean_cost_usd": str(sum((r.total_cost_usd for r in rows), Decimal(0)) / count),
    }


def snapshot_hash(data):
    content = {k: v for k, v in data.items() if k != "version"}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()[:24]


def build_calibration(
    observations,
    known_configurations,
    rankings,
    *,
    catalog_version,
    minimum_samples=30,
    ttl_days=30,
):
    from app.routing_selector import cohort_key

    if minimum_samples < 2 or not 1 <= ttl_days <= 90:
        raise ValueError("Use at least two independent samples per split and a 1–90 day TTL")
    if not observations:
        raise ValueError("No observations; refusing to publish fabricated calibration")
    groups = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    identities, splits, profiles = set(), {}, {}
    scopes = set()
    for raw in observations:
        row = Observation.model_validate(raw)
        if row.configuration_id not in known_configurations:
            raise ValueError("Observed configuration is not an evidenced Bedrock configuration")
        if row.profile.workflow not in known_configurations[row.configuration_id]["workflows"]:
            raise ValueError("Configuration does not support the observed workflow")
        identity = (row.case_id, row.configuration_id)
        if identity in identities:
            raise ValueError("Repeated case/configuration; repetitions cannot inflate sample size")
        identities.add(identity)
        if row.case_id in splits and splits[row.case_id] != row.split:
            raise ValueError("The same task appears in calibration and validation")
        splits[row.case_id] = row.split
        if row.profile.workflow == "embedding" and row.max_output_tokens != 0:
            raise ValueError("Embedding observations must have zero output token cap")
        if row.profile.workflow != "embedding" and row.max_output_tokens < 1:
            raise ValueError("Text observations need an output token cap")
        key = cohort_key(row.profile, row.input_tokens, row.max_output_tokens)
        signature = (row.profile.model_dump_json(), key)
        if row.case_id in profiles and profiles[row.case_id] != signature:
            raise ValueError("Candidate configurations were evaluated on different task profiles")
        profiles[row.case_id] = signature
        scopes.add(row.cost_scope)
        groups[key][row.split][row.configuration_id].append(row)
    if len(scopes) != 1:
        raise ValueError("Actual bills and reference costs must not be mixed")
    cohorts = {}
    for key, splits in groups.items():
        if set(splits) != {"calibration", "validation"}:
            raise ValueError("Every cohort needs independent calibration and validation tasks")
        training, validation = splits["calibration"], splits["validation"]
        if set(training) != set(validation):
            raise ValueError("Configuration coverage differs between splits")
        for cohort_rows in (training, validation):
            case_sets = [{r.case_id for r in rows} for rows in cohort_rows.values()]
            if any(len(cases) < minimum_samples for cases in case_sets):
                raise ValueError("Too few independent tasks")
            if any(cases != case_sets[0] for cases in case_sets):
                raise ValueError(
                    "Configurations must be measured on the same tasks, including failures"
                )
        cohorts[key] = {
            "status": "validated",
            "configurations": {
                cid: {**summarize(rows), "heldout": summarize(validation[cid])}
                for cid, rows in training.items()
            },
        }
    result = {
        "schema_version": 1,
        "status": "measured",
        "ranking_version": rankings["version"],
        "catalog_version": catalog_version,
        "captured_at": int(time.time()),
        "expires_at": int(time.time()) + ttl_days * 86400,
        "minimum_samples_per_split": minimum_samples,
        "cost_scope": next(iter(scopes)),
        "method": "Calibration split supplies Wilson 95% lower success bound, mean total cost and observed P95. Heldout split is reporting only; no threshold tuning on it.",
        "cohorts": cohorts,
    }
    result["version"] = snapshot_hash(result)
    return result


def verify_snapshot(data):
    if data.get("version") == "uncalibrated" and data.get("cohorts") == {}:
        return data
    if data.get("version") != snapshot_hash(data):
        raise ValueError("Calibration checksum mismatch")
    for cohort in data["cohorts"].values():
        for stats in cohort["configurations"].values():
            if (
                not 0 <= stats["success_lower_bound"] <= 1
                or not math.isfinite(stats["p95_ms"])
                or stats["p95_ms"] < 0
            ):
                raise ValueError("Invalid calibration metrics")
            cost = Decimal(stats["mean_cost_usd"])
            if not cost.is_finite() or cost < 0:
                raise ValueError("Invalid calibration cost")
    return data
