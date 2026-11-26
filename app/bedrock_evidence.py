"""Exact-endpoint Bedrock measurements, kept separate from public benchmark scores."""

import hashlib
import json
from pathlib import Path

PATH = Path(__file__).with_name("bedrock_evaluations.json")


def digest(data):
    return hashlib.sha256(json.dumps({k: v for k, v in data.items() if k != "version"},
                                    sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


def load_evidence():
    if not PATH.exists():
        return {"version": "unmeasured", "entries": {}, "scope": "no_measurements"}
    data = json.loads(PATH.read_text())
    if data["version"] != digest(data):
        raise ValueError("Bedrock measurement checksum mismatch")
    return data


def measured(model, evidence):
    entry = evidence.get("entries", {}).get(model["id"], {})
    return entry if entry.get("invoke_id") == model.get("invoke_id") else {}


def measured_scores(entry, workflow):
    # Keep content correctness, exact-output compliance and protocol checks separate.
    key = "general_text" if workflow == "customer_support" else workflow
    return entry.get("measurements", {}).get(key, {})


def runtime_observations(entry, fields, inference, workflow):
    """Censored response deadlines, bound to the actual configuration and task kind."""
    return [
        row for row in entry.get("runtime_observations", [])
        if row["request_fields"] == fields and row["inference_config"] == inference
        and row["workflow"] == workflow
    ]


def public_summary(benchmark, workflow):
    from app.routing_evidence import task_metrics

    return {"source": benchmark.get("source"), "release": benchmark.get("release"),
            "configuration": benchmark.get("benchmark_configuration"),
            "scores": task_metrics(benchmark, workflow),
            "note": "External endpoint; compare raw scores only within the same dataset and metric."}
