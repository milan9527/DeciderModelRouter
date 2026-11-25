"""Compare two completed effect runs; preserve observed failures and coverage."""

import argparse
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from app.gateway_effect_judge import (
    OVERALL_METRIC_VERSION,
    effective_grade,
    overall_outcome,
    summarize,
)


def observations(job):
    unique = {}
    for row in job["results"]:
        if row.get("observation_id"):
            unique.setdefault(row["observation_id"], row)
    known = [
        Decimal(r["output"]["observed_reference_cost_usd"])
        for r in unique.values()
        if (r.get("output") or {}).get("observed_reference_cost_usd") is not None
    ]
    return {
        "target_attempts": job["target_model_calls"],
        "unique_observations": len(unique),
        "known_price_observations": len(known),
        "known_reference_cost_usd": str(sum(known, Decimal(0))),
    }


def row_outcome(row):
    output = row.get("output") or {}
    usage = output.get("usage") or {}
    return {
        "row_id": row["id"], "execution_status": row["status"],
        "grade": effective_grade(row)["status"],
        "overall": overall_outcome(row),
        "review_method": row.get("review", {}).get("method"),
        "selected_model": (row.get("selected_model") or {}).get("id"),
        "configuration_id": row.get("configuration_id"),
        "output_tokens": usage.get("outputTokens"),
        "effective_max_output_tokens": output.get("effective_max_output_tokens"),
        "target_ms": output.get("backend_total_ms"),
        "reference_cost_usd": output.get("observed_reference_cost_usd"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    old, new = [json.loads(p.read_text()) for p in (args.previous, args.current)]
    for job in (old, new):
        if job["status"] != "completed" or len(job["results"]) != job["recorded_rows"]:
            raise ValueError("Both exports must be completed and contain every row")
    old_rows = {(r["provider"], r["case_id"]): r for r in old["results"] if r["arm"] == "routed"}
    new_rows = {(r["provider"], r["case_id"]): r for r in new["results"] if r["arm"] == "routed"}
    if old_rows.keys() != new_rows.keys():
        raise ValueError("Require the same providers and cases; selected follow-ups are not full-suite comparisons")
    old_cases = {c["id"]: c for c in old["cases"]}
    new_cases = {c["id"]: c for c in new["cases"]}
    pairs = []
    for key, current in new_rows.items():
        previous = old_rows[key]
        before_case, after_case = old_cases[key[1]], new_cases[key[1]]
        pairs.append({
            "provider": key[0], "case_id": key[1], "title": after_case["title"],
            "same_task_and_fixture_name": before_case["task"] == after_case["task"]
            and before_case["fixture"] == after_case["fixture"],
            "same_assessed_profile": previous.get("assessed_profile") == current.get("assessed_profile"),
            "same_selected_configuration": bool(current.get("configuration_id"))
            and previous.get("configuration_id") == current.get("configuration_id"),
            "same_target_request_fields": bool(current.get("output")) and bool(previous.get("output"))
            and previous["output"].get("request_fields") == current["output"].get("request_fields"),
            "judge_definition_changed": before_case["judge"] != after_case["judge"],
            "before": row_outcome(previous), "after": row_outcome(current),
        })
    transitions = {
        provider: dict(Counter(
            f"{p['before']['execution_status']}:{p['before']['grade']} -> "
            f"{p['after']['execution_status']}:{p['after']['grade']}"
            for p in pairs if p["provider"] == provider
        ))
        for provider in new["providers"]
    }
    summaries = [summarize(j["results"], j["providers"], len(j["cases"])) for j in (old, new)]
    result = {
        "overall_metric_version": OVERALL_METRIC_VERSION,
        "previous_job": old["id"], "current_job": new["id"],
        "previous_output_cap": old["max_output_tokens"], "current_output_cap": new["max_output_tokens"],
        "versions": {
            k: {"before": old[k], "after": new[k]}
            for k in ("dataset_version", "judge_version", "profile_version", "catalog_version",
                      "ranking_version", "policy_version", "calibration_version")
        },
        "costs": {"before": observations(old), "after": observations(new)},
        "summaries": {"before": summaries[0], "after": summaries[1]},
        "transitions": transitions, "pairs": pairs,
        "overall_transitions": {
            provider: dict(Counter(
                f"{p['before']['overall']['status']} -> {p['after']['overall']['status']}"
                for p in pairs if p["provider"] == provider
            ))
            for provider in new["providers"]
        },
        "limitations": (
            "Visible development cases, single sequential pass per budget, no fixed target generation seed; "
            "shared outputs are not independent replicates. Changed judge definitions and model configurations "
            "are disclosed. A paired improvement does not establish a general causal effect or oracle-optimal routing. "
            "Costs are known reference subtotals, not AWS bills."
        ),
    }
    lines = [
        "# 完整评测：输出预算对照", "",
        f"前批 `{old['id']}`：{old['max_output_tokens']} tokens；"
        f"本批 `{new['id']}`：{new['max_output_tokens']} tokens。", "",
        "同一场景目录和三个决策模型分别重新运行；以下为路由方案的配对结果，"
        "共享目标输出不构成独立重复。旧自动分数及追加评审保留。", "",
        f"整体验收按 `{OVERALL_METRIC_VERSION}` 从两批原始证据重新汇总，原输出、自动评分和追加评审未改写。"
        "分母固定为全部计划场景，通过包括答案通过与预设边界正确拦截；无候选等非预期路由失败计入未通过，"
        "待评和缺口仍留在分母。", "",
        "| 决策模型 | 整体验收通过/全部场景：前→后 | 通过率：前→后 | 已评分答案通过（辅助）：前→后 | 完整答案不合格：前→后 | 输出未完成：前→后 | 调用错误：前→后 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for current in summaries[1]:
        previous = next(s for s in summaries[0] if s["provider"] == current["provider"])
        lines.append(
            f"| {current['provider']} | {previous['overall_pass']}/{previous['overall_total']} → "
            f"{current['overall_pass']}/{current['overall_total']} | "
            f"{previous['overall_pass_rate']:.1%} → {current['overall_pass_rate']:.1%} | "
            f"{previous['quality_pass']}/{previous['quality_graded']} → {current['quality_pass']}/{current['quality_graded']} | "
            f"{previous['answer_failed']} → {current['answer_failed']} | "
            f"{previous['output_incomplete']} → {current['output_incomplete']} | "
            f"{previous['errors']} → {current['errors']} |"
        )
    lines += ["", "## 顺序调用延迟", "",
              "| 决策模型 | 完整路由 P50 ms：前→后 | 完整路由 P95 ms：前→后 | 有结果目标 P50 ms：前→后 | 有结果目标 P95 ms：前→后 |",
              "|---|---:|---:|---:|---:|"]
    for current in summaries[1]:
        previous = next(s for s in summaries[0] if s["provider"] == current["provider"])
        values = []
        for key in ("route_p50_ms", "route_p95_ms", "target_p50_ms", "target_p95_ms"):
            formatted = ["—" if s[key] is None else f"{s[key]:.1f}" for s in (previous, current)]
            values.append(" → ".join(formatted))
        lines.append(f"| {current['provider']} | " + " | ".join(values) + " |")
    lines += ["", "这是两次顺序执行的观测分位数，不代表并发吞吐或 SLA；"
              "目标延迟只统计返回结果中具有耗时的数据，不能将未知耗时的失败补零。"]
    lines += ["", "## 任务结果发生变化的用例", "",
              "| 场景 | 决策模型 | 前→后 | 目标配置相同 |", "|---|---|---|---|"]
    labels = {"pass": "通过", "fail": "未通过", "pending": "待评", "unmeasured": "未测"}
    for pair in pairs:
        before, after = pair["before"], pair["after"]
        if (before["execution_status"], before["overall"]["status"]) == (after["execution_status"], after["overall"]["status"]):
            continue
        transition = (
            f"{labels[before['overall']['status']]}（{before['execution_status']}）→ "
            f"{labels[after['overall']['status']]}（{after['execution_status']}）"
        )
        lines.append(f"| {pair['title']} | {pair['provider']} | {transition} | "
                     f"{'是' if pair['same_selected_configuration'] else '否或无目标'} |")
    lines += ["", "## 实际调用及参考费用", "",
              "| 批次 | 独立目标观察 | 价格已知观察 | 已知参考费用 USD |", "|---|---:|---:|---:|"]
    for name, cost in result["costs"].items():
        lines.append(f"| {name} | {cost['unique_observations']} | {cost['known_price_observations']} | {cost['known_reference_cost_usd']} |")
    lines += ["", "费用按实际独立观察去重，包含低价/高能力对照；不把三个路由方案的共享费用相加。"
              "未知价格、未知调用用量和 GPU/ECS 等固定费用未补零，不能据此计算完整账单或通用节省比例。", "",
              "## 比较边界", "",
              "没有固定目标生成随机种子或多次重复采样。配置变化、内容随机变化、判分器变化与预算差异应分别考虑；"
              "配对改善不等于预算增大的普遍因果效果。概率评分器和视频参考在两套件之间有已公开修正，"
              "旧批次的相应追加评审和勘误保留。", "",
              "批次上限不是每种适配器的实际上限：Sonic 语音仍限制为 1024，"
              "其输出记录包含 effective_max_output_tokens；图片编辑与向量任务不使用普通文本输出预算。"
              "因此不能把所有媒体变化归因于 16384-token 设置。", "",
              "完整配对字段、版本、分组统计见 [comparison.json](comparison.json)。", ""]
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    (args.output / "README.zh.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
