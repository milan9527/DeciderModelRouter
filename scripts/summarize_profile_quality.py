"""Aggregate planned denominators, execution, functional checks and reviewed quality."""

import argparse
import ast
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from app.gateway_store import percentile
from scripts.run_profile_quality import read, save

MEDIA = {"图像理解", "音频与语音", "视频理解", "图像生成与编辑", "视频生成", "复合多模态"}


def code_syntax(text):
    checks = []
    for index, code in enumerate(re.findall(r"```(?:python|py)\s*\n(.*?)```", text, re.S)):
        try:
            ast.parse(code)
            checks.append({"block": index + 1, "status": "valid_syntax"})
        except SyntaxError as exc:
            checks.append({"block": index + 1, "status": "invalid_syntax",
                           "line": exc.lineno, "message": exc.msg})
    return checks


def build(folder):
    manifest = read(folder / "manifest.json")
    audits = read(folder / "assistant-audits.json") if (folder / "assistant-audits.json").exists() else {}
    reviews = {p.name.removesuffix(".json.gz"): read(p) for p in (folder / "reviews").glob("*.json.gz")}
    code = {r["case_id"]: r for r in read(folder / "code-results.json")}
    functional = {r["case_id"]: r for r in read(folder / "code-content-results.json")}
    results, routes, outputs, industry_reports = [], [], [], []

    def reviewed(key, initial):
        if key in audits:
            return audits[key]
        if key in reviews:
            r = reviews[key]
            return {**r.get("review", {"status": "pending"}), "method": r["method"]}
        return initial

    for group, cases in (("base", manifest["base_cases"]), ("additional", manifest["additional_cases"])):
        for case in cases:
            path = folder / group / (case["id"] + ".json.gz")
            row = read(path) if path.exists() else {"status": "not_recorded"}
            state = row["status"]
            info = {"id": f"{group}-{case['id']}", "case_id": case["id"], "cohort": group,
                    "family": case["family"], "title": case["title"], "execution_status": state,
                    "row_file": str(path.relative_to(folder))}
            route = row.get("route")
            if route:
                routes.append(route)
                info.update(model_id=route["selection"]["selected_model_id"],
                            profile=route["profile"], assessed_profile=route["assessed_profile"],
                            route_ms=route["route_ms"], workflow_fit=row.get("workflow_fit"))
            output = row.get("output")
            if output:
                outputs.append(output)
                info.update(target_ms=output["backend_total_ms"],
                            reference_cost_usd=output.get("observed_reference_cost_usd"))
            if row.get("boundary_check") is True:
                verdict = {"status": "pass", "kind": "expected_boundary", "reason": "预登记输入或费用约束正确拦截"}
            elif state in {"capability_gap", "not_recorded", "started"}:
                verdict = {"status": "unmeasured", "kind": state, "reason": case.get("gap") or "No completed observation"}
            elif state != "completed":
                verdict = {"status": "fail", "kind": state,
                           "reason": str(row.get("error") or row.get("grade"))}
            else:
                initial = row.get("grade", {"status": "pending"})
                if case["id"] in code:
                    verdict = {"status": code[case["id"]]["status"], "kind": "code_contract",
                               "reason": "原始输出的隔离测试；代码围栏不符合裸代码要求",
                               "method": "isolated_tests"}
                    info["functional_check"] = {
                        k: functional[case["id"]][k] for k in ("status", "result", "source_sha256")
                    }
                elif case["id"] in {"tts", "tts-zh", "image-edit"}:
                    media_path = folder / "media-reviews" / (case["id"] + ".json")
                    initial = read(media_path) if media_path.exists() else initial
                    verdict = reviewed(info["id"], initial)
                elif initial["status"] in {"pass", "fail"}:
                    verdict = initial
                else:
                    verdict = reviewed(info["id"], initial)
            info["outcome"] = verdict
            results.append(info)

    for item in manifest["industry_scenarios"]:
        path = folder / "industries" / (item["id"] + ".json.gz")
        report = read(path) if path.exists() else {}
        turns = {t["number"]: t for t in report.get("turns", [])}
        previous = None
        switches, calls, cache_reads, models_by_turn = 0, 0, 0, []
        for number, _text in enumerate(item["turns"], 1):
            turn = turns.get(number, {"status": "not_recorded"})
            cid = f"industry-{item['id']}-{number}"
            models = []
            for step in turn.get("steps", []):
                route = step["route"]
                routes.append(route)
                mid = route["selection"]["selected_model_id"]
                if mid:
                    models.append(mid)
                    switches += int(previous is not None and previous != mid)
                    previous = mid
                    calls += 1
                if step.get("output"):
                    outputs.append(step["output"])
                    cache_reads += (step["output"].get("cache") or {}).get("read_tokens", 0)
            models_by_turn.append({"turn": number, "models": models})
            syntax = code_syntax(turn.get("answer", "")) if number == 3 else []
            if turn["status"] == "completed":
                verdict = reviewed(cid, {"status": "pending", "reason": "Business review missing"})
                invalid = [c for c in syntax if c["status"] == "invalid_syntax"]
                if invalid:
                    verdict = {"status": "fail", "method": "python_syntax_check",
                               "reason": "回答中的Python代码块存在语法错误", "syntax_errors": invalid}
            elif turn["status"] in {"dependency_blocked", "not_recorded", "started"}:
                verdict = {"status": "unmeasured", "reason": turn["status"]}
            else:
                verdict = {"status": "fail", "kind": turn["status"], "reason": turn.get("error", turn["status"])}
            results.append({
                "id": cid, "cohort": "industry", "family": item["industry"],
                "title": item["title"] + f" / 第{number}轮", "case_id": cid,
                "execution_status": turn["status"], "outcome": verdict,
                "model_ids": models, "code_syntax": syntax, "row_file": str(path.relative_to(folder)),
            })
        industry_reports.append({"id": item["id"], "industry": item["industry"],
                                 "model_calls": calls, "selected_model_switches": switches,
                                 "observed_cache_read_tokens": cache_reads,
                                 "models_by_turn": models_by_turn})

    def stats(sample):
        counts = Counter(r["outcome"]["status"] for r in sample)
        return {"planned": len(sample), "passed": counts["pass"], "failed": counts["fail"],
                "pending": counts["pending"], "unmeasured": counts["unmeasured"],
                "pass_rate": counts["pass"] / len(sample) if sample else None,
                "completed_outputs": sum(r["execution_status"] == "completed" for r in sample)}

    groups = {g: stats([r for r in results if r["cohort"] == g]) for g in ("base", "additional", "industry")}
    base = [r for r in results if r["cohort"] == "base"]
    breakdown = {
        "文本、代码与检索": stats([r for r in base if r["family"] not in MEDIA | {"文档与复杂输入", "输入与约束", "系统可靠性"}]),
        "多模态": stats([r for r in base if r["family"] in MEDIA]),
        "文档与复杂输入": stats([r for r in base if r["family"] == "文档与复杂输入"]),
        "约束与系统": stats([r for r in base if r["family"] in {"输入与约束", "系统可靠性"}]),
    }
    known_costs = [float(o["observed_reference_cost_usd"]) for o in outputs
                   if o.get("observed_reference_cost_usd") is not None]
    model_counts = Counter(r["selection"]["selected_model_id"] for r in routes
                           if r["selection"]["selected_model_id"])
    family_groups = {f: stats([r for r in results if r["family"] == f])
                     for f in dict.fromkeys(r["family"] for r in results)}
    completed_answers = [r for r in results if r["execution_status"] == "completed"]
    result = {
        "suite_version": manifest["version"], "policy_version": manifest["policy_version"],
        "policy_sha256": manifest["policy_sha256"], "total": stats(results),
        "cohorts": groups, "base_breakdown": breakdown, "families": family_groups,
        "completed_answer_quality": stats(completed_answers),
        "industry_phases": {
            str(n): stats([r for r in results if r["cohort"] == "industry" and r["id"].endswith(f"-{n}")])
            for n in range(1, 5)
        },
        "routing_observations": len(routes),
        "one_forward_observations": sum(r["gpu"]["timings"].get("forward_passes") == 1 for r in routes),
        "route_p50_ms": percentile([r["route_ms"] for r in routes], .5),
        "route_p95_ms": percentile([r["route_ms"] for r in routes], .95),
        "route_timing_boundary": "workspace CPU, actual pool construction, SSM tunneled HTTP; not production ECS or GPU-host-only latency",
        "gpu_service_p50_ms": percentile([r["gpu"]["timings"]["service_reported_ms"] for r in routes], .5),
        "gpu_service_p95_ms": percentile([r["gpu"]["timings"]["service_reported_ms"] for r in routes], .95),
        "cpu_policy_p50_ms": percentile([r["cpu_policy_ms"] for r in routes], .5),
        "cpu_policy_p95_ms": percentile([r["cpu_policy_ms"] for r in routes], .95),
        "pool_p50_ms": percentile([r["pool_ms"] for r in routes], .5),
        "pool_p95_ms": percentile([r["pool_ms"] for r in routes], .95),
        "target_observations": len(outputs),
        "known_reference_cost_usd": sum(known_costs), "cost_known_observations": len(known_costs),
        "target_p50_ms": percentile([o.get("backend_total_ms") for o in outputs], .5),
        "target_p95_ms": percentile([o.get("backend_total_ms") for o in outputs], .95),
        "model_selection_counts": dict(model_counts), "industries": industry_reports,
        "functional_code": {k: {"raw_contract": code[k]["status"],
                                "extracted_code": v["status"], "result": v["result"]}
                            for k, v in functional.items()},
        "additional_code_diagnostics": read(folder / "fresh-code-results.json"),
        "review_methods": dict(Counter(r["outcome"].get("method", r["outcome"].get("source", "execution_or_gap"))
                                      for r in results)),
        "limitations": [
            "visible development suite and synthetic industry records; not a hidden production sample",
            "one output per case/turn, correlated conversation turns; no statistical accuracy guarantee",
            "LLM-assisted rubric judgments with explicit assistant audits, not human expert certification",
            "industry code syntax/static reviews do not prove functional or concurrent correctness",
            "12 preregistered capability/data/system gaps stay in the denominator",
            "industry execution uses local native adapter and tools, not deployed AgentCore Runtime V2",
            "no production architecture change, no failure/latency retries of target calls",
        ],
        "results": results,
    }
    assert len(results) == 143
    result["summary_sha256"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.input)
    save(args.output, result)
    print(result["total"])
    print(result["cohorts"])
    print(result["base_breakdown"])


if __name__ == "__main__":
    main()
