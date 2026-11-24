"""Add measured public JevBench results to v16 without changing its existing slides."""

import json
import re
import shutil
from datetime import UTC, datetime

import build_presentation_v10 as V
import presentation_layout as L
from build_presentation_v5 import replace_text
from pptx import Presentation
from pptx.util import Inches

ROOT = V.ROOT
OUT = ROOT / "docs/presentations/v17"
BASE = next((ROOT / "docs/presentations/v16").glob("*.pptx"))
NAME = "Strands-Decider-v17-JevBench公开集实测与榜单口径-34页-20261016"
RUN = ROOT / "validation/jevbench/runs/2026-10-16-original"
PAGES = 34


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main():
    (OUT / "assets").mkdir(parents=True, exist_ok=True)
    (OUT / "sources").mkdir(exist_ok=True)
    V.OUT, V.ASSETS, V.SOURCES = OUT, OUT / "assets", OUT / "sources"
    V.EVIDENCE.clear()
    V.IMAGES.clear()
    V.SLIDES.clear()
    base_hash = V.digest(BASE)
    previous = V.load(V.record(ROOT / "docs/presentations/v16/build-manifest.json"))
    evidence = {}
    for name in ("summary.json", "analysis.json", "verification.json", "leaderboard-score.json",
                 "leaderboard-reference.json", "ec2-price-reference.json"):
        source = V.record(RUN / name)
        target = OUT / "sources" / name
        shutil.copy2(source, target)
        evidence[name] = V.load(V.record(target))
    summary, analysis, score = (
        evidence[name] for name in ("summary.json", "analysis.json", "leaderboard-score.json")
    )
    assert summary["n_correct"] == 168 and summary["n_attempted"] == 231
    assert score["official_composite"] is None and score["rank"] is None
    prs = Presentation(BASE)
    original_ids = list(prs.slides._sldIdLst)
    order, metas = [], []

    def keep(page):
        order.append(original_ids[page - 1])
        metas.append({**previous["slides"][page - 1], "source": "v16",
                      "v16_page": page, "body_modified": False})

    def new(title, subtitle, notes):
        slide = V.base(prs, "02  JevBench · Decider 实测", title, subtitle, "", notes)
        order.append(prs.slides._sldIdLst[-1])
        metas.append({**V.SLIDES[-1], "source": "new", "body_modified": True})
        return slide

    for page in range(1, 7):
        keep(page)
    notes = (
        "2026-10-16 02:46 UTC 实测，原版 Strands Decider 2B Hobson v19，"
        "us-east-1 g6.xlarge / NVIDIA L4，非 vLLM。"
        "使用官方 JevBench 固定提交 1bcc55eb6c8cffde2306b3db03ede39b61c6152a，"
        "运行 v1 的 231 道公开题：easy 48、original/standard 72、hard 111。"
        "每次一道题，单并发，无重试，Choice/Noul/Score 各预热一次，预热不计入。"
        "客户端 ECS Fargate 经私网 HTTP 连接模型，延迟含网络和服务排队，"
        "不是纯 GPU kernel 时间；窗口 4096 tokens，输入平均 661.1 tokens。"
        "168 正确，63 错误；调用失败和严格格式错误均为 0。"
        "Brier=0.34828721233766263，ECE=0.04986926406926421，越低越好。"
        "18 个任务族完整数据见 sources/summary.json。"
        "工具选择和意图识别是本批优势，长规则、时序数值、多跳推理是短板；"
        "小样本公开路由题并不等于生产模型路由验收。"
        "这是旧版公开子集，不是当前榜单完整题集；不与旧版 PPT 的 Jev / GPT 榜单数据混算。"
        "\n来源：validation/jevbench/README.md\n"
        + json.dumps({"analysis": analysis, "totals": {
            k: summary[k] for k in ("n_attempted", "n_correct", "accuracy", "latency",
                                   "schema_validity_strict", "brier_mean")
        }}, ensure_ascii=False, indent=2)
    )
    slide = new(
        "Decider 公开集：短决策更稳，复杂推理仍有短板",
        "原版 v19 · g6.xlarge / L4 · 单并发 231 题 · 私网客户端计时",
        notes,
    )
    metrics = [
        ("正确率", f'{summary["accuracy"] * 100:.2f}%', "168 / 231", "clipboard-check"),
        ("P50 延迟", f'{summary["latency"]["p50_s"] * 1000:.1f} ms', "中位数", "timer"),
        ("P95 延迟", f'{summary["latency"]["p95_s"] * 1000:.1f} ms', "95% 请求不高于此值", "timer"),
        ("调用失败", "0 / 231", "响应格式有效率 100%", "circle-check"),
    ]
    for index, (title, value, caption, icon) in enumerate(metrics):
        x = .68 + index * 3.04
        L.rect(slide, x, 2.18, 2.86, 1.39, L.WHITE, L.LINE, True)
        V.icon(slide, icon, x + .17, 2.32, L.TEAL, .28)
        L.text(slide, title, x + .57, 2.30, 2.10, .31, 15, L.MUTED)
        L.text(slide, value, x + .17, 2.73, 2.54, .44, 27, L.TEAL, True)
        L.text(slide, caption, x + .17, 3.23, 2.54, .23, 11.5, L.MUTED)
    L.text(slide, "按难度看差距", .68, 3.85, 5.55, .36, 19, L.NAVY, True)
    tiers = analysis["tiers"]
    V.table(
        slide, ["难度", "答对 / 题数", "正确率"],
        [[label, f'{tiers[key]["correct"]}/{tiers[key]["n"]}',
          f'{tiers[key]["accuracy"] * 100:.2f}%']
         for key, label in (("easy", "简单"), ("standard", "标准"), ("hard", "困难"))],
        [1.5, 2.05, 2.0], x=.68, y=4.35, row_h=.43, size=14,
    )
    L.text(slide, "按任务看适用边界", 6.64, 3.85, 6.0, .36, 19, L.NAVY, True)
    V.table(
        slide, ["任务", "答对 / 题数", "正确率"],
        [[label, f'{summary["per_family"][key]["n_correct"]}/{summary["per_family"][key]["n_attempted"]}',
          f'{summary["per_family"][key]["accuracy"] * 100:.2f}%']
         for key, label in (("tool_selection", "工具选择"), ("intent", "意图识别"),
                            ("long_policy", "长规则"), ("temporal_numeric", "时序 / 数值"))],
        [2.40, 1.83, 1.79], x=6.64, y=4.35, row_h=.37, size=13,
    )
    L.text(slide, f'概率质量：Brier {summary["brier_mean"]:.4f} · ECE {summary["ece"]["ece"]:.4f}',
           .80, 6.11, 5.6, .28, 12.5, L.MUTED)
    L.text(slide, "先做领域验收；复杂条件保留复核与回退。", 6.76, 6.26, 5.78, .29,
           14, L.TEAL, True)

    notes = (
        "本页明确区分本地测量、按网站公式估算与正式榜单分数。"
        "现有 231 道旧版公开题不能替代当前发布集的 300 公开 + 1200 封闭题。"
        "Intelligence 涉及机会校正、题型/难度权重、公开/封闭组合及差距惩罚；"
        "Calibration 需要版本对应的概率分布评分，不能直接以 100*(1-ECE) 替代。"
        "Capability=(I+C)/2；Composite Option A 为四轴等权调和平均，"
        "I、Speed、Cost 低于 50 时乘各自平方惩罚。缺项则没有总分。"
        "本地 P50/P95 直接代入速度公式得 93.94，但私网不等于官方公网测量。"
        "套用官方自托管 2*t+150ms 假设，P50=286.65ms，P95=957.42ms，Speed=85.62。"
        "官方自托管用回环测量，本次已含私网；此值仅作条件估算，不是正式评分。"
        "AWS Price List 查询 g6.xlarge Linux Shared us-east-1 On Demand 为 $0.8048/h。"
        "单并发平均耗时125.9066ms、100%请求时间占比：$0.028147/千次，Cost=56.52；"
        "不含空闲、EBS、网络、业务服务与测试客户端。请求占比并非 GPU 利用率。"
        "官网 What-If 按P50估计为$0.015274/千次、64.48分，也不替代正式共同成本口径。"
        "核验发布文件137个综合分均与公式一致，最大误差1.42e-14。"
        "Jev 1.13.0 官网能力分77.13、综合分71.49属于另一完整评测，不与本地正确率排名。"
        "\n来源：https://benchmarkheaven.com/jev-models\n"
        "https://benchmarkheaven.com/jev-models/api\n"
        "https://benchmarkheaven.com/jev-models/methodology\n"
        "validation/jevbench/leaderboard-score.md\n"
        + json.dumps(score, ensure_ascii=False, indent=2)
    )
    slide = new(
        "公开题正确率与榜单总分，需要分开看",
        "已复算官网 137 个综合分，公式一致；本次 Decider 尚不具备完整榜单成绩",
        notes,
    )
    V.flow(slide, [
        ("已测：231 道旧版公开题", "正确率 72.73%\n延迟与概率质量已保存", "clipboard-check"),
        ("当前发布集：1,500 题", "300 道公开题\n1,200 道封闭题", "layers"),
        ("还缺：完整质量评分", "Intelligence / Calibration\n正式能力分、总分与排名待评测", "target"),
    ], y=2.18, h=1.82)
    speed = score["speed_projections"]["self_hosted_rule_axis"]
    cost = score["cost_projections"]["cases"]["mean_call_time_allocation"][0]
    V.card(slide, .68, 4.25, 5.78, 1.52, f"速度分估算  {speed:.2f}",
           "P50 / P95 分别套用 2×耗时 + 150 ms。\n本次包含私网耗时，仅为条件估算。", L.TEAL, size=14.5)
    V.card(slide, 6.68, 4.25, 5.98, 1.52, f'成本分估算  {cost["cost_axis"]:.2f}',
           f'g6.xlarge 按需，持续有请求：${cost["usd_per_1000"]:.5f}/千次。\n按平均耗时分摊，仅计 EC2 计算费。', V.PURPLE, size=14.5)
    V.callout(slide, "72.73% 不能直接与 Jev 的榜单综合分 71.49 比较；Decider 正式总分暂无。",
              y=5.92, size=15)
    # Preserve the existing methodology links on P6 and in notes, without a new footer row.
    for page in range(7, 33):
        keep(page)
    for node in list(prs.slides._sldIdLst):
        prs.slides._sldIdLst.remove(node)
    for node in order:
        prs.slides._sldIdLst.append(node)
    assert len(prs.slides) == PAGES
    for index, (slide, meta) in enumerate(zip(prs.slides, metas, strict=True), 1):
        for shape in list(slide.shapes):
            if shape.has_text_frame and Inches(6.5) <= shape.top < Inches(7.03):
                assert not shape.text.strip()
                shape._element.getparent().remove(shape._element)
            elif shape.has_text_frame and shape.top > Inches(7) and re.fullmatch(r"\d+ / \d+", shape.text):
                replace_text(shape, shape.text, f"{index:02} / {PAGES}")
        meta.update(page=index, notes=slide.notes_slide.notes_text_frame.text)
    prs.core_properties.title = "Strands Decider × Jev × GPT：JevBench公开集实测与榜单口径"
    prs.core_properties.subject = "v17 / 34 pages / measured public JevBench results"
    path = OUT / f"{NAME}.pptx"
    prs.save(path)
    assert V.digest(BASE) == base_hash
    save(OUT / "build-manifest.json", {
        "created_at": datetime.now(UTC).isoformat(),
        "base_file": str(BASE.relative_to(ROOT)), "base_sha256": base_hash,
        "pptx_file": str(path.relative_to(ROOT)), "pptx_sha256": V.digest(path),
        "pages": PAGES, "new_pages": [7, 8], "slides": metas,
        "evidence": V.EVIDENCE, "images": V.IMAGES,
        "preserved_original_pages": 32, "internal_only_pages": [34],
        "new_model_calls": 0, "lower_explanatory_rows": 0,
    })
    notes = ["# v17 讲稿与来源", "", "新增 P7–8：Decider JevBench 公开集实测及榜单评分口径。",
             "继承页的讲稿原文保留；其中历史页码对应 v16，可按 build-manifest.json 的 v16_page 查找。", ""]
    for meta in metas:
        notes += [f'## {meta["page"]:02} · {meta["title"]}', "", meta["notes"], ""]
    (OUT / "讲稿与来源.md").write_text("\n".join(notes))
    print(json.dumps({"pptx": str(path), "pages": PAGES, "new_pages": [7, 8]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
