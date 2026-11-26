"""Pinned public capability evidence. Never supplies execution settings or a winner."""

import hashlib
import json
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).with_name("public_benchmarks.json")
VERSION = "public-capabilities-v1"
TEXT_QUALITY_WORKFLOWS = frozenset({"reasoning", "coding", "general_text", "customer_support"})
RELEVANT = {
    "reasoning": {"Reasoning", "Mathematics", "Data Analysis", "GPQA Diamond"},
    "coding": {"Coding", "Agentic Coding", "Deep SWE", "SWE Atlas QA", "Terminal Bench 4.0"},
    "general_text": {"Language", "IF"},
    "customer_support": {"Language", "IF", "Tau2 Airline"},
    "vision": {"MMMU Pro Vision"},
    "video_understanding": {"VGI-Bench"},
}
AGENT_METRICS = {"Long Agentic", "Tau2 Airline", "Tau3 Banking", "Terminal Bench 4.0"}


def digest(data):
    content = {k: v for k, v in data.items() if k != "version"}
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


@lru_cache(maxsize=1)
def load_public_benchmarks():
    data = json.loads(PATH.read_text())
    if data.get("schema_version") != 1 or data.get("version") != digest(data):
        raise ValueError("Public benchmark snapshot checksum/schema mismatch")
    return data


def evidence_for(model_id, workflow, *, agent=False, snapshot=None):
    data = load_public_benchmarks() if snapshot is None else snapshot
    relevant = RELEVANT.get(workflow, set()) | (AGENT_METRICS if agent else set())
    rows = []
    for record in data["records"].get(model_id, []):
        scores = {k: v for k, v in record["scores"].items() if k in relevant}
        if scores:
            rows.append({**record, "scores": scores,
                         "sample_counts": {k: record.get("sample_counts", {}).get(k) for k in scores}})
    return rows


def routing_evidence_for(model_id, workflow, *, agent=False, snapshot=None):
    """Text quality uses LiveBench only; keep the complete archive for inspection."""
    rows = evidence_for(model_id, workflow, agent=agent, snapshot=snapshot)
    if workflow in TEXT_QUALITY_WORKFLOWS:
        rows = [r for r in rows if r["source"] == "livebench"]
    return rows


def evidence_cards(records, references):
    """Compact all relevant records, with the tested effort and scope still explicit."""
    result = []
    for r in records:
        descriptor = {
            "source": r["source"], "benchmark": r["benchmark"], "release": r["release"],
            "metric": r["metric"], "scope": "external_same_model_not_current_Bedrock_configuration",
        }
        key = json.dumps(descriptor, sort_keys=True, ensure_ascii=False)
        rid = references.setdefault(key, f"r{len(references)}")
        values = ",".join(
            f"{name}={score:g}(n={r.get('sample_counts', {}).get(name) if r.get('sample_counts', {}).get(name) is not None else '?'})"
            for name, score in r["scores"].items()
        )
        state = ";INCOMPLETE" if r.get("status") == "incomplete" else ""
        result.append(f"{rid}[tested={r['configuration']}{state}]:{values}")
    return result


def inventory(snapshot=None):
    data = load_public_benchmarks() if snapshot is None else snapshot
    return {
        "version": data["version"], "captured_at": data["captured_at"],
        "model_count": len(data["records"]),
        "record_count": sum(len(v) for v in data["records"].values()),
        "sources": sorted({r["source"] for rows in data["records"].values() for r in rows}),
        "releases": sorted({r["release"] for rows in data["records"].values() for r in rows}),
        "unmapped_models": data["unmapped_models"],
        "scope": "公开成绩是同型号能力参考；配置、端点、输出预算可能不同，不是本次成功概率。",
    }
