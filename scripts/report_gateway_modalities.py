"""Reproduce the modality report for the frozen 16384-token evaluation."""

import argparse
import hashlib
import json
import os
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GROUPING_VERSION = "task-modality-v1"
SOURCE_SHA256 = "187b310813a31fe8b762d605351d27692af667e9c1a9eda269fda1cfde62d6b3"
GROUPS = {
    "text": {
        "label": "文本、代码与检索",
        "families": [
            "通用对话",
            "指令与边界",
            "知识与事实",
            "翻译与多语言",
            "文本处理",
            "抽取与格式",
            "创作与规划",
            "数学与推理",
            "数据与分析",
            "代码与工程",
            "客服与业务",
            "专业与安全",
            "给定资料问答",
            "向量与重排",
            "工具与结构化接口",
            "多轮与长上下文",
        ],
    },
    "multimodal": {
        "label": "图像、音频、视频与复合多模态",
        "families": [
            "图像理解",
            "音频与语音",
            "视频理解",
            "图像生成与编辑",
            "视频生成",
            "复合多模态",
        ],
    },
    "documents": {"label": "文档与复杂文件", "families": ["文档与复杂输入"]},
    "system": {"label": "约束与系统", "families": ["输入与约束", "系统可靠性"]},
}
PROVIDER_NAMES = {"decider": "Decider", "liquid": "Liquid", "h2o": "H2O"}
STATUS_NAMES = {"pass": "通过", "fail": "未通过", "pending": "待评", "unmeasured": "未测"}


def ratio(numerator, denominator):
    if not denominator:
        return "未测（0/0）"
    percentage = (Decimal(numerator) * 100 / Decimal(denominator)).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )
    return f"{numerator}/{denominator}（{percentage}%）"


def summarize(rows, cases):
    outcomes = Counter(r["overall"]["status"] for r in rows)
    kinds = Counter(r["overall"]["kind"] for r in rows)
    raw = []
    effective = []
    grades = []
    for row in rows:
        case = cases[row["case_id"]]
        allowed = case["allowed_workflows"]
        if allowed and row.get("assessed_profile", {}).get("workflow") is not None:
            raw.append(row["assessed_profile"]["workflow"] in allowed)
        if allowed and row.get("profile", {}).get("workflow") is not None:
            match = row["profile"]["workflow"] in allowed
            if match != row.get("workflow_fit"):
                raise ValueError(f"Stored workflow_fit differs: {row['id']}")
            effective.append(match)
        grades.append(row.get("review") or row.get("grade", {}))
    return {
        "planned": len(cases),
        **{name: outcomes[name] for name in STATUS_NAMES},
        "pass_rate": outcomes["pass"] / len(cases) if cases else None,
        "answer_pass": kinds["answer_pass"],
        "boundary_pass": kinds["expected_boundary"],
        "outcome_kinds": dict(sorted(kinds.items())),
        "raw_workflow_match": sum(raw),
        "raw_workflow_checked": len(raw),
        "effective_workflow_match": sum(effective),
        "effective_workflow_checked": len(effective),
        "target_completed": sum(r.get("output", {}).get("complete") is True for r in rows),
        "quality_pass": sum(g.get("status") == "pass" for g in grades),
        "quality_graded": sum(g.get("status") in {"pass", "fail"} for g in grades),
    }


