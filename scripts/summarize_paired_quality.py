"""Aggregate all planned cases, preserving missing measurements and pairwise disagreements."""

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from app.gateway_store import percentile
from scripts.check_paired_code import CASES, NEW
from scripts.run_paired_quality import ARMS
from scripts.run_profile_quality import read, save
from scripts.summarize_profile_quality import MEDIA, code_syntax


def stats(rows):
    counts = Counter(r["outcome"]["status"] for r in rows)
    return {"planned": len(rows), "passed": counts["pass"], "failed": counts["fail"],
            "pending": counts["pending"], "unmeasured": counts["unmeasured"],
            "pass_rate": counts["pass"] / len(rows) if rows else None,
            "completed": sum(r["execution_status"] == "completed" for r in rows)}


def distribution(values):
    return {"count": len(values), "p50": percentile(values, .5), "p95": percentile(values, .95),
            "min": min(values) if values else None, "max": max(values) if values else None}


def build(folder):
    manifest = read(folder / "manifest.json")
    reviews = {p.name.removesuffix(".json.gz"): read(p) for p in (folder / "paired-reviews").glob("*.gz")}
    audit_path = folder / "assistant-audits.json"
    audits = read(audit_path) if audit_path.exists() else {}
    result = {"manifest": manifest, "arms": {}, "pairwise": {}}
    for arm in ARMS:
        root = folder / arm
        suite = read(root / "manifest.json")
        rows, routes, outputs, histories = [], [], [], []

        def reviewed(key, fallback, arm=arm):
            if f"{arm}:{key}" in audits:
                return audits[f"{arm}:{key}"]
            value = reviews.get(key, {})
            if value.get("status") == "completed" and arm in value["reviews"]:
                return {**value["reviews"][arm], "method": value["method"]}
            return fallback

        for group, cases in [("base", suite["base_cases"]), ("additional", suite["additional_cases"]),
                             ("holdout", suite["holdout_cases"])]:
            for case in cases:
                path = root / group / (case["id"] + ".json.gz")
                source = read(path) if path.exists() else {"status": "not_recorded"}
                state, key = source["status"], group + "-" + case["id"]
                row = {"id": key, "cohort": group, "family": case["family"], "title": case["title"],
                       "execution_status": state, "source": str(path.relative_to(folder))}
                if source.get("route"):
                    route = source["route"]
                    routes.append(route)
                    row.update(model_id=route["selection"].get("selected_model_id"),
                               route_ms=route["route_ms"], profile=route["profile"])
                if source.get("output"):
                    outputs.append(source["output"])
                    row["target_ms"] = source["output"]["backend_total_ms"]
                if source.get("boundary_check") is True:
                    outcome = {"status": "pass", "kind": "expected_boundary", "reason": "预设输入/预算边界正确拦截"}
                elif state in {"capability_gap", "started", "not_recorded"}:
                    outcome = {"status": "unmeasured", "kind": state,
                               "reason": case.get("gap") or "尚无完成观测"}
                elif state != "completed":
                    outcome = {"status": "fail", "kind": state, "reason": str(source.get("error") or source.get("grade"))}
                elif case["id"] in set(CASES) | set(NEW):
                    check = root / "code-checks" / (case["id"] + ".json")
                    outcome = read(check) if check.exists() else {"status": "pending", "reason": "等待隔离代码检查"}
                elif case["id"] in {"tts", "tts-zh", "image-edit"}:
                    media = root / "media-reviews" / (case["id"] + ".json")
                    outcome = read(media) if media.exists() else {"status": "pending", "reason": "等待实际媒体检查"}
                    # ASR disagreement in uncalibrated Chinese is not a validated content failure.
                    if case["id"] == "tts-zh":
                        outcome = {**outcome, "status": "pending", "reason": "中文音频需校准听审，未由ASR不匹配直接判错"}
                    outcome = reviewed(key, outcome)
                elif source.get("grade", {}).get("status") in {"pass", "fail"}:
                    outcome = source["grade"]
                else:
                    outcome = reviewed(key, source.get("grade") or {"status": "pending", "reason": "等待质量复核"})
                if f"{arm}:{key}" in audits and state == "completed":
                    outcome = audits[f"{arm}:{key}"]
                row["outcome"] = outcome
                rows.append(row)
        for item in suite["industry_scenarios"]:
            path = root / "industries" / (item["id"] + ".json.gz")
            report = read(path) if path.exists() else {}
            turns = {t["number"]: t for t in report.get("turns", [])}
            mids, cache_read, tools, retained_queries, turn_traces = [], 0, 0, set(), []
            for number, task in enumerate(item["turns"], 1):
                turn = turns.get(number, {"status": "not_recorded"})
                key, state = f"industry-{item['id']}-{number}", turn["status"]
                turn_mids, turn_tools, duplicate_queries = [], [], []
                available_queries = set(retained_queries)
                for step in turn.get("steps", []):
                    route = step["route"]
                    routes.append(route)
                    if mid := route["selection"].get("selected_model_id"):
                        mids.append(mid)
                        turn_mids.append(mid)
                    if step.get("output"):
                        output = step["output"]
                        outputs.append(output)
                        cache_read += (output.get("cache") or {}).get("read_tokens", 0)
                        tools += len(output.get("tool_calls", []))
                        for call, response in zip(output.get("tool_calls", []), step.get("tool_results", []),
                                                  strict=False):
                            turn_tools.append({"name": call["name"], "input": call["input"]})
                            signature = call["name"] + ":" + json.dumps(call["input"], sort_keys=True)
                            if call["name"] in {"query_records", "read_policy"}:
                                if signature in available_queries:
                                    duplicate_queries.append(signature)
                                if response["toolResult"]["status"] == "success":
                                    available_queries.add(signature)
                syntax = code_syntax(turn.get("answer", "")) if number == 3 else []
                if state in {"not_recorded", "started", "dependency_blocked"}:
                    outcome = {"status": "unmeasured", "reason": state}
                elif state != "completed":
                    outcome = {"status": "fail", "kind": state, "reason": turn.get("error") or state}
                else:
                    outcome = reviewed(key, {"status": "pending", "reason": "等待业务/代码评审"})
                    invalid = [c for c in syntax if c["status"] == "invalid_syntax"]
                    if invalid and f"{arm}:{key}" not in audits:
                        outcome = {"status": "fail", "method": "python_syntax_check",
                                   "reason": "Python代码块存在语法错误", "errors": invalid}
                    if item["id"] == "saas" and number == 4:
                        count = len(re.findall(r"[\u4e00-\u9fff]", turn.get("answer", "")))
                        if count > 100:
                            outcome = {"status": "fail", "method": "deterministic_han_count",
                                       "reason": f"明确要求不超过100字，实际仅汉字就有{count}字",
                                       "han_characters": count}
                    if item["id"] == "retail" and number == 4 and any(
                            t["name"] in {"query_records", "read_policy"} for t in turn_tools):
                        outcome = {"status": "fail", "method": "native_tool_trace",
                                   "reason": "本轮明确要求不要重复查询，实际再次调用了查询工具"}
                if state == "completed":
                    retained_queries = available_queries
                turn_traces.append({"turn": number, "tools": turn_tools,
                                    "repeated_successful_query_signatures": duplicate_queries})
                rows.append({"id": key, "cohort": "industry", "family": item["industry"], "turn": number,
                             "title": item["title"] + f" / 第{number}轮", "task": task,
                             "execution_status": state, "model_ids": turn_mids, "code_syntax": syntax,
                             "outcome": outcome, "source": str(path.relative_to(folder))})
            histories.append({"id": item["id"], "models": mids, "tool_calls": tools,
                              "switches": sum(a != b for a, b in zip(mids, mids[1:], strict=False)),
                              "cache_read_tokens": cache_read, "turn_traces": turn_traces})
        failed_routes = [read(p) for p in (root / "route-errors").glob("*.gz")]
        all_routes = routes + failed_routes
        calls = [c for r in all_routes for c in r["gpu_calls"]]
        gpu_ok = [c for c in calls if c["status"] == "completed"]
        costs = [o["observed_reference_cost_usd"] for o in outputs if o.get("observed_reference_cost_usd") is not None]
        legacy = [r for r in rows if r["cohort"] != "holdout"]
        base = [r for r in rows if r["cohort"] == "base"]
        result["arms"][arm] = {
            "all159": stats(rows), "existing143": stats(legacy),
            "cohorts": {g: stats([r for r in rows if r["cohort"] == g])
                        for g in ("base", "additional", "industry", "holdout")},
            "base_breakdown": {
                "text_code_retrieval": stats([r for r in base if r["family"] not in MEDIA | {"文档与复杂输入", "输入与约束", "系统可靠性"}]),
                "multimodal": stats([r for r in base if r["family"] in MEDIA]),
                "documents": stats([r for r in base if r["family"] == "文档与复杂输入"]),
                "constraints_system": stats([r for r in base if r["family"] in {"输入与约束", "系统可靠性"}]),
            },
            "industry_phases": {str(n): stats([r for r in rows if r["cohort"] == "industry" and r["turn"] == n])
                                for n in range(1, 5)},
            "routes_recorded": len(all_routes), "gpu_attempts": len(calls), "gpu_completed": len(gpu_ok),
            "gpu_one_forward_calls": sum(c["result"]["timings"].get("forward_passes") == 1 for c in gpu_ok),
            "route_call_counts": dict(Counter(len(r["gpu_calls"]) for r in all_routes)),
            "gpu_stages": {str(n): {
                "http_elapsed_ms": distribution([c["elapsed_ms"] for c in gpu_ok if c["number"] == n]),
                "service_reported_ms": distribution([c["result"]["timings"]["service_reported_ms"]
                    for c in gpu_ok if c["number"] == n and c["result"]["timings"].get("service_reported_ms") is not None]),
                "input_tokens": distribution([c["result"]["usage"]["input_tokens"]
                    for c in gpu_ok if c["number"] == n]),
                "questions": dict(Counter(len(c["payload"]["questions"]) for c in gpu_ok if c["number"] == n)),
            } for n in (1, 2)},
            "experiment_admission_queue_ms": distribution([r["experiment_queue_ms"] for r in all_routes]),
            "eligible_candidates": distribution([r["selection"]["candidate_count"] for r in routes]),
            "gpu_evaluated_configurations": distribution([r["selection"]["evaluated_configurations"]
                for r in routes if "evaluated_configurations" in r["selection"]]),
            "route_p50_ms": percentile([r["route_ms"] for r in all_routes], .5),
            "route_p95_ms": percentile([r["route_ms"] for r in all_routes], .95),
            "gpu_service_total_p50_ms": percentile([sum(c.get("result", {}).get("timings", {}).get("service_reported_ms") or 0
                 for c in r["gpu_calls"]) for r in routes], .5),
            "target_observations": len(outputs), "target_p50_ms": percentile([o["backend_total_ms"] for o in outputs], .5),
            "target_p95_ms": percentile([o["backend_total_ms"] for o in outputs], .95),
            "known_reference_cost_usd": sum(float(c) for c in costs), "cost_known_observations": len(costs),
            "selected_model_counts": dict(Counter(r["selection"].get("selected_model_id") for r in routes
                                                 if r["selection"].get("selected_model_id"))),
            "industry_histories": histories, "results": rows,
        }
        assert len(rows) == 159
    for a, b in [("two_stage", "joint"), ("two_stage", "cpu_policy"), ("joint", "cpu_policy")]:
        left = {r["id"]: r for r in result["arms"][a]["results"]}
        right = {r["id"]: r for r in result["arms"][b]["results"]}
        counts = Counter()
        differences = []
        for key, row in left.items():
            x, y = row["outcome"]["status"], right[key]["outcome"]["status"]
            if x in {"pass", "fail"} and y in {"pass", "fail"}:
                counts[x + "/" + y] += 1
                if x != y:
                    differences.append({"id": key, a: x, b: y})
            else:
                counts["pending_or_unmeasured"] += 1
        result["pairwise"][a + "_vs_" + b] = {"counts": dict(counts), "differences": differences}
    result["summary_sha256"] = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.input)
    save(args.output, result)
    print(json.dumps({a: r["all159"] for a, r in result["arms"].items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
