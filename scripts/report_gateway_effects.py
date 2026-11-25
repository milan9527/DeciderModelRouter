"""Render the saved evaluation, including missing coverage, without inventing scores."""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from app.gateway_effect_judge import (
    OVERALL_METRIC_VERSION,
    effective_grade,
    overall_outcome,
    summarize,
)
from app.gateway_store import percentile


def rate(value):
    return "未测" if value is None else f"{value * 100:.1f}%"


def ms(value):
    return "—" if value is None else f"{value:.1f}"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--evaluation", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    j = json.loads(args.evaluation.read_text())
    j["summary"] = summarize(j["results"], j["providers"], len(j["cases"]))
    routed = [r for r in j["results"] if r["arm"] == "routed"]
    unique = {}
    for row in j["results"]:
        if row.get("observation_id"):
            unique.setdefault(row["observation_id"], row)
    known = [float(r["output"]["observed_reference_cost_usd"]) for r in unique.values()
             if r.get("output", {}).get("observed_reference_cost_usd") is not None]
    unique_statuses = Counter(r["status"] for r in unique.values())
    families = defaultdict(list)
    for c in j["cases"]:
        families[c["family"]].append(c)
    lines = [
        "# 全场景路由效果实测报告", "",
        f"任务 `{j['id']}`；场景版本 `{j['dataset_version']}`；状态 `{j['status']}`。", "",
        f"{len(j['cases'])} 个场景、{len(families)} 类、{len(j['providers'])} 种决策模型；"
        f"GPU调用尝试 {j['started_gpu_calls']}/{j['planned_gpu_calls']}；"
        f"Bedrock目标调用尝试 {j['target_model_calls']}/{j['max_target_calls']}，共享目标结果 {j['reused_observations']} 次。", "",
        f"批次请求的目标输出上限 {j['max_output_tokens']} tokens；"
        f"专用适配器可采用更低上限，以实际输出记录为准。{j['note']}", "",
        "**这是公开开发回归集，不是隐藏测试集。** 每种情况的样本量有限，"
        "共享输出也不是独立重复。整体验收比例必须与待评、未测和评审来源一起阅读。", "",
        f"## 整体验收（{OVERALL_METRIC_VERSION}）", "",
        "| 决策模型 | 整体验收通过/全部场景 | 通过率 | 答案通过 | 正确拦截 | 未通过 | 待评 | 未测 | 已判定覆盖率 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for s in j["summary"]:
        lines.append(f"| {s['provider']} | {s['overall_pass']}/{s['overall_total']} | {rate(s['overall_pass_rate'])} | "
                     f"{s['overall_answer_pass']} | {s['overall_boundary_pass']} | {s['overall_fail']} | "
                     f"{s['overall_pending']} | {s['overall_unmeasured']} | {rate(s['overall_coverage'])} |")
    lines += [
        "", "统一使用全部计划场景作分母，包括待评、预登记缺口、预算未执行和尚无记录。"
        "四类互斥：通过 + 未通过 + 待评 + 未测 = 全部场景。通过包括完整答案通过及预设边界正确拦截；"
        "错误路由无候选/执行受阻计入未通过。待评与缺口没有被判错，也不当作已通过。"
        "正确拦截只依据冻结用例的 boundary_check，不因一次无路由而临时认定合理。", "",
        "| 决策模型 | 非预期无路由/执行受阻 | 边界违约 | 预登记缺口 | 预算未执行 | 尚无记录 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for s in j["summary"]:
        lines.append(f"| {s['provider']} | {s['overall_routing_failed']} | {s['overall_boundary_failed']} | "
                     f"{s['capability_gaps']} | {s['budget_skipped']} | {s['not_recorded']} |")
    lines += [
        "", "## 答案质量与分类诊断（辅助）", "",
        "| 决策模型 | 输出完成/全部场景 | 任务类型判断正确/已检查 | 已评分答案通过（辅助） | 子集通过率 | 完整答案不合格 | 输出未完成 | 答案评分覆盖率 | 待评 | 无候选/缺口/未接通（旧字段） | 调用失败 | 预算未测 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for s in j["summary"]:
        lines.append(f"| {s['provider']} | {s['target_completed']}/{s['planned']} | "
                     f"{s['workflow_fit']}/{s['workflow_checked']} | {s['quality_pass']}/{s['quality_graded']} | {rate(s['quality_pass_rate'])} | "
                     f"{s['answer_failed']} | {s['output_incomplete']} | "
                     f"{rate(s['quality_coverage'])} | {s['pending']} | {s['gaps']} | {s['errors']} | {s['budget_skipped']} |")
    lines += ["", "已评分答案是辅助指标，各模型分母可能不同，不用于整体排名。旧字段“无候选/缺口”"
              "保留兼容性，包含预期拒绝；新主指标已经区分正确拦截、路由失败及预登记缺口。", "",
              "任务类型判断正确表示识别的用途与请求匹配，例如没有把文字问候误分为语音生成；"
              "这是诊断项，不因内部标签不同而否决已通过的完整答案，也不代表所选型号最优。完整答案不合格与输出未完成分别计数；"
              "截断是该预算内未完成交付，不等于模型答错。历史原始判分不变。", "",
              "## 延迟与成本", "",
              "| 决策模型 | GPU分类往返 P50/P95 ms | 完整路由 P50/P95 ms | 有结果目标调用 P50/P95 ms | 路由方案已知参考费用 USD | 价格已知/目标观察 |",
              "|---|---:|---:|---:|---:|---:|"]
    for s in j["summary"]:
        latencies = [r.get("classifier_timings", {}).get("upstream_ms") for r in routed if r["provider"] == s["provider"]]
        cost = f"{s['known_reference_cost_usd']:.6f}" if s["cost_known_samples"] else "未知"
        lines.append(f"| {s['provider']} | {ms(percentile(latencies, .5))}/{ms(percentile(latencies, .95))} | "
                     f"{ms(s['route_p50_ms'])}/{ms(s['route_p95_ms'])} | {ms(s['target_p50_ms'])}/{ms(s['target_p95_ms'])} | "
                     f"{cost} | {s['cost_known_samples']}/{s['target_observations']} |")
    lines += ["", f"按去重后的目标观察计，本批有 {len(unique)} 个记录，其中 {len(known)} 个有完整用量与参考价格；"
              f"已知部分参考费用合计 **${sum(known):.6f}**。"
              "未返回结果、未知价格及GPU/ECS/存储/网络费用未补零；这不是AWS账单或完整每成功任务成本。"
              "上表三行描述不同路由方案，含共享观察，不可直接相加。", "",
              f"包括低价/高能力对照在内，独立目标观察共有："
              f"完整输出 {unique_statuses['completed']} 个、输出未完成 {unique_statuses['incomplete']} 个、"
              f"调用错误 {unique_statuses['error']} 个。完整输出不等于质量通过；"
              "对照异常不能从路由方案的零错误推断为不存在。", "",
              "延迟来自顺序执行；目标分位数仅使用返回结果中带有耗时的观察，不含无法取回耗时的失败。"
              "GPU往返含服务处理和传输，不是纯CUDA耗时。", "",
              "## 低价与高能力对照", "",
              "| 决策模型 | 有已评分对照的场景 | 实际失败而已测对照通过 | 双方通过且价格可比 | 存在更便宜的已测通过方案 | 高于该方案的参考费用 USD |",
              "|---|---:|---:|---:|---:|---:|"]
    for s in j["summary"]:
        lines.append(f"| {s['provider']} | {s['paired_quality_cases']} | {s['avoidable_failures_vs_tested_baselines']} | "
                     f"{s['cost_comparable_passing_pairs']} | {s['lower_cost_passing_alternative_cases']} | "
                     f"{s['reference_cost_above_tested_passing_alternative_usd']:.6f} |")
    lines += ["", "仅相对于本次已测低价/高能力策略比较，不代表遍历全部模型后的最优路由。"
              "待评分、未知价格及相同观察复用均有记录，不能据此虚构全局节省比例。", "",
              "## 评分来源", "",
              "| 决策模型 | 明确答案自动检查/执行失败评分 | 用户评审 | 助手辅助评审 | 隔离代码测试 |",
              "|---|---:|---:|---:|---:|"]
    for s in j["summary"]:
        methods = s["review_methods"]
        automatic = sum(r["provider"] == s["provider"] and not r.get("review")
                        and effective_grade(r)["status"] in {"pass", "fail"} for r in routed)
        lines.append(f"| {s['provider']} | {automatic} | {methods['user_review']} | {methods['assistant_review']} | {methods['isolated_tests']} |")
    if j.get("reference_errata"):
        lines += ["", "### 参考勘误（原始快照保留）", ""]
        for erratum in j["reference_errata"]:
            lines += [f"- `{erratum['case_id']}`：{erratum['corrected']} {erratum['basis']}"]
    lines += ["", "助手辅助评审是本次助手依据输出和验收规则的内容审阅，并非独立人工金标。"
              "代码记录包含具体测试数、源代码SHA-256与隔离镜像；转写/颜色/OCR等按冻结参考自动判断。", "",
              "## 全场景覆盖", "",
              "| 分类 | 场景 | 记录方式 |", "|---|---|---|"]
    for family, cases in families.items():
        lines.append(f"| {family} | {'、'.join(c['title'] for c in cases)} | "
                     f"{len(cases)}个用例；{sum(bool(c['gap']) for c in cases)}个预先登记缺口 |")
    labels = {"pass": "通过", "fail": "失败", "pending": "待评", "unmeasured": "未测"}
    states = {"no_route": "无候选", "execution_blocked": "执行未接通", "capability_gap": "能力/数据缺口",
              "budget_exhausted": "预算未测", "error": "调用失败", "incomplete": "截断"}
    lines += ["", "## 逐用例结果", "",
              "| 场景 | " + " | ".join(j["providers"]) + " |",
              "|---|" + "---|" * len(j["providers"])]
    for c in j["cases"]:
        cells = []
        for provider in j["providers"]:
            row = next((r for r in routed if r["case_id"] == c["id"] and r["provider"] == provider), None)
            if not row:
                cells.append("尚无记录")
                continue
            outcome = overall_outcome(row)
            text = "整体" + labels[outcome["status"]]
            if row["status"] == "incomplete":
                text += " · 输出未完成（含截断）"
            elif row.get("output", {}).get("complete") and effective_grade(row)["status"] == "fail":
                text += " · 完整答案不合格"
            elif row["status"] in states:
                text += " · " + states[row["status"]]
            if row.get("boundary_check") is not None:
                text += " · 边界检查" + ("通过" if row["boundary_check"] else "失败")
            cells.append(text)
        lines.append(f"| {c['title']} (`{c['id']}`) | " + " | ".join(cells) + " |")
    lines += ["", "## 目标模型覆盖", "",
              "| 实际目标模型ID | 独立目标观察数 |", "|---|---:|"]
    counts = Counter(r.get("output", {}).get("model_id") or (r.get("selected_model") or {}).get("id", "未返回型号")
                     for r in unique.values())
    for model, count in counts.most_common():
        lines.append(f"| `{model}` | {count} |")
    lines += ["", "完整输出、实际配置、错误、参考价格、评审证据和共享观察ID见同目录 `evaluation.json`。"
              "总体方法与后续正式基准要求见 [评测说明](../../../docs/effectiveness-evaluation.zh.md)。", ""]
    args.output.write_text("\n".join(lines))


if __name__ == "__main__":
    main()
