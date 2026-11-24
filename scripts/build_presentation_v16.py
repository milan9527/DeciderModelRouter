"""Summarize measured decisions and consolidate support routing around current originals."""

import json
import re
from datetime import UTC, datetime
from statistics import mean

import build_presentation_v10 as V
import presentation_layout as L
from build_presentation_v5 import replace_text
from pptx import Presentation
from pptx.util import Inches

ROOT = V.ROOT
OUT = ROOT / "docs/presentations/v16"
BASE = next((ROOT / "docs/presentations/v15").glob("*.pptx"))
NAME = "Strands-Decider-v16-三模型性能效果与原始客服路由-32页-20261016"
PROVIDERS = ("decider", "jev", "bedrock")
STAGES = ("intake", "verify", "approve", "execute", "close")
STAGE_LABELS = ("客服受理", "支付核验", "风控审批", "资金处理", "客户回复")


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def payment_evidence():
    raw = V.data("docs/presentations/v12/sources/payment-live.json")
    run = raw["cases"]["pending"]["baseline"]
    comparison = {
        "case": "pending", "strategy": "baseline", "n_per_step": 1,
        "source_page": "v15 P9 (v16 P10)",
        "timing": "backend_total_ms per decision; sum of five decisions, not end-to-end business time",
        "providers": {},
    }
    for stage in STAGES:
        rows = [run["results"][p][stage] for p in PROVIDERS]
        assert all(row["model_input"] == rows[0]["model_input"] for row in rows)
    for provider in PROVIDERS:
        rows = [run["results"][provider][stage] for stage in STAGES]
        comparison["providers"][provider] = {
            "model": rows[0]["model"],
            "steps": {stage: run["results"][provider][stage] for stage in STAGES},
            "sum_ms": sum(row["timings"]["backend_total_ms"] for row in rows),
            "matches_reference": sum(row["matches_reference"] is True for row in rows),
            "business_outcome": run["businessOutcomes"][provider],
        }
    browser = V.data("validation/browser_finance/runs/2026-10-13-live/scored-summary.json")
    assert browser["browser"]["jev"]["success"] == browser["browser"]["jev"]["total"] == 10
    comparison["separate_browser_validation"] = {
        p: {k: browser["browser"][p][k] for k in ("success", "total")}
        for p in ("decider", "jev")
    }
    save(OUT / "sources/payment-step-comparison.json", comparison)
    V.record(OUT / "sources/payment-step-comparison.json")
    return comparison


def routing_evidence():
    raw = V.data("docs/results/model-evidence-2026-10-16/results.json")
    assert len(raw["runs"]) == 3
    comparison = {
        "task": raw["runs"][0]["cases"]["support"]["task"]["text"],
        "strategy": "original", "n_per_stage": 3,
        "scope": "Workflow and target-model routing decisions only. Target execution is outside this slide.",
        "providers": {},
    }
    for run in raw["runs"]:
        sides = run["cases"]["support"]["sides"]
        for stage in (0, 1):
            rows = [sides[p]["steps"][stage] for p in PROVIDERS]
            assert all(row["model_input"] == rows[0]["model_input"] for row in rows)
    for provider in PROVIDERS:
        samples = [run["cases"]["support"]["sides"][provider] for run in raw["runs"]]
        for sample in samples:
            assert sample["error"] is None
            assert sample["steps"][0]["choice"] == "customer_support"
            assert all(step["source"] == "live" for step in sample["steps"])
            assert sample["steps"][1]["strategy"] == "original"
        workflow_ms = mean(s["steps"][0]["timings"]["backend_total_ms"] for s in samples)
        model_ms = mean(s["steps"][1]["timings"]["backend_total_ms"] for s in samples)
        first = samples[0]["steps"][1]
        assert all(s["steps"][1]["choice"] == first["choice"] for s in samples)
        comparison["providers"][provider] = {
            "router_model": first["model"],
            "workflow": "customer_support",
            "choice": first["choice"],
            "selected_model": first["selected_model"],
            "workflow_mean_ms": workflow_ms,
            "model_mean_ms": model_ms,
            "route_mean_ms": workflow_ms + model_ms,
            "samples": [s["steps"] for s in samples],
        }
    save(OUT / "sources/support-routing-original.json", comparison)
    V.record(OUT / "sources/support-routing-original.json")
    return comparison