def build_report(source):
    source_bytes = source.read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != SOURCE_SHA256:
        raise ValueError(
            "This report includes batch-specific analysis and requires the frozen source SHA-256; "
            "review and version the analysis before applying it to another export"
        )
    job = json.loads(source_bytes)
    if job.get("overall_metric_version") != "end-to-end-v1":
        raise ValueError("This grouping report requires an end-to-end-v1 export")
    cases = {c["id"]: c for c in job["cases"]}
    if len(cases) != len(job["cases"]):
        raise ValueError("Duplicate case IDs")
    family_groups = {family: group for group, spec in GROUPS.items() for family in spec["families"]}
    unknown = {c["family"] for c in cases.values()} - family_groups.keys()
    if unknown:
        raise ValueError(
            f"Unmapped families; update the versioned grouping explicitly: {sorted(unknown)}"
        )
    rows = [r for r in job["results"] if r["arm"] == "routed"]
    expected = {(provider, case_id) for provider in job["providers"] for case_id in cases}
    actual = [(r["provider"], r["case_id"]) for r in rows]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError(
            "A completed export must contain one routed row per provider and planned case"
        )
    if any(r.get("overall", {}).get("status") not in STATUS_NAMES for r in rows):
        raise ValueError("Missing or unsupported overall outcome")

    def summaries(subset):
        return {
            provider: summarize(
                [r for r in rows if r["provider"] == provider and r["case_id"] in subset], subset
            )
            for provider in job["providers"]
        }

    groups = {}
    for key, spec in GROUPS.items():
        subset = {cid: c for cid, c in cases.items() if family_groups[c["family"]] == key}
        groups[key] = {
            **spec,
            "case_ids": list(subset),
            "summary": summaries(subset),
            "family_summary": {
                family: summaries({cid: c for cid, c in subset.items() if c["family"] == family})
                for family in spec["families"]
                if any(c["family"] == family for c in subset.values())
            },
        }
    totals = summaries(cases)
    for provider, total in totals.items():
        for field in ("planned", *STATUS_NAMES, "answer_pass", "boundary_pass"):
            if sum(group["summary"][provider][field] for group in groups.values()) != total[field]:
                raise ValueError(f"Groups do not reconcile: {provider}/{field}")
        original = next(s for s in job["summary"] if s["provider"] == provider)
        for field in STATUS_NAMES:
            if total[field] != original[f"overall_{field}"]:
                raise ValueError(f"Grouped counts differ from original summary: {provider}/{field}")
    return {
        "source": {
            "path": source.relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
            "job_id": job["id"],
            "dataset_version": job["dataset_version"],
            "overall_metric_version": job["overall_metric_version"],
            "max_output_tokens": job["max_output_tokens"],
            "exported_rows": len(job["results"]),
            "routed_rows": len(rows),
        },
        "grouping_version": GROUPING_VERSION,
        "providers": job["providers"],
        "totals": totals,
        "groups": groups,
        "cases": [
            {
                "id": case["id"],
                "title": case["title"],
                "family": case["family"],
                "group": family_groups[case["family"]],
                "gap": case["gap"],
                "allowed_workflows": case["allowed_workflows"],
                "results": {
                    r["provider"]: {
                        "row_id": r["id"],
                        "overall": r["overall"],
                        "raw_workflow": r.get("assessed_profile", {}).get("workflow"),
                        "effective_workflow": r.get("profile", {}).get("workflow"),
                        "workflow_fit": r.get("workflow_fit"),
                        "observation_id": r.get("observation_id"),
                    }
                    for r in rows
                    if r["case_id"] == case["id"]
                },
            }
            for case in cases.values()
        ],
        "new_gpu_calls": 0,
        "new_bedrock_calls": 0,
    }


