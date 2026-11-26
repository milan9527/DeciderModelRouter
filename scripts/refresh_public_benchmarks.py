"""Archive public OpenRouter/LiveBench results and compile explicit model bindings.

No model inference. No router/ensemble score is attributed to an individual model.
Public results never authorize an untested Bedrock reasoning configuration.
"""

import argparse
import csv
import hashlib
import io
import json
import math
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

import httpx

from app.public_benchmarks import digest

ROOT = Path(__file__).resolve().parents[1]
OR_PAGES = {
    "gpqa": "https://openrouter.ai/benchmarks/gpqa-diamond",
    "tau2": "https://openrouter.ai/benchmarks/tau2-bench-airline",
    "video": "https://openrouter.ai/benchmarks/vgi-bench",
    "routers": "https://openrouter.ai/benchmarks/routers",
}
ROUTER_METRICS = {
    "deep_swe": "Deep SWE", "swe_atlas_qa": "SWE Atlas QA",
    "terminal-bench-4.0": "Terminal Bench 4.0", "tau3_bench_banking": "Tau3 Banking",
    "mmmu_pro_vision": "MMMU Pro Vision",
}


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active, self.current, self.scripts = False, [], []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.active, self.current = True, []

    def handle_data(self, data):
        if self.active:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            self.scripts.append("".join(self.current))
            self.active = False


def flight_objects(raw):
    """Decode JSON only; never evaluate page JavaScript."""
    parser = Scripts()
    parser.feed(raw.decode())
    chunks = []
    prefix = "self.__next_f.push("
    for script in parser.scripts:
        if script.startswith(prefix):
            value = json.loads(script[len(prefix):].removesuffix(";").removesuffix(")"))
            if len(value) > 1 and isinstance(value[1], str):
                chunks.append(value[1])
    objects = []
    for line in "".join(chunks).splitlines():
        try:
            objects.append(json.loads(line.split(":", 1)[1]))
        except (ValueError, IndexError):
            continue  # Flight module references and text are not benchmark records.
    if not objects:
        raise ValueError("OpenRouter page has no readable public data")
    return objects