def main():
    (OUT / "assets").mkdir(parents=True, exist_ok=True)
    (OUT / "sources").mkdir(exist_ok=True)
    V.OUT, V.ASSETS, V.SOURCES = OUT, OUT / "assets", OUT / "sources"
    V.EVIDENCE.clear()
    V.IMAGES.clear()
    V.SLIDES.clear()
    base_hash = V.digest(BASE)
    old = V.load(V.record(ROOT / "docs/presentations/v15/build-manifest.json"))
    payment, routing = payment_evidence(), routing_evidence()
    prs = Presentation(BASE)
    originals = list(prs.slides._sldIdLst)
    order, metas = [], []

    def keep(page):
        order.append(originals[page - 1])
        metas.append({
            **old["slides"][page - 1], "source": "v15", "v15_page": page,
            "body_modified": False,
        })

    def new(section, title, subtitle, notes):
        slide = V.base(prs, section, title, subtitle, "", notes)
        order.append(prs.slides._sldIdLst[-1])
        metas.append({**V.SLIDES[-1], "source": "new", "body_modified": True})
        return slide

    for page in range(1, 5):
        keep(page)
    notes = (
        "原P4之后新增性能、效果与适用场景总结。左表完整采用v15第9页"
        "（本版第10页）的USDC待确认原始固定流程记录，2026-10-15采集，各步骤每模型一次。"
        "GPT为us.openai.gpt-6.1-sol，low effort；不是模型路由模块中的GPT-6 Sol。"
        "耗时是应用timings.backend_total_ms，含排队和提供方调用；总计是五次决策耗时相加，"
        "不是资金完成时间或浏览器端到端耗时。预热不计入。"
        "Decider与Jev原始审批选await_approval，GPT选approved；其他四步相同。"
        "与演示参考一致4/5、4/5、5/5，只是本例五次选择的计数，不是通用准确率。"
        "三者确认数都停留3/12，没有入账或结案，GPT避免额外审批不等于完成支付。"
        "适用场景为结合本项目各项验证的选型建议，不把一个支付样本外推为全面能力评测。"
        "Decider用于高频分类、工具选择、语义分流需先做领域验收，具体型号选型另有误选。"
        "Jev证据核验、多步操作建议另由2026-10-13浏览器验证10/10完成支持"
        "（Decider4/10），属于独立场景，不混入左表支付计数。"
        "GPT适合复杂条件判断及开放式任务，回复/代码等开放任务质量应另测。"
        "\n逐步原始输入、选择、耗时和业务状态：sources/payment-step-comparison.json\n"
        + json.dumps(payment, ensure_ascii=False, indent=2)
    )
    s = new(
        "02  模型对照 · 实测总结",
        "三模型怎么选：逐步耗时、判断效果与适用场景",
        "同一 USDC 待确认任务 · 原始固定流程 · 单次决策耗时（ms）· GPT-6.1 Sol / low",
        notes,
    )
    rows = []
    for stage, label in zip(STAGES, STAGE_LABELS, strict=True):
        rows.append([label, *[
            f'{payment["providers"][p]["steps"][stage]["timings"]["backend_total_ms"]:.1f}'
            for p in PROVIDERS
        ]])
    rows.append(["五步合计", *[f'{payment["providers"][p]["sum_ms"]:.1f}' for p in PROVIDERS]])
    rows.append(["参考一致", *[
        f'{payment["providers"][p]["matches_reference"]}/5' for p in PROVIDERS
    ]])
    V.table(s, ["步骤 · ms", "Decider", "Jev", "GPT"], rows,
            [2.12, 1.68, 1.68, 1.77], y=2.22, row_h=.475, size=16, highlights=(5, 6))
    panels = [
        ("Decider · 高频短决策", "分类、工具选择、语义分流。\n本例最快；审批仍有误选。", L.TEAL),
        ("Jev · 证据与多步操作", "证据核验、浏览器任务。\n另组浏览器验证完成 10/10。", V.PURPLE),
        ("GPT · 复杂条件与开放任务", "本例五步与参考一致 5/5。\n回复、代码等任务另行验收。", L.BLUE),
    ]
    for i, (title, body, color) in enumerate(panels):
        x, y, w, h = 8.20, 2.22 + i * 1.31, 4.46, 1.19
        L.rect(s, x, y, w, h, L.WHITE, L.LINE, True)
        L.rect(s, x, y, .045, h, color)
        L.text(s, title, x + .18, y + .11, w - .36, .32, 16, color, True)
        L.text(s, body, x + .18, y + .55, w - .36, .57, 13.5)
    V.callout(
        s, "三者仍需等待链上确认数从 3/12 达标；Decider / Jev 还额外选择了“等待人工审批”。",
        y=6.22, size=13.3,
    )

    for page in range(5, 18):
        keep(page)
    notes = (
        "将v15第18、19页两页客服路由合并为一页，采用2026-10-16最新保存的原始流程。"
        "本页范围仅为工作流选择与具体型号选择，两次都由对应模型判断。"
        "不展示目标模型执行结果或其等待时间，也不据此宣称目标答复完成或业务成功。"
        "用户请求、三路同阶段输入、候选ID与候选顺序相同；每阶段3轮后端耗时取均值。"
        "原始流程每轮两次判断，预热排除。所有18次相关路由判断返回有效候选，"
        "三轮选择不变。未使用增强输入、程序选型或答案替换。"
        "GPT路由器为us.openai.gpt-6-sol low，与支付逐步对比中的GPT-6.1 Sol区分。"
        "三轮为一个任务的重复，不是稳定延迟排名、吞吐或通用路由准确率。"
        "原始目标执行记录仍保存在docs/results/model-evidence-2026-10-16/results.json中，"
        "此次只修改演示范围，没有修改记录或运行新的模型调用。"
        "\n完整路由输入、选择、三轮耗时：sources/support-routing-original.json\n"
        + json.dumps(routing, ensure_ascii=False, indent=2)
    )
    s = new(
        "04  模型路由 · 支付客服原始流程",
        "支付客服：流程选择一致，具体型号选择不同",
        "2026-10-16 · 原始流程三轮均值 · 比较两次路由决策：选择处理流程 → 选择目标型号",
        notes,
    )
    V.icon(s, "headset", .85, 2.28, L.TEAL, .35)
    L.rect(s, 1.36, 2.15, 11.30, .86, L.PALE_TEAL, radius=True)
    L.text(s, routing["task"], 1.55, 2.30, 10.88, .57, 17)
    for i, (p, label, color) in enumerate(zip(
        PROVIDERS, ("Strands Decider", "Jev", "GPT-6 Sol · low"),
        (L.TEAL, V.PURPLE, L.BLUE), strict=True,
    )):
        row = routing["providers"][p]
        x = .68 + i * 4.07
        V.card(s, x, 3.40, 3.84, 2.63, label, "", color)
        L.text(s, f'① 流程选择 · {row["workflow_mean_ms"]:.1f} ms',
               x + .18, 3.96, 3.48, .33, 15, color, True)
        L.text(s, row["workflow"], x + .18, 4.32, 3.48, .31, 15)
        L.text(s, f'② 型号选择 · {row["model_mean_ms"]:.1f} ms',
               x + .18, 4.82, 3.48, .33, 15, color, True)
        L.text(s, f'{row["choice"]} → {row["selected_model"]["name"].replace("Claude ", "")}',
               x + .18, 5.18, 3.48, .32, 15)
        L.text(s, f'路由合计 {row["route_mean_ms"]:.1f} ms',
               x + .18, 5.66, 3.48, .35, 17, color, True)
    V.callout(
        s, "三路都识别为客服任务；Decider 选 Opus 5，Jev 选 Opus 5.5，GPT 选 Luna。本批 Decider 路由最快。",
        y=6.24, size=12.6,
    )
    for page in range(20, 33):
        keep(page)

    used = {node.rId for node in order}
    for node in list(prs.slides._sldIdLst):
        prs.slides._sldIdLst.remove(node)
        if node.rId not in used:
            prs.part.drop_rel(node.rId)
    for node in order:
        prs.slides._sldIdLst.append(node)
    assert len(prs.slides) == 32
    for index, (slide, meta) in enumerate(zip(prs.slides, metas, strict=True), 1):
        for shape in list(slide.shapes):
            if shape.has_text_frame and Inches(6.5) <= shape.top < Inches(7.03):
                assert not shape.text.strip()
                shape._element.getparent().remove(shape._element)
            elif shape.has_text_frame and shape.top > Inches(7) and re.fullmatch(
                r"\d+ / \d+", shape.text
            ):
                replace_text(shape, shape.text, f"{index:02} / 32")
        meta.update(page=index, notes=slide.notes_slide.notes_text_frame.text)
    prs.core_properties.title = "Strands Decider × Jev × GPT：性能效果总结与原始客服路由"
    prs.core_properties.subject = "v16 / 32 pages / per-step measured decisions; original support routing"
    path = OUT / f"{NAME}.pptx"
    prs.save(path)
    assert V.digest(BASE) == base_hash
    save(OUT / "build-manifest.json", {
        "created_at": datetime.now(UTC).isoformat(),
        "base_file": str(BASE.relative_to(ROOT)), "base_sha256": base_hash,
        "pptx_file": str(path.relative_to(ROOT)), "pptx_sha256": V.digest(path),
        "pages": 32, "new_pages": [5, 19], "slides": metas,
        "evidence": V.EVIDENCE, "images": V.IMAGES,
        "preserved_original_pages": 30, "replaced_v15_pages": [18, 19],
        "optimization_pages_unchanged": True, "internal_only_pages": [32],
        "support_comparison_scope": "original workflow and model routing only",
        "new_model_calls": 0, "aws_resource_mutations": 0, "lower_explanatory_rows": 0,
    })
    rows = ["# v16 讲稿与来源", "",
            "P5 新增逐步性能、效果与适用场景；P19 合并为最新原始客服路由；P32 保留 internal only。", ""]
    for meta in metas:
        rows += [f'## {meta["page"]:02} · {meta["title"]}', "", meta["notes"], ""]
    (OUT / "讲稿与来源.md").write_text("\n".join(rows))
    print(json.dumps({"pptx": str(path), "pages": 32, "new_pages": [5, 19]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