def markdown(report, output_dir):
    source = report["source"]
    providers = report["providers"]
    names = [PROVIDER_NAMES.get(p, p) for p in providers]
    source_link = Path(os.path.relpath(ROOT / source["path"], output_dir)).as_posix()
    method_link = Path(
        os.path.relpath(ROOT / "docs/effectiveness-evaluation.zh.md", output_dir)
    ).as_posix()
    lines = [
        "# 文本与多模态测试指标",
        "",
        f"依据[原始导出]({source_link})，批次 `{source['job_id']}`，场景版本 "
        f"`{source['dataset_version']}`，验收口径 `{source['overall_metric_version']}`，"
        f"分组版本 `{report['grouping_version']}`。请求输出上限为{source['max_output_tokens']} tokens，"
        "各适配器的实际上限以原始记录为准。",
        "",
        f"只统计{source['routed_rows']}条 `arm=routed` 记录（87项×3种方案，含未测占位）；"
        f"导出的{source['exported_rows']}条记录还包含"
        "低价/高能力对照，它们不重复计入分母。本报告复用原判定，没有新增模型调用。",
        "",
        "## 如何分类",
        "",
        "按冻结场景的任务意图分组，不按GPU实际返回的标签、选中型号或成功与否分组。"
        "文字问候被误分成语音，仍计入文本；文字输入要求生成图像或语音，计入多模态。"
        "文件输入和系统约束另外列出，避免把拒绝缺少图片当成识图能力通过。",
        "",
        "| 组别 | 场景数 | 范围 |",
        "|---|---:|---|",
    ]
    for group in report["groups"].values():
        lines.append(
            f"| {group['label']} | {len(group['case_ids'])} | {'、'.join(group['families'])} |"
        )
    lines += [
        "",
        "四组互斥且覆盖全部87项。文本包含尚未接通的工具、多轮和检索质量测试；"
        "多模态包含尚无标注数据的多图、音画同步等测试。它们保留在所属组分母。"
        "这是对既有场景新增的统计分组，不是生产路由规则。",
        "",
        "## 分组整体验收通过率",
        "",
        "通过率 = 本组已通过场景 / 本组全部计划场景。"
        "通过包括答案通过和预设边界正确拦截；下表文本、多模态、文档组没有边界通过，"
        "3次正确拦截均在约束与系统组。",
        "",
        "| 组别 | " + " | ".join(names) + " |",
        "|---|" + "---:|" * len(names),
    ]
    for label, summary in [
        ("全部场景", report["totals"]),
        *[(g["label"], g["summary"]) for g in report["groups"].values()],
    ]:
        lines.append(
            "| "
            + label
            + " | "
            + " | ".join(ratio(summary[p]["pass"], summary[p]["planned"]) for p in providers)
            + " |"
        )
    lines += [
        "",
        "待评和未测未被判错，但不能增加已证实通过比例；0/2表示两项均未测，不表示实际执行两次均失败。",
        "",
        "| 组别 | 模型 | 通过 | 未通过 | 待评 | 未测 | 已判定覆盖率 |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for group in report["groups"].values():
        for p in providers:
            s = group["summary"][p]
            lines.append(
                f"| {group['label']} | {PROVIDER_NAMES.get(p, p)} | {s['pass']} | {s['fail']} | "
                f"{s['pending']} | {s['unmeasured']} | {ratio(s['pass'] + s['fail'], s['planned'])} |"
            )
    lines += [
        "",
        "## 分类、完成与答案通过不是同一个指标",
        "",
        "| 组别 | 模型 | GPU原始类型匹配/已检查 | 后端调整后类型匹配/已检查 | 输出完成/计划场景 | 已评分答案通过（辅助） |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for key in ("text", "multimodal"):
        group = report["groups"][key]
        for p in providers:
            s = group["summary"][p]
            lines.append(
                f"| {group['label']} | {PROVIDER_NAMES.get(p, p)} | "
                f"{ratio(s['raw_workflow_match'], s['raw_workflow_checked'])} | "
                f"{ratio(s['effective_workflow_match'], s['effective_workflow_checked'])} | "
                f"{ratio(s['target_completed'], s['planned'])} | "
                f"{ratio(s['quality_pass'], s['quality_graded'])} |"
            )
    lines += [
        "",
        "- **GPU原始类型匹配**：比较 `assessed_profile.workflow` 与冻结用例的 `allowed_workflows`，"
        "只检查任务类型这一项。87项效果集没有其余六项属性的完整参考标签，不能计算七项全字段准确率。",
        "- **后端调整后类型匹配**：比较 `profile.workflow` 与同一参考；这就是现有前端的"
        " `workflow_fit`。后端会依据实际附件、明确输出类型等调整流程，不能把它全部归功于GPU。",
        "- **输出完成**：目标返回 `complete=true`，不要求质量通过。无候选和正确拦截没有目标输出，"
        "因此不会计入完成数。",
        "- **已评分答案通过（辅助）**：有效评审优先于原始自动评分，只统计已有pass/fail评分的子集。"
        "各模型分母不同，不替代整体验收排名。缺少类型记录和没有评分的样本只从对应诊断分母排除，仍在主指标分母。",
        "",
        "例如：Decider的图表任务原始为speech，后端调整为vision；Decider和H2O的ASR原始为speech，"
        "后端调整为audio_understanding。调整后三者的多模态类型匹配均为11/11，"
        "但最终仅3项答案通过。类型正确不等于媒体内容正确或目标能力可用。",
        "",
        "## 文本指标怎么验收",
        "",
        "| 能力 | 当前验收依据 | 当前边界 |",
        "|---|---|---|",
        "| 对话、摘要、翻译、创作、专业问答 | 冻结任务要求、事实与格式约束；保存具体评审理由 | 开放题含助手辅助评审，不是独立人工金标 |",
        "| 数学、抽取、JSON与资料问答 | 精确值、等价数值/分数、结构、给定证据和禁止的额外输出 | 只证明对应样本满足规则，不是领域总体准确率 |",
        "| 代码与SQL | 按任务要求检查功能与格式；已有Python隔离功能测试，开放设计/审查逐项评审 | 生成SQL不等于连接数据库执行；围栏会导致要求原始代码的样本无法直接运行 |",
        "| 文本向量与重排 | 重排核对目标文档位置；向量需另验检索效果 | 两个向量接口输出待评；检索质量用例未测，不能用返回维度当质量通过 |",
        "| 多轮、工具、长上下文 | 需真实上下文、工具调用、长输入及对应验收 | 当前7项文本未测：4项工具/结构化接口、2项多轮/长上下文、1项检索质量 |",
        "",
        "文本未通过分别为14、3、4项。Decider包含13项通用路由/输出模态问题和1项代码安全问题；"
        "Liquid包含2项路由问题和1项代码安全问题；H2O另有1项代码格式问题。"
        "这些是路由、执行与答案共同形成的端到端结果。",
        "",
        "## 多模态指标怎么验收",
        "",
        "| 能力 | 本批已得到的结果（三种路由方案） | 进一步评测指标 |",
        "|---|---|---|",
        "| 图像理解 | 2/4通过：蓝图、OCR；红图识别错误，图表答案内容正确但额外解释违反格式 | 扩展视觉问答内容正确率；OCR字符错误率CER；内容和格式分别记分 |",
        "| 音频识别 | 0/1通过：词语正确，但输出多了时间戳，不满足“只转写说话内容” | 词错误率WER/字符错误率CER衡量内容；另记额外输出/时间戳的格式合规率 |",
        "| 语音生成 | 英语1项待听评；中文1项未通过且有边界标记问题 | 文本读对比例、可懂度、自然度MOS及端到端延迟；当前没有MOS分数 |",
        "| 视频理解 | 主体与动作1项通过；长视频时序定位1项未测 | 内容正确率；有时间段标注后计算时序IoU和定位召回率 |",
        "| 图像生成与编辑 | 去背景损坏主体，1项未通过；文生图1项无可用执行型号且有边界标记问题 | 背景移除、主体保留和指令符合度分别验收；当前没有统一感知分数 |",
        "| 视频生成 | 1项无可用执行型号且有边界标记问题 | 内容符合、跨帧一致、运动质量和生成时延，均需实际生成后验收 |",
        "| 多图、音画同步、实时语音、说话人分离 | 4项未测；加上长视频时序定位，共5项多模态未测 | 差异识别、音画同步偏差、打断响应、说话人分离错误率DER，需对应输入与标注 |",
        "",
        "WER/CER =（替换 + 删除 + 插入错误数）/ 参考词数或字符数，越低越好，可能超过100%；"
        "MOS为定义量表上的听评均分；时序IoU为预测与参考时间段的交并比，越高越好；"
        "DER衡量说话人时间归属错误。以上细粒度指标尚未在本批计算，不能把它们写成已有实测分数。",
        "",
        "多模态16项的状态为：3通过、7未通过、1待评、5未测。"
        "7项未通过中，2项内容错误（红图、去背景）、2项格式违规（图表、ASR）、"
        "3项能力未交付且有边界标记问题（中文语音、文生图、文生视频）。"
        "这3项仍沿用原 `end-to-end-v1` 的失败判定；本次没有重评或调整分数。",
        "",
        "三种方案引用了部分相同目标观察，不能把相同的媒体输出当作三次独立实验。"
        "GPU只读取文字和附件类型，真实媒体由Bedrock目标模型处理；这些通过率不代表GPU自身的识图或听音能力。",
        "",
        "## 各细分类通过率",
        "",
        "| 组别 | 细分类 | " + " | ".join(names) + " |",
        "|---|---|" + "---:|" * len(names),
    ]
    for group in report["groups"].values():
        for family, summary in group["family_summary"].items():
            lines.append(
                f"| {group['label']} | {family} | "
                + " | ".join(ratio(summary[p]["pass"], summary[p]["planned"]) for p in providers)
                + " |"
            )
    lines += [
        "",
        "## 复核与使用限制",
        "",
        "这是可见开发回归集的一次顺序执行；不能据此推断未来业务成功概率、并发吞吐或全部多模态能力。"
        "通过率是使用对应GPU分类器的完整路由方案结果，包含后端调整、候选过滤、目标模型和适配器的影响。"
        "已知边界标记问题及共享观察限制见[失败原因分析](../failure-analysis.zh.md)。",
        "",
        "延迟与费用需要独立阅读：GPU服务往返、完整路由和目标执行的P50/P95分别统计；"
        "目标Token × 已知参考价只代表已知费用，不含常驻GPU、ECS、网络等基础设施。"
        "未知价格不能当零，也不能据此宣称完整的每成功任务成本已测得。",
        "",
        "[机器可读指标与逐场景分组](metrics.json)保存源文件SHA-256、记录ID、原始/调整后类型和观察ID，"
        f"可与[完整评测方法]({method_link})对照。生成器核验本批源文件SHA-256，并检查分类覆盖、"
        "重复记录和分组加总；其他批次需重新分析并发布分组报告。",
        "",
        "在仓库根目录重建：",
        "",
        "```bash",
        "python scripts/report_gateway_modalities.py \\",
        f"  --evaluation {source['path']} \\",
        "  --output-dir validation/independent_gateway/overall-metrics-2026-11-25/modality-breakdown",
        "```",
        "",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    source = args.evaluation.resolve()
    output_dir = args.output_dir.resolve()
    outputs = [output_dir / name for name in ("metrics.json", "README.zh.md")]
    if source in outputs:
        parser.error("The source export must not be an output file")
    report = build_report(source)
    rendered = markdown(report, output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs[0].write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    outputs[1].write_text(rendered, encoding="utf-8")
    print(
        json.dumps(
            {
                "job": report["source"]["job_id"],
                "groups": {key: len(group["case_ids"]) for key, group in report["groups"].items()},
                "routed_rows": report["source"]["routed_rows"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
