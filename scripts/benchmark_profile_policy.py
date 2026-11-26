"""Prepare/replay the isolated profile + CPU policy experiment.

No production routes, deployment settings or target-model calls are changed.
Run ``python -m scripts.benchmark_profile_policy --help`` for the three phases.
"""

import argparse
import base64
import gzip
import hashlib
import json
import statistics
import subprocess
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from scripts.profile_policy import VERSION, choose


def encode(value):
    return base64.b64encode(gzip.compress(json.dumps(value).encode(), mtime=0)).decode()


def read_json(path):
    data = path.read_bytes()
    return json.loads(gzip.decompress(data) if path.suffix == ".gz" else data)


def write_json(path, value):
    data = json.dumps(value, ensure_ascii=False, indent=2).encode()
    path.write_bytes(gzip.compress(data, mtime=0) if path.suffix == ".gz" else data)


def prepare(args):
    from app.bedrock_evidence import load_evidence
    from app.gateway_model_pool import build_pool
    from app.routing import Task, exclusion, load_catalog, load_rankings
    from app.routing_profile import RequestProfile

    catalog, rankings, evidence = load_catalog(), load_rankings(), load_evidence()
    cases = []
    for row in read_json(args.baseline):
        task = Task(text=json.loads(row["profile_payload"]["state"])["request"])
        profile = RequestProfile(**row["expected_profile"])
        timings = []
        for _ in range(21):
            started = time.perf_counter()
            pool = build_pool(task, profile, catalog, rankings, exclusion, evidence=evidence)
            timings.append((time.perf_counter() - started) * 1000)
        assert len(pool["candidates"]) == row["selection_candidates"]
        cases.append({
            "case": row["case"], "profile_payload": row["profile_payload"],
            "expected_profile": row["expected_profile"], "candidates": pool["candidates"],
            "excluded": pool["excluded"], "cost_context": pool["cost_context"],
            "local_pool_ms": timings[1:], "local_pool_median_ms": statistics.median(timings[1:]),
        })
    data = {
        "created_at": datetime.now(UTC).isoformat(),
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "policy_version": VERSION,
        "policy_sha256": hashlib.sha256(Path("scripts/profile_policy.py").read_bytes()).hexdigest(),
        "catalog_version": catalog["version"], "ranking_version": rankings["version"],
        "measurement_version": evidence["version"],
        "pool_timing_boundary": "local workspace CPU, warm in-memory evidence/catalog, build_pool only",
        "cases": cases,
    }
    write_json(args.output, data)
    print(json.dumps({r["case"]: {"candidates": len(r["candidates"]),
                                 "local_pool_median_ms": r["local_pool_median_ms"]} for r in cases}))


def render_remote(args):
    data = read_json(args.inputs)
    # Transport exactly the fields consumed by the policy; the full audit snapshot
    # remains in inputs.json.gz. Removing unused catalog metadata reduces SSM size.
    for case in data["cases"]:
        case["candidates"] = [{
            **{k: r[k] for k in ("candidate_id", "measured_evidence", "cost_evidence", "public_evidence")},
            "model": {"id": r["model"]["id"]},
            "benchmark": {k: r["benchmark"].get(k, {})
                          for k in ("request_fields", "inference_config")},
        } for r in case["candidates"]]
        del case["excluded"]
    bundle = {
        "scripts/__init__.py": "",
        "scripts/profile_policy.py": Path("scripts/profile_policy.py").read_text(),
        "scripts/benchmark_profile_policy.py": Path(__file__).read_text(),
        "inputs.json": json.dumps(data),
    }
    # Nothing is installed into a service directory, and no production process is restarted.
    source = (
        "import base64,gzip,json,pathlib,subprocess,sys,tempfile\n"
        f"bundle=json.loads(gzip.decompress(base64.b64decode({encode(bundle)!r})))\n"
        "with tempfile.TemporaryDirectory(prefix='dmr-policy-benchmark-') as temp:\n"
        " for name,body in bundle.items():\n"
        "  path=pathlib.Path(temp)/name\n"
        "  path.parent.mkdir(parents=True,exist_ok=True)\n"
        "  path.write_text(body)\n"
        f" subprocess.run([sys.executable,'-m','scripts.benchmark_profile_policy','run',"
        f"'--inputs','inputs.json','--url',{args.url!r},'--key-file',{str(args.key_file)!r},"
        f"'--repetitions',{str(args.repetitions)!r}],cwd=temp,check=True)\n"
    )
    if len(source.encode()) > 63000:
        raise ValueError("SSM script too large; split the input cases")
    args.output.write_text(source)
    print(f"Remote script: {len(source.encode())} bytes")