def dictionaries(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from dictionaries(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from dictionaries(value)


def number(value, *, maximum=None):
    if value is None or value in ("", "n/a", "N/A"):
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean score")
    value = float(value)
    if not math.isfinite(value) or value < 0 or (maximum is not None and value > maximum):
        raise ValueError("Invalid public score")
    return value


def csv_rows(raw):
    rows = list(csv.DictReader(io.StringIO(raw.decode())))
    result = {r["model"]: r for r in rows}
    if not rows or len(result) != len(rows):
        raise ValueError("Empty or duplicate model CSV")
    return result


def record(source, benchmark, release, source_id, configuration, scores, url, **extra):
    return {
        "source": source, "benchmark": benchmark, "release": release,
        "source_model_id": source_id, "configuration": configuration,
        "scores": scores, "url": url, "sample_counts": {},
        "metric": "score_0_100", "status": "completed",
        "scope": "external_same_model_not_current_Bedrock_configuration",
        **extra,
    }


def compile_livebench_v1(rankings):
    """Retain every latest exact source configuration, including partial categories."""
    result = defaultdict(list)
    for mid, entry in rankings["entries"].items():
        latest = {}
        for row in [entry, *entry.get("configurations", [])]:
            if row.get("source") != "livebench" or row.get("configuration_status", "verified") != "verified":
                continue
            sid = row["source_id"]
            if sid not in latest or row["release"] > latest[sid]["release"]:
                latest[sid] = row
        for sid, row in latest.items():
            scores = {k: number(v, maximum=100) for k, v in row["category_scores"].items() if v is not None}
            if scores:
                result[mid].append(record(
                    "livebench", "LiveBench 1", row["release"], sid, sid, scores, row["source_url"],
                    tested_request_fields=row.get("request_fields"), metric="category_mean_0_100",
                    provenance="routing_rankings.json",
                ))
    return result


def compile_livebench_v2(raw, bindings, release):
    tables, costs = csv_rows(raw["table.csv"]), csv_rows(raw["cost.csv"])
    categories = {k: v for k, v in json.loads(raw["categories.json"]).items() if not k.startswith("_")}
    breakdown, notes = json.loads(raw["breakdown.json"]), json.loads(raw["notes.json"])
    result = defaultdict(list)
    for mid, binding in bindings.items():
        sid = binding.get("livebench_v2")
        if not sid or sid not in tables:
            continue
        scores, counts, details = {}, {}, {}
        for category, columns in categories.items():
            values = [number(tables[sid].get(c), maximum=100) for c in columns]
            if not values or any(v is None for v in values):
                continue
            scores[category] = sum(values) / len(values)
            n = [number(costs.get(sid, {}).get("nq_" + c)) for c in columns]
            counts[category] = int(sum(n)) if all(v is not None for v in n) else None
            part = breakdown.get(category, {})
            details[category] = {
                "definitions": part.get("parts", []),
                "scores": part.get("models", {}).get(sid, {}).get("scores", {}),
            }
        if scores:
            result[mid].append(record(
                "livebench", "LiveBench 2", release, sid,
                binding.get("livebench_v2_effort", "highest_supported_effort"),
                scores, f"https://livebench.ai/#/release/{release.replace('-', '')}",
                sample_counts=counts, metric="mean_task_score_0_100", breakdown=details,
                configuration_note="See archived release notes; effort/budget/endpoint differ from Bedrock defaults.",
                notes=notes,
            ))
    return result


def compile_openrouter_page(raw, kind, bindings, captured_at):
    names = {"gpqa": "GPQA Diamond", "tau2": "Tau2 Airline", "video": "VGI-Bench"}
    benchmark = names[kind]
    # Slug AND version must match the reviewed binding. Never match a model family.
    reverse = {(v["openrouter_id"], v["canonical_slug"]): k for k, v in bindings.items()
               if v.get("openrouter_id") and v.get("canonical_slug")}
    result, seen = defaultdict(list), set()
    for row in dictionaries(flight_objects(raw)):
        if not {"modelSlug", "modelPermaslug", "median", "providers"} <= row.keys():
            continue
        mid = reverse.get((row["modelSlug"], row["modelPermaslug"]))
        if not mid or mid in seen:
            continue
        seen.add(mid)
        data = row["median"]
        score = number(data.get("accuracy"), maximum=1)
        if score is None:
            continue
        result[mid].append(record(
            "openrouter", benchmark, "snapshot-" + captured_at[:10], row["modelSlug"], "effort_unreported",
            {benchmark: score * 100}, OR_PAGES[kind], metric="accuracy_percent",
            sample_counts={benchmark: data.get("totalTasks")}, source_revision=row["modelPermaslug"],
            provider_scope="published representative result; not best-provider selection",
            recorded_at=data.get("lastRunTimestamp"),
            external_observation={k: data.get(k) for k in (
                "avgCostPerTask", "avgDurationPerTaskMs", "accuracyStdDev", "runCount")},
            configuration_note="Source headline does not disclose exact effort, output budget or our regional endpoint.",
        ))
    return result


def compile_openrouter_runs(raw, bindings):
    reverse = {}
    for mid, b in bindings.items():
        for slug in (b.get("openrouter_id"), b.get("canonical_slug")):
            if slug:
                if slug in reverse and reverse[slug] != mid:
                    raise ValueError("Ambiguous OpenRouter identity")
                reverse[slug] = mid
    result, seen = defaultdict(list), set()
    for row in dictionaries(flight_objects(raw)):
        if not {"workflow_id", "benchmark_type", "model_permaslug", "baseline"} <= row.keys():
            continue
        # Router and switching/ensemble results do not describe any single target.
        if row["baseline"] is not True or row.get("route_models") or row["id"] in seen:
            continue
        seen.add(row["id"])
        mid = reverse.get(row["model_permaslug"])
        benchmark = ROUTER_METRICS.get(row["benchmark_type"])
        score = number(row.get("accuracy"), maximum=1)
        if not mid or not benchmark or score is None:
            continue
        result[mid].append(record(
            "openrouter", benchmark, "published-router-baselines",
            row["model_permaslug"], row.get("reasoning_effort") or "effort_unreported",
            {benchmark: score * 100}, OR_PAGES["routers"], metric="accuracy_percent",
            sample_counts={benchmark: row.get("total_questions")},
            status="completed" if row.get("success") else "incomplete",
            provider_scope=row.get("provider_name"), recorded_at=row["timestamp"],
            run_id=row["id"], config_key=row.get("config_key"),
            tested_max_tokens=row.get("max_tokens"), tested_temperature=row.get("temperature"),
            external_observation={k: row.get(k) for k in (
                "total_cost", "generation_time_ms", "duration_ms", "correct_answers", "epochs", "recorded_epochs")},
        ))
    # Latest run per model/benchmark/effort/provider; include failures, never cherry-pick high scores.
    for mid, rows in result.items():
        latest = {}
        for row in sorted(rows, key=lambda r: (r["recorded_at"], r["run_id"])):
            latest[(row["benchmark"], row["configuration"], row["provider_scope"])] = row
        result[mid] = list(latest.values())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "app/public_benchmarks.json")
    args = parser.parse_args()
    registry = json.loads((ROOT / "app/public_benchmark_bindings.json").read_text())
    catalog = json.loads((ROOT / "app/bedrock_catalog.json").read_text())
    captured = datetime.now(UTC).isoformat()
    args.archive_dir.mkdir(parents=True, exist_ok=True)
    release = registry["livebench_v2_release"]
    urls = {**OR_PAGES, "models": "https://openrouter.ai/api/v1/models",
            **{k: f"https://livebench.ai/data/{release.replace('-', '')}/{k}"
               for k in ("table.csv", "categories.json", "breakdown.json", "notes.json", "cost.csv")}}

    def fetch(item):
        name, url = item
        response = httpx.get(url, timeout=45, follow_redirects=True)
        response.raise_for_status()
        raw = response.content
        filename = name.replace(".", "-") + ".source"
        (args.archive_dir / filename).write_bytes(raw)
        return name, raw, {"url": url, "file": filename, "sha256": hashlib.sha256(raw).hexdigest()}

    with ThreadPoolExecutor(max_workers=5) as executor:
        fetched = list(executor.map(fetch, urls.items()))
    raw = {k: value for k, value, _ in fetched}
    sources = [meta for _, _, meta in fetched]
    models = {r["id"]: r for r in json.loads(raw["models"])["data"]}
    for mid, binding in registry["models"].items():
        sid = binding.get("openrouter_id")
        if sid and models.get(sid, {}).get("canonical_slug") != binding["canonical_slug"]:
            raise ValueError(f"Public model identity changed or disappeared: {mid}")
    rankings_bytes = (ROOT / "app/routing_rankings.json").read_bytes()
    (args.archive_dir / "routing_rankings.json").write_bytes(rankings_bytes)
    sources.append({"url": "repository:app/routing_rankings.json", "file": "routing_rankings.json",
                    "sha256": hashlib.sha256(rankings_bytes).hexdigest()})
    compiled = {
        "livebench_v1": compile_livebench_v1(json.loads(rankings_bytes)),
        "livebench_v2": compile_livebench_v2(raw, registry["models"], release),
        **{k: compile_openrouter_page(raw[k], k, registry["models"], captured)
           for k in ("gpqa", "tau2", "video")},
        "routers": compile_openrouter_runs(raw["routers"], registry["models"]),
    }
    for name, group in compiled.items():
        if name != "video" and not any(group.values()):
            raise ValueError(f"Required source {name} has no mapped results; existing snapshot preserved")
    records = defaultdict(list)
    for group in compiled.values():
        for mid, rows in group.items():
            records[mid].extend(rows)
    for mid, rows in records.items():
        for row in rows:
            row["id"] = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()[:20]
        records[mid] = sorted(rows, key=lambda r: (r["source"], r["benchmark"], r["release"],
                                                 r["configuration"], r["id"]))
    if len(records) < 35 or not records.get("openai.gpt-6.2-sol"):
        raise ValueError("Unexpected public coverage loss; existing snapshot preserved")
    data = {
        "schema_version": 1, "captured_at": captured, "catalog_version": catalog["version"],
        "bindings_version": hashlib.sha256(json.dumps(registry, sort_keys=True).encode()).hexdigest()[:24],
        "sources": sources, "records": dict(sorted(records.items())),
        "unmapped_models": [m["id"] for m in catalog["models"] if m["id"] not in records],
        "scope": "Public capability evidence; different configurations/endpoints; no business success guarantee.",
    }
    data["version"] = digest(data)
    (args.archive_dir / "manifest.json").write_text(json.dumps(sources, indent=2) + "\n")
    tmp = args.output.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(args.output)
    print(json.dumps({"version": data["version"], "models": len(records),
                      "records": sum(len(v) for v in records.values()),
                      "sources": sorted({r["source"] for rows in records.values() for r in rows})}))


if __name__ == "__main__":
    main()
