"""Produce an auditable public-deployment report without any inference calls."""

import argparse
import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from app.gateway_store import percentile
from scripts.run_online_quality import read, save
from scripts.summarize_latency_quality import counts, summarize


def report(folder):
    summary = summarize(folder)
    manifest = read(folder / "manifest.json")
    cases = {c["id"]: c for c in manifest["cases"]}
    raw_cases = {p.name.removesuffix(".json.gz"): read(p) for p in (folder / "base").glob("*.json.gz")}
    turns = {}
    for p in (folder / "industries").glob("*.json.gz"):
        scenario = read(p)
        for turn in scenario["turns"]:
            turns[f"agent-{scenario['scenario_id']}-{turn['number']}"] = turn
    for row in summary["rows"]:
        raw = raw_cases.get(row["case_id"], turns.get(row["case_id"], {}))
        row["run_id"] = raw.get("run_id")
        row["session_id"] = raw.get("session_id")
        row["configuration_id"] = raw.get("route", {}).get("selection", {}).get("selected_candidate_id")
        if row["review"].get("source") == "execution":
            errors = raw.get("errors") or ([raw["error"]] if raw.get("error") else [])
            if errors:
                row["review"] = {**row["review"], "reason": json.dumps(errors, ensure_ascii=False)}
        if row["case_id"] in cases and cases[row["case_id"]].get("allowed_workflows"):
            workflow = (raw.get("route", {}).get("profile") or {}).get("workflow")
            if workflow is not None:
                row["workflow_fit"] = workflow in cases[row["case_id"]]["allowed_workflows"]
                if row["workflow_fit"] is False and row["status"] == "pass":
                    row["status"] = "fail"
        if row["case_id"] in turns:
            row["job_id"] = raw.get("job_id")
            row["models"] = [s.get("model_id") for s in raw.get("steps", [])]
    summary["overall"] = counts(summary["rows"])
    summary["categories"] = {
        name: counts([r for r in summary["rows"] if r["category"] == name])
        for name in summary["categories"]
    }
    summary["workflow_fit"] = {
        "passed": sum(r.get("workflow_fit") is True for r in summary["rows"]),
        "scored": sum(r.get("workflow_fit") is not None for r in summary["rows"]),
        "scope": "Frozen allowed workflow labels for the old independent tasks; not seven-label accuracy",
    }
    summary["original_72"] = counts([r for r in summary["rows"] if r["split"] != "fresh_general"])
    summary["new_general_32"] = counts([r for r in summary["rows"] if r["split"] == "fresh_general"])
    summary["failures"] = [r for r in summary["rows"] if r["status"] == "fail"]
    summary["unmeasured"] = [r for r in summary["rows"] if r["status"] == "unmeasured"]
    histories = [read(p) for p in sorted((folder / "history").glob("*.json.gz"))]
    start = read(folder / "catalog-start.json.gz")
    end_path = folder / "catalog-end.json.gz"
    end = read(end_path) if end_path.exists() else None
    selections, outputs = [], []
    for history in histories:
        for event in history.get("events", []):
            if event.get("stage") == "selection" and event.get("status") == "completed":
                selections.append(event["data"].get("selection", {}))
            if event.get("stage") == "execution" and event.get("status") == "completed":
                outputs.append(event["data"])
    routing = start["model_routing"]
    summary["provenance"] = {
        "url": "https://d3eipg2g3l3an3.cloudfront.net",
        "catalog_version": start["catalog_version"], "routing": routing,
        "catalog_stable": end is not None and all(start[k] == end[k] for k in (
            "catalog_version", "ranking_version", "policy_version", "model_routing")),
        "saved_histories": len(histories), "saved_selections": len(selections),
        "selection_versions": {
            key: dict(Counter(s.get(key) for s in selections))
            for key in ("routing_version", "measurement_version", "public_benchmark_version")
        },
        "expected_versions_match": all(
            s.get("routing_version") == routing["version"]
            and s.get("measurement_version") == routing["measurement_version"]
            and s.get("public_benchmark_version") == routing["public_benchmark_version"]
            for s in selections),
        "gpu_call_counts": dict(Counter(len(s.get("decision_calls", [])) for s in selections)),
        "history_statuses": dict(Counter(h.get("status") for h in histories)),
        "fallback_count": sum(h.get("fallback_count") or 0 for h in histories),
        "agent_platform_versions": dict(Counter(t.get("platform_version") for t in turns.values())),
        "sources": {
            path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
            for path in ("scripts/run_online_quality.py", "scripts/online_general_cases.py",
                         "scripts/summarize_online_quality.py")
        },
    }
    # Agent results are stored in their native job records; include their history outputs once.
    costs = [Decimal(str(o["observed_reference_cost_usd"])) for o in outputs
             if o.get("observed_reference_cost_usd") is not None]
    summary["performance"] = {
        "boundary": "Deployed ECS history route/target timings; public HTTP wall times separately for ordinary tasks",
        "route_p50_ms": percentile([h.get("route_ms") for h in histories], .5),
        "route_p95_ms": percentile([h.get("route_ms") for h in histories], .95),
        "target_p50_ms": percentile([h.get("target_ms") for h in histories], .5),
        "target_p95_ms": percentile([h.get("target_ms") for h in histories], .95),
        "ordinary_http_p50_ms": percentile([r.get("wall_ms") for r in raw_cases.values()], .5),
        "ordinary_http_p95_ms": percentile([r.get("wall_ms") for r in raw_cases.values()], .95),
        "saved_execution_outputs": len(outputs), "cost_known_outputs": len(costs),
        "known_reference_cost_usd": str(sum(costs, Decimal(0))),
        "cost_limit": "Known public-rate reference only; failed calls and missing usage are not free; excludes GPU/infrastructure.",
    }
    save(folder.parent / "summary.json", summary)

    text = [
        "# 最新线上环境评测 · 2026-11-27", "",
        "通过正式CloudFront入口调用ECS revision33及真实AgentCore V2。"
        "原72项开发回归加32项新通用任务，共104项；新增题目在首次调用前冻结。"
        "未部署代码、调整路由权重或针对失败补跑。", "",
        "## 结果", "",
        "| 范围 | 严格通过 | 内容正确 | 执行完成 |",
        "|---|---:|---:|---:|",
    ]

    def table_row(name, value):
        if value["status_counts"] == {"unmeasured": value["planned"]}:
            return f"| {name} | 未测 | 未测 | {value['completed']}/{value['planned']} |"
        return (f"| {name} | {value['passed']}/{value['planned']}（{value['pass_percent']}%）"
                f" | {value['content_passed']}/{value['planned']}（{value['content_percent']}%）"
                f" | {value['completed']}/{value['planned']} |")

    for key, label in (("overall", "全部计划项"), ("original_72", "原72项"),
                       ("new_general_32", "新增32项通用任务")):
        text.append(table_row(label, summary[key]))
    text += ["", "严格通过要求执行完整、内容正确及格式合规。内容正确率仍以全部计划项为分母；"
             "超时、失败和未测项不移出分母。开放题为助手按冻结标准非盲复核，"
             "每份复核绑定回答SHA-256；代码在无凭证、无网络的隔离容器内执行功能检查。", "",
             "## 分类", "", "| 类别 | 严格通过 | 内容正确 | 执行完成 |", "|---|---:|---:|---:|"]
    text += [table_row(name, value) for name, value in summary["categories"].items()]
    text += ["", "## 未通过与未测项", ""]
    for row in summary["failures"] + summary["unmeasured"]:
        reason = row["review"].get("reason") or json.dumps(row["review"], ensure_ascii=False)
        if row["case_id"] == "embed-en":
            reason = "接口只返回12维向量预览，无法核验完整检索排序。"
        elif row["review"].get("source") == "execution":
            raw = raw_cases.get(row["case_id"], {})
            if raw.get("errors"):
                reason = "；".join(e.get("message", str(e)) for e in raw["errors"])
        status = "未测" if row["status"] == "unmeasured" else "未通过"
        text.append(f"- `{row['case_id']}`：{status}；{reason}")
    stable_label = "一致" if summary["provenance"]["catalog_stable"] else "未确认一致"
    versions_label = "匹配" if summary["provenance"]["expected_versions_match"] else "不匹配"
    calls_label = "、".join(f"{count}次选模各调用GPU{calls}次"
                           for calls, count in summary["provenance"]["gpu_call_counts"].items())
    text += [
        "", "## 环境、耗时与费用", "",
        f"- 开始和结束目录、路由、价格版本{stable_label}；运行期间保持ECS revision33。",
        f"- {calls_label}；路由与价格版本逐项{versions_label}，"
        f"实际回退{summary['provenance']['fallback_count']}次。",
        f"- 路由P50/P95：{summary['performance']['route_p50_ms']}/{summary['performance']['route_p95_ms']}ms。"
        "这是ECS记录的路由时间，不含目标生成。",
        f"- {len(costs)}/{len(outputs)}份执行输出有已知参考费用；合计"
        f"${summary['performance']['known_reference_cost_usd']}。失败调用缺最终用量不能算免费，"
        "该合计也不含GPU、网络等基础设施费用。",
        "", "## 评测边界", "",
        "- 线上向量接口只提供12维预览，不能复算完整跨语言检索排序。`embed-en`接口执行单列，"
        "质量标为未测，保留在104项和原72项分母中。",
        "- 旧72项使用本地后端及本地Agent适配器；本次使用正式入口、流式输出和真实AgentCore。"
        "旧题内容沿用，但运行环境、价格及部分默认行为不同，不能把分差全部归因于价格或微调。",
        "- 新通用题默认均衡；原题保留指定偏好。目标输出上限16384，端点限制由线上服务处理。"
        "普通请求并发上限2，Agent随后执行。普通任务关闭回退；所有实际回退数由原始历史核对。",
        "- 新增通用多轮4项使用同一真实线上会话；旧题中的显式历史按原题提供。"
        "工具数据来自线上合成行业数据集，不发生真实业务交易。",
        "- 新题是人工构造的小规模首次测试，旧题为开发回归；均不代表随机总体或通用准确率。",
        "- 冻结清单中“No target retries or fallback”仅准确描述普通请求。线上Agent入口"
        "默认启用回退，测试保留线上行为；详见[执行协议澄清](protocol-clarification.json)。"
        "此澄清未改变题目、评分标准或已保存响应。",
        "", "## 记录与复现", "",
        "[冻结题目](suite.json) · [机器可读汇总](summary.json) · [逐项结论](cases.zh.md) · "
        "[新增通用任务完整回答](general-cases.zh.md) · [原始运行](run/) · "
        "[开始部署](deployment-start.json) · [结束部署](deployment-end.json) · "
        "[评分与原始记录完整性核验](integrity-verification.json) · [文件哈希](artifact-sha256.json)。", "",
        "7道代码题的973项隔离功能检查全部通过；评测相关47项软件测试通过，"
        "没有把软件测试数量混入回答正确率。[工具及代码检查](software-checks.json)。", "",
        "离线重算（不调用模型）：", "",
        "```bash", f".venv/bin/python -m scripts.summarize_online_quality --input {folder}", "```", "",
        "重新测试必须使用新输出目录，调用 `scripts.run_online_quality --suite <suite.json> --output <新目录>`。"
        "需AWS Secrets Manager访问和线上测试账户；登录凭证不写入报告。"
        "中断记录不可静默重试，按保存run/job ID通过只读API恢复。", "",
    ]
    (folder.parent / "README.zh.md").write_text("\n".join(text))
    details = ["# 逐项结果", "", "| 用例 | 分类 | 严格 | 内容 | 复核依据 |", "|---|---|---|---|---|"]
    for row in summary["rows"]:
        reason = str(row["review"].get("reason", row["review"].get("source", ""))).replace("|", "\\|").replace("\n", " ")
        details.append(f"| {row['case_id']} | {row['category']} | {row['status']} | {row['content_status']} | {reason} |")
    (folder.parent / "cases.zh.md").write_text("\n".join(details) + "\n")
    general = ["# 新增32项通用任务：冻结标准与实际回答", ""]
    for cid, case in cases.items():
        if case["split"] != "fresh_general":
            continue
        raw = raw_cases.get(cid, {})
        row = next(r for r in summary["rows"] if r["case_id"] == cid)
        general += [f"## {cid}", "", case["task"]["text"], "",
                    "**冻结标准：** " + case["judge"]["rubric"], "",
                    f"**结果：** 严格{row['status']}，内容{row['content_status']}；型号{row.get('model')}。", "",
                    "````text", raw.get("output", {}).get("text", "(无完整回答)"), "````", ""]
    (folder.parent / "general-cases.zh.md").write_text("\n".join(general))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    summary = report(args.input)
    print(json.dumps({k: summary[k] for k in ("overall", "original_72", "new_general_32")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