def run(args):
    data = read_json(args.inputs)
    key = args.key_file.read_text().strip()
    rows, audits = [], {}
    for trial in range(args.repetitions + 1):
        cases = data["cases"] if trial % 2 == 0 else list(reversed(data["cases"]))
        for case in cases:
            started = time.perf_counter()
            request = urllib.request.Request(
                args.url, data=json.dumps(case["profile_payload"]).encode(),
                headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=45) as response:
                result = json.load(response)
            http_ms = (time.perf_counter() - started) * 1000
            policy_start = time.perf_counter()
            profile = {k: v["choice"] for k, v in result["answers"].items()}
            if profile != case["expected_profile"]:
                raise ValueError("GPU profile changed; rebuild the hard-filtered candidate pool")
            decision = choose(profile, case["candidates"])
            policy_ms = (time.perf_counter() - policy_start) * 1000
            total_ms = (time.perf_counter() - started) * 1000
            if result.get("forward_passes") != 1:
                raise ValueError("Expected one instrumented Liquid forward pass")
            audits[case["case"]] = decision
            rows.append({
                "case": case["case"], "trial": trial, "total_ms": total_ms,
                "gpu_http_ms": http_ms, "cpu_policy_ms": policy_ms,
                "service_ms": result["latency_ms"], "gpu_calls": 1,
                "forward_passes": result["forward_passes"], "usage": result["usage"],
                "model": result["model"], "profile": profile,
                "selected_candidate_id": decision["selected_candidate_id"],
                "selected_model_id": decision["selected_model_id"],
                "selected_configuration": decision["selected_configuration"],
                "candidate_count": decision["candidate_count"],
                "effective_tier": decision["effective_tier"],
            })
    summary = []
    for case in data["cases"]:
        measured = [r for r in rows if r["case"] == case["case"] and r["trial"] > 0]
        metrics = {}
        for field in ("total_ms", "gpu_http_ms", "cpu_policy_ms"):
            values = sorted(r[field] for r in measured)
            metrics[field] = {
                "median": statistics.median(values), "min": values[0], "max": values[-1],
                "sample_p95": values[max(0, (95 * len(values) + 99) // 100 - 1)],
            }
        summary.append({
            "case": case["case"], "samples": len(measured), "metrics": metrics,
            "selected_models": sorted({r["selected_model_id"] for r in measured}),
            "candidate_count": measured[0]["candidate_count"],
            "input_tokens": measured[0]["usage"]["input_tokens"],
        })
    output = {
        "created_at": datetime.now(UTC).isoformat(), "production_changed": False,
        "policy_version": VERSION, "policy_sha256": data["policy_sha256"],
        "source_commit": data["source_commit"],
        "inputs_sha256": hashlib.sha256(args.inputs.read_bytes()).hexdigest(),
        "method": {
            "warmups_per_case": 1, "measured_repetitions": args.repetitions,
            "order": "case order reversed on alternate rounds",
            "boundary": "GPU-host local authenticated HTTP + profile validation + CPU policy",
            "excluded": "ECS network, pool construction, persistence, target execution",
            "pool": "prebuilt from seed assessment; every actual assessment must match before selection",
        },
        "summary": summary, "rows": rows, "audits": audits,
    }
    # Compressed stdout avoids SSM's stdout truncation; contains no credentials.
    print(json.dumps({"gzip_base64": encode(output)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    phases = parser.add_subparsers(dest="phase", required=True)
    p = phases.add_parser("prepare")
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.set_defaults(action=prepare)
    for phase, function in (("remote", render_remote), ("run", run)):
        p = phases.add_parser(phase)
        p.add_argument("--inputs", type=Path, required=True)
        p.add_argument("--url", required=True)
        p.add_argument("--key-file", type=Path, required=True)
        p.add_argument("--repetitions", type=int, default=20)
        if phase == "remote":
            p.add_argument("--output", type=Path, required=True)
        p.set_defaults(action=function)
    args = parser.parse_args()
    if hasattr(args, "repetitions") and not 1 <= args.repetitions <= 100:
        parser.error("repetitions must be between 1 and 100")
    args.action(args)


if __name__ == "__main__":
    main()
