"""Compile exact model/configuration bindings across separate LiveBench releases."""

import copy
import csv
import hashlib
import io
import json
import math
from decimal import Decimal
from statistics import mean

BASE = "https://livebench.ai/v1/"
REFERENCE_URL = "https://openrouter.ai/api/v1/models"


def numeric(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean benchmark value")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("Invalid benchmark value")
    return result


def table(raw):
    rows = list(csv.DictReader(io.StringIO(raw.decode())))
    if not rows or "model" not in rows[0]:
        raise ValueError("Missing model column")
    indexed = {row["model"]: row for row in rows}
    if len(indexed) != len(rows):
        raise ValueError("Duplicate benchmark model IDs")
    return indexed


def compile_livebench(mappings, registry, fetch, evidence, captured_at):
    """Keep releases separate; references without scores never become evaluations."""
    sources = {}

    def read(url):
        if url not in sources:
            raw = fetch(url)
            sources[url] = raw
            evidence.append({"url": url, "sha256": hashlib.sha256(raw).hexdigest()})
        return sources[url]

    references = json.loads(read(REFERENCE_URL))["data"]
    references = {row["id"]: row for row in references}
    bindings = copy.deepcopy(registry["models"])
    for mid, sid in mappings["livebench"].items():
        bindings[mid] = {
            **bindings.get(mid, {}),
            "benchmark_ids": [sid],
            "request_fields": mappings["livebench_request_fields"].get(mid, {}),
            "configuration_status": "verified",
        }
    for binding in bindings.values():
        url = binding.get("configuration_source")
        if url:
            read(url.replace("github.com/", "raw.githubusercontent.com/").replace("/blob/", "/"))
    costs = table(read(BASE + "cost_" + registry["releases"][-1].replace("-", "_") + ".csv"))
    model_references = {}
    for mid, binding in bindings.items():
        rid = binding.get("reference_id")
        ref = references.get(rid)
        if ref is None:
            continue
        expected = binding.get("reference_canonical_slug")
        if expected and ref.get("canonical_slug") != expected:
            raise ValueError(f"Reference alias changed its model version: {rid}")
        price = ref.get("pricing", {})

        def per_million(field, pricing=price):
            value = numeric(pricing.get(field))
            return numeric(Decimal(str(pricing[field])) * 1_000_000) if value is not None else None

        model_references[mid] = {
            "source": "openrouter",
            "id": rid,
            "canonical_slug": ref.get("canonical_slug"),
            "name": ref["name"],
            "url": "https://openrouter.ai/" + rid,
            "captured_at": captured_at,
            "input_price": per_million("prompt"),
            "output_price": per_million("completion"),
            "context_tokens": ref.get("context_length"),
            "scope": "External model reference and pricing, not a benchmark or Bedrock capability.",
        }

    rows = {}
    releases = []
    for release in registry["releases"]:
        suffix = release.replace("-", "_")
        score_url, category_url = BASE + f"table_{suffix}.csv", BASE + f"categories_{suffix}.json"
        scores, categories = table(read(score_url)), json.loads(read(category_url))
        if not categories or any(not isinstance(v, list) or not v for v in categories.values()):
            raise ValueError("Invalid category schema")
        count = 0
        for mid, binding in bindings.items():
            for sid in binding.get("benchmark_ids", []):
                if sid not in scores:
                    continue
                values = {}
                for category, tasks in categories.items():
                    parts = [numeric(scores[sid].get(task)) for task in tasks]
                    if any(v is not None and v > 100 for v in parts):
                        raise ValueError("Benchmark score outside 0–100")
                    values[category] = mean(parts) if all(v is not None for v in parts) else None
                if not any(v is not None for v in values.values()):
                    continue
                cost, reference = costs.get(sid, {}), model_references.get(mid, {})
                inp, out = (
                    numeric(cost.get("input_price_per_million")),
                    numeric(cost.get("output_price_per_million")),
                )
                pricing_source = {
                    "source": "livebench",
                    "url": BASE + "cost_" + registry["releases"][-1].replace("-", "_") + ".csv",
                    "captured_at": captured_at,
                }
                if inp is None or out is None:
                    inp, out = reference.get("input_price"), reference.get("output_price")
                    pricing_source = (
                        {
                            "source": "openrouter",
                            "url": reference["url"],
                            "captured_at": captured_at,
                        }
                        if reference
                        else None
                    )
                row = {
                    "source": "livebench",
                    "source_id": sid,
                    "source_url": f"https://livebench.ai/#/release/{release}",
                    "release": release,
                    "dataset_revision": f"livebench-{release}",
                    "benchmark_configuration": sid,
                    "request_fields": binding.get("request_fields", {}),
                    "inference_config": binding.get("inference_config", {}),
                    "configuration_status": binding.get("configuration_status", "verified"),
                    "configuration_source": binding.get("configuration_source"),
                    "configuration_note": binding.get(
                        "configuration_note",
                        "按精确型号和推理配置映射；输出上限、端点和运行环境与评测可能不同。",
                    ),
                    "metrics": {
                        "quality": mean(values.values())
                        if all(v is not None for v in values.values())
                        else None,
                        "coding": values.get("Coding"),
                        "reasoning": values.get("Reasoning"),
                        "language": values.get("Language"),
                        "input_price": inp,
                        "output_price": out,
                        "output_tps": None,
                        "ttft_seconds": None,
                    },
                    "category_scores": values,
                    "pricing_source": pricing_source,
                }
                rows.setdefault(mid, []).append(row)
                count += 1
        releases.append({"release": release, "configurations": count, "source_url": score_url})

    # Every configured identity must resolve. A typo or disappearing source must not
    # silently erase an existing mapping on refresh.
    required = {mid for mid, binding in bindings.items() if binding.get("benchmark_ids")}
    missing = required - rows.keys()
    if missing:
        raise ValueError(f"Unresolved exact benchmark bindings: {sorted(missing)}")
    entries = {}
    for mid, configurations in rows.items():
        ordered = sorted(configurations, key=lambda r: (r["release"], r["source_id"]), reverse=True)
        entries[mid] = {**ordered[0], "configurations": ordered[1:]}
    return entries, model_references, releases
