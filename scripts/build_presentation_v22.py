"""Streamline v21 into one 34-slide narrative using its existing evidence and visuals."""

import json
import re
import shutil
from copy import deepcopy
from datetime import UTC, datetime

import build_presentation_v10 as V
import presentation_layout as L
from build_presentation_v5 import replace_text
from pptx import Presentation
from pptx.util import Inches, Pt

ROOT = V.ROOT
OUT = ROOT / "docs/presentations/v22"
BASE = next((ROOT / "docs/presentations/v21").glob("*.pptx"))
NAME = "Strands-Decider-v22-从业务验证到优化部署-34页-20261021"
PAGES = 34
ORDER = [1, 2, 3, 7, 6, 4, 10, 12, 13, 15, 14, 16, 17, 18, 19,
         21, 24, 22, 23, 25, 27, 28, 29, 30, 31, 32, 34, 36,
         37, 38, 39, 40, 41, 42]
REMOVED = {
    5: "通用流程与新原理页重复；适用场景并入 P3。",
    8: "支付逐步耗时在详细结果页重复，适用结论保留到末尾。",
    9: "旧榜单表与新截图重复；保留截图与正确的能力分定义。",
    11: "公开集和榜单分数边界并入两页评测说明，移除分数估算展开。",
    20: "客服闭环作为旁支移出主线；固定流程的未完成状态仍完整保留。",
    26: "未来优化路线与已完成微调流程重复，衔接并入微调首屏。",
    33: "独立部署图与演示架构重复；共享 GPU 架构统一到 P8。",
    35: "高并发等待结论与 Decider/Jev 并发表重复，合并到 P28。",
}


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def edit(slide, before, after, size=None):
    shapes = [s for s in slide.shapes if s.has_text_frame and before in s.text]
    assert len(shapes) == 1, (before, len(shapes))
    shape = shapes[0]
    if "\n" in before:
        assert shape.text == before
        lines = after.split("\n")
        assert len(lines) == len(shape.text_frame.paragraphs)
        style = next(
            (run._r.rPr for p in shape.text_frame.paragraphs for run in p.runs if run._r.rPr is not None),
            None,
        )
        for paragraph, text in zip(shape.text_frame.paragraphs, lines, strict=True):
            if paragraph.runs:
                paragraph.runs[0].text = text
                for run in paragraph.runs[1:]:
                    run.text = ""
            elif text:
                run = paragraph.add_run()
                if style is not None:
                    run._r.append(deepcopy(style))
                run.text = text
            if size is not None:
                for run in paragraph.runs:
                    run.font.size = Pt(size)
    else:
        replace_text(shape, before, after, size)
    assert after in shape.text, after
    return shape


def chapter(page):
    if page <= 7:
        return "01  为什么需要决策模型"
    if page == 8:
        return "02  三模型演示架构"
    if page <= 15:
        return "03  支付客服 · 从误选到输入优化"
    if page <= 19:
        return "04  模型路由 · 看速度，也看选择"
    if page == 20:
        return "05  其他场景 · 适用边界"
    if page <= 25:
        return "06  领域微调 · 从数据到结果"
    if page <= 28:
        return "07  vLLM · 部署与性能"
    if page <= 31:
        return "08  Jev 与 EC2 · 成本选择"
    return "09  总结与资料" if page < 34 else "附录  internal only · 候选顺序诊断"


def main():
    for part in ("sources/v21-notes", "screenshots"):
        (OUT / part).mkdir(parents=True, exist_ok=True)
    V.EVIDENCE.clear()
    base_hash = V.digest(BASE)
    previous = V.load(BASE.parent / "build-manifest.json")
    for item in previous["evidence"]:
        source = ROOT / item["path"]
        assert V.digest(source) == item["sha256"]
        if not source.is_relative_to(BASE.parent):
            V.record(source)
            continue
        target = OUT / source.relative_to(BASE.parent)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        V.record(target)
    source_files = {
        "routing-summary.json": ROOT / "docs/results/model-evidence-2026-10-16/summary.json",
        "support-routing-original.json": ROOT / "docs/presentations/v16/sources/support-routing-original.json",
        "payment-comparison.json": ROOT / "docs/presentations/v12/sources/payment-comparison.json",
        "finetuning-summary.json": ROOT / "docs/presentations/v19/sources/reports/2026-10-16/summary.json",
    }
    for name, source in source_files.items():
        target = OUT / "sources" / name
        shutil.copy2(source, target)
        V.record(target)
    for meta in previous["slides"]:
        path = OUT / "sources/v21-notes" / f'page-{meta["page"]:02}.txt'
        path.write_text(meta["notes"])
        V.record(path)
    route = V.load(OUT / "sources/routing-summary.json")
    prs = Presentation(BASE)
    originals = list(prs.slides._sldIdLst)
    metas, changes = [], []
    for page, old in enumerate(ORDER, 1):
        slide = prs.slides[old - 1]
        meta = {**previous["slides"][old - 1], "source": "v21", "v21_page": old,
                "page": page, "content_modified": False, "section": chapter(page)}
        for shape in slide.shapes:
            if shape.has_text_frame and Inches(.30) <= shape.top <= Inches(.36) and shape.text:
                replace_text(shape, shape.text, chapter(page))

        def change(before, after, size=None, slide=slide, meta=meta, page=page):
            edit(slide, before, after, size)
            meta["content_modified"] = True
            if meta["title"] == before:
                meta["title"] = after
            changes.append({"page": page, "before": before, "after": after})

        if old == 1:
            change("Strands Decider、Jev 与 GPT：原理、业务验证、领域微调、vLLM 部署与成本",
                   "为什么需要决策模型 → 业务实测 → 输入优化与领域微调 → 推理部署与成本")
            change("选得对", "验证效果")
            change("跑得动", "改善判断")
            change("算得清", "部署与成本")
            change("业务事实 → 候选动作\n核对证据和完成条件",
                   "支付客服与模型路由\n比较速度、选择与业务结果")
            change("单次延迟 → 并发吞吐\n看完整请求和尾延迟",
                   "上下文与条件 → 领域微调\n分别验证两条优化路线")
            change("自托管 → 按量 API\n加入 EC2 RI 长期承诺",
                   "vLLM 适配与并发性能\nEC2 按需 / 一年 RI / Jev")
        elif old == 3:
            change("适合高频、有限动作的判断；格式正确仍需验证选择是否符合业务事实。",
                   "典型场景：客服分流、工具选择、模型路由、证据核验；选择结果仍需业务验收。", 13.5)
        elif old == 7:
            change("相同候选问题，可以通过不同模型回答",
                   "BERT、决策模型与 GPT：输出方式决定适配方式")
            change("应用先定义问题和可执行选项；模型负责选择，执行器负责真正改变业务状态。",
                   "固定标签、动态候选与开放生成各有用途；下面进一步看 Decider 如何实现候选判断。")
        elif old == 4:
            slide.notes_slide.notes_text_frame.text = (
                "2026-10-21 公开 JevBench API 榜单截图，图中第7–9名为裁剪节选，未改动图内数据。"
                "Jev1.13 综合分71.491186；Intelligence63.620758、Calibration90.632202，"
                "Capability=(I+C)/2=77.12648。63.6是智能单轴，不是能力分。"
                "综合分同时考虑智能、校准、速度、成本并有低分轴惩罚，因此不等于正确率。"
                "API中位延迟约0.239s。正式榜单包含公开和封闭题，与本项目231题自测分开解释。"
                "\nhttps://benchmarkheaven.com/jev-models/api\n"
                "https://benchmarkheaven.com/jev-models/methodology\n"
                "来源快照：sources/jevbench-rows.json；截图：screenshots/jevbench-ranking-detail.png。"
            )
        elif old == 10:
            change("原版 v19 · g6.xlarge / L4 · 单并发 231 题 · 私网客户端计时",
                   "本项目原版 v19 · 231 道公开题 · 单并发私网计时；尚无完整榜单总分")
            change("先做领域验收；复杂条件保留复核与回退。",
                   "公开题正确率 ≠ 榜单总分；继续看业务实测。", 13)
        elif old == 12:
            change("EC2 g6.xlarge\nDecider v19", "EC2 g6.xlarge\nDecider / vLLM")
            change("VPC · us-east-1", "业务 VPC · us-east-1")
            slide.shapes[9].height = Inches(2.02)
            slide.shapes[11].height = Inches(1.52)
            L.rect(slide, 3.43, 4.92, 4.98, 1.27, None, V.PURPLE)
            L.text(slide, "共享 GPU VPC\n私网互联", 3.62, 5.12, 1.90, .74, 12, V.PURPLE, True)
            slide.notes_slide.notes_text_frame.text = (
                "当前演示前端为私有S3+CloudFront OAC，后端为ECS Fargate，用户名密码登录。"
                "ECS通过私网互联连接共享g6.xlarge vLLM服务。Bedrock负责GPT，Jev经Lambda适配。"
                "本次只更新材料，不变更部署。配置依据deployment/DEPLOYED.md。"
                "后续支付和路由沿用此前原版v19采集数据，未改称vLLM结果；"
                "原版/vLLM对照见本版P27，与Jev的公共API比较见P28。"
            )
        elif old == 15:
            change("五步都执行了，业务结果仍要单独验收",
                   "支付实测：Decider 更快，审批选择出现分歧")
            change("固定流程 · 每格为真实 choice / 后端毫秒；输入保持同一业务快照，按阶段更换问题与候选。",
                   "129 USDC 待确认 · 同一业务快照 · 每格为真实 choice / 后端毫秒")
        elif old == 14:
            change("原始固定流程：同一收款审批，三个模型怎样选择",
                   "为什么多等了一次审批？从实际请求看条件歧义")
        elif old == 16:
            change("怎么优化：删去不适用字段，把适用范围写进每个候选",
                   "输入优化：删去无关字段，明确每个候选的适用范围")
        elif old == 17:
            change("同一审批的实际变化：两次“等待人工审批”变为“通过”",
                   "优化后的实际变化：Decider 与 Jev 不再额外等待审批")
        elif old == 18:
            change("四场景一起重测：选择更一致，仍有两项 Decider 错误",
                   "四场景复测：Decider 参考一致从 14/20 提升到 18/20")
        elif old == 21:
            change("模型路由：同一个入口，按任务选择处理流程与模型",
                   "第二个任务：从选择支付动作，到选择执行模型")
            slide.notes_slide.notes_text_frame.text = (
                "四类任务只有任务原文，不额外给用户优先级。模型先选择处理流程，"
                "再根据候选模型能力、区域可用性与第三方评测证据选择具体型号。"
                "程序不按排名替换模型选择；三侧使用相同事实、候选ID和顺序。"
                "候选合法不代表型号适用。接下来依次看支付客服两步请求、四任务对照、"
                "双语索引误选。本版仅展示原始路由；三轮数据来自"
                "docs/results/model-evidence-2026-10-16/results.json。"
            )
        elif old == 22:
            change("流程判断一致，具体型号选择出现差异",
                   "四任务对照：流程选择一致，具体型号仍有差异")
            change("每格为所选型号与两次决策总耗时；GPT 决策器为 GPT-6 Sol low。",
                   "四任务各重复三轮 · 原始流程两次决策的后端均值 · GPT-6 Sol low")
            old_values = [
                ("support", ("461.0", "747.9", "1464.8")),
                ("code", ("522.6", "933.3", "6168.2")),
                ("analysis", ("504.6", "820.4", "12106.1")),
                ("retrieval", ("182.2", "801.7", "1900.8")),
            ]
            for case, values in old_values:
                for provider, value in zip(("decider", "jev", "bedrock"), values, strict=True):
                    new_value = route["cases"][case][provider]["original_route"]["mean_ms"]
                    change(f"{value} ms", f"{new_value:.1f} ms")
            for shape in slide.shapes:
                if shape.has_text_frame and "\n" in shape.text:
                    for paragraph in shape.text_frame.paragraphs:
                        paragraph.space_after = Pt(1)
                        paragraph.line_spacing = 1.05
            slide.notes_slide.notes_text_frame.text = (
                "本页统一使用2026-10-16原始路由三轮记录，与前一页客服使用同一批数据。"
                "每路由器四任务×三轮，先选workflow再选model，两步均为真实模型判断。"
                "三侧输入、候选ID和顺序相同，预热排除，值为workflow+model的backend_total_ms均值。"
                "不含所选模型的目标执行，也不将与GPT选型一致记为准确率。"
                "全部四任务在三轮中的型号选择稳定；双语索引的English选择与语言证据不符。"
                "下一页是同一任务的较早界面截图，保留实际输入与选择，不将其单次耗时当本页均值。"
                "\n来源：docs/results/model-evidence-2026-10-16/results.json；sources/routing-summary.json。"
            )
        elif old == 25:
            change("其他场景：只看最终业务效果、延迟与适用结论",
                   "扩展验证：局部选择可用，复杂证据与多步任务仍需补强")
        elif old == 27:
            change("微调闭环：从可验证标签，到独立测试和完整对局",
                   "进一步优化：用领域标签训练 LoRA 与决策头")
            change("俄罗斯方块：根据棋盘、当前方块、下一块和候选后果，选择一个落点。",
                   "支付已验证输入优化；下面用俄罗斯方块说明改动模型参数后，如何训练与验收。")
            slide.notes_slide.notes_text_frame.text += (
                "\n承接上文：支付只改输入字段和候选条件；此处是另一个领域的监督微调实测。"
                "Tetris效果不能直接当作支付或模型路由已提高准确率，两个业务尚需各自数据验收。"
            )
        elif old == 32:
            change("保留决策头，把单请求执行接到并发调度",
                   "效果验证之后，再用 vLLM 改善并发服务能力")
            change("这不是把 Decider 当聊天模型生成 JSON；适配使用官方模板、位置元数据与评分逻辑。",
                   "保留官方模板、位置元数据和决策头；导出、校验、启动服务，再与原版对齐。")
            slide.notes_slide.notes_text_frame.text += (
                "\n本版将重复部署架构合入P8。适配与性能实验使用原始Decider v19，"
                "不是将前述Tetris检查点作为本次vLLM压测对象。"
            )
        elif old == 36:
            change("相同 20 个支付输入循环提交；Decider 8192 token 预算；Jev 直连 API。",
                   "相同 20 个支付输入循环提交；低并发看响应，高并发同时看吞吐与排队。")
            slide.notes_slide.notes_text_frame.text += (
                "\n合并原并发说明：Decider这批短输入在8并发后吞吐进入平台，"
                "继续增加并发主要增加排队。8192为每批token预算；更大的预算未改善该短输入样本吞吐。"
            )
        elif old == 40:
            change("业务质量先过关，再在低延迟、自托管与按量之间选择",
                   "按任务选模型：先验证效果，再优化速度与成本")
            change("优化后支付：Decider 91.5 ms / 18/20；Jev 388.5 ms / 20/20；GPT 1271.9 ms / 20/20。",
                   "支付数据为优化输入后 20 次决策的均值与参考一致次数；选型仍需结合具体任务验收。")
            change("优先：分类、工具选择、分流。\n\n低单次延迟、可控自托管。\n稳定流量再评估 1 年 RI。\n证据与完成判断需补强。",
                   "优先：客服分流、工具选择。\n支付均值 91.5 ms/次。\n参考一致 18/20，条件可优化。\n领域微调可进一步改善判断。\n稳定流量再评估一年 RI。", 15.5)
            change("优先：核验、文字浏览器操作。\n\n低或波动流量，按量付费。\n本批业务效果更好。\n金额与路由硬约束仍复核。",
                   "优先：证据核验、多步操作。\n支付均值 388.5 ms/次。\n参考一致 20/20。\n无需管理 GPU，按输入量付费。\n金额与路由硬约束仍需复核。", 15.5)
            change("适合：回复、代码、复杂异常。\n\n本次支付参考一致 20/20。\n高频短选择要权衡延迟。\n开放任务质量需单独实测。",
                   "适合：回复、代码与复杂异常。\n支付均值 1271.9 ms/次。\n参考一致 20/20。\n高频短选择要权衡额外延迟。\n开放任务质量仍需单独验收。", 15.5)
            slide.notes_slide.notes_text_frame.text = (
                "支付汇总来自2026-10-15四场景、每场景五步的优化输入实测："
                "Decider91.5ms/18/20，Jev388.5ms/20/20，GPT1271.9ms/20/20。"
                "延迟为应用后端单次决策均值，参考一致只描述本批预设业务策略，"
                "不代表普遍正确率。Decider为原版v19历史采集。"
                "其他场景适用性以本版P20各自验证为依据；领域微调效果见P21–25，"
                "来自Tetris独立实验，不等于已完成支付或路由微调。"
                "Jev适合波动流量和按量计费，自托管在稳定负载下结合容量与RI评估。"
            )
        elif old == 41:
            slide.notes_slide.notes_text_frame.text = (
                "在线演示：https://d3i7ogfx5ml7dc.cloudfront.net，使用已有用户名密码。"
                "项目：https://github.com/milan9527/StrandsDecider；"
                "vLLM：https://github.com/milan9527/StrandsDeciderVLLM；"
                "微调：https://github.com/milan9527/StrandsDeciderTetrisFinetuning。"
                "截图、实验记录和分批统计来源见前文备注及本版讲稿。"
                "后接候选顺序诊断附录，标为internal only。"
            )
        if old == 4:
            meta["notes_modified"] = True
        meta["notes"] = slide.notes_slide.notes_text_frame.text
        metas.append(meta)
    for node in list(prs.slides._sldIdLst):
        prs.slides._sldIdLst.remove(node)
        if node not in [originals[i - 1] for i in ORDER]:
            prs.part.drop_rel(node.rId)
    for old in ORDER:
        prs.slides._sldIdLst.append(originals[old - 1])
    assert len(prs.slides) == PAGES
    shortened = []
    for page, (slide, meta) in enumerate(zip(prs.slides, metas, strict=True), 1):
        for shape in slide.shapes:
            if shape.has_text_frame and shape.top > Inches(7) and re.fullmatch(r"\d+ / \d+", shape.text):
                replace_text(shape, shape.text, f"{page:02} / {PAGES}")
        notes = slide.notes_slide.notes_text_frame.text
        if len(notes) > 2600:
            notes = notes.split("\n{", 1)[0].split("\n[", 1)[0]
            if len(notes) > 2350:
                notes = notes[:2300].rsplit("。", 1)[0] + "。"
            notes += f'\n完整原备注：sources/v21-notes/page-{meta["v21_page"]:02}.txt。'
            shortened.append(page)
        slide.notes_slide.notes_text_frame.text = notes
        meta["notes"] = notes
    prs.core_properties.title = "Strands Decider：从业务验证到优化部署"
    prs.core_properties.subject = "v22 / 34 slides / coherent narrative with redundant sections consolidated"
    prs.core_properties.modified = datetime.now(UTC)
    target = OUT / f"{NAME}.pptx"
    prs.save(target)
    assert V.digest(BASE) == base_hash
    save(OUT / "build-manifest.json", {
        "created_at": datetime.now(UTC).isoformat(), "pages": PAGES,
        "base_file": str(BASE.relative_to(ROOT)), "base_sha256": base_hash,
        "pptx_file": str(target.relative_to(ROOT)), "pptx_sha256": V.digest(target),
        "v21_order": ORDER, "removed_v21_pages": REMOVED, "slides": metas,
        "changes": changes, "evidence": V.EVIDENCE, "internal_only_pages": [34],
        "shortened_notes_pages": shortened, "all_retained_images_unchanged": True,
        "new_model_calls": 0, "new_training_jobs": 0,
    })
    lines = ["# v22 讲稿与来源", "", "v21 的 42 页整合为 34 页。", "",
             "故事线：问题与模型原理 → 业务表现 → 输入优化 → 领域微调 → 部署性能 → 成本与选型。",
             "原页完整备注保存在 sources/v21-notes；当前正文的历史数据按各自批次解释。", ""]
    for meta in metas:
        lines += [f'## {meta["page"]:02} · {meta["title"]}', "", meta["notes"], ""]
    (OUT / "讲稿与来源.md").write_text("\n".join(lines))
    report = ["# 内容整合与页码对照", "", "## 合并或移出主线", "",
              "| v21 页面 | 处理 |", "| --- | --- |"]
    report += [f"| P{page} | {why} |" for page, why in REMOVED.items()]
    report += ["", "## 新版顺序", "", "| v22 | v21 | 页面 |", "| --- | --- | --- |"]
    report += [f'| P{m["page"]} | P{m["v21_page"]} | {m["title"]} |' for m in metas]
    report += ["", "路由四任务表统一使用已保存的 2026-10-16 三轮原始流程均值，"
               "与客服两步详解来自同一批次；未增加优化路由章节、未发起模型调用。",
               "架构统一为当前共享 vLLM 服务；历史支付/路由性能仍注明原始部署。"
               "微调与 vLLM 的图、数据、统计边界分别保留，避免把两个实验混为同一次部署。",
               "删除重复旧榜单表，避免将 Intelligence 63.6 与 Capability 77.1 混用。"]
    (OUT / "调整说明.md").write_text("\n".join(report) + "\n")
    (OUT / "README.md").write_text(
        "# v22 · 从业务验证到优化部署\n\n"
        f"[PPTX](./{NAME}.pptx) · [PDF](./{NAME}.pdf) · "
        "[全篇预览](./slides-overview.png) · [讲稿](./讲稿与来源.md) · [调整说明](./调整说明.md)\n\n"
        "**基于 v21，由 42 页精简为 34 页。** 所有保留的演示截图、流程图和成本图继续复用。\n\n"
        "| 页码 | 故事线 |\n| --- | --- |\n"
        "| P1–7 | 市场趋势 → 类型化决策 → BERT/决策模型/GPT → Decider 原理 → 榜单与公开集 |\n"
        "| P8 | 统一的 AWS / 共享 vLLM 架构 |\n"
        "| P9–15 | 支付客服：发现差异 → 实际请求 → 修改条件 → 前后截图 → 四场景复测与剩余错误 |\n"
        "| P16–19 | 模型路由：任务与流程 → 客服两步实测 → 四任务对照 → 双语索引误选 |\n"
        "| P20 | 其他场景最终结果与适用边界 |\n"
        "| P21–25 | 领域微调：流程 → 标签样本 → 参数更新 → 独立测试 → 完整对局 |\n"
        "| P26–28 | vLLM 部署、原版性能对照及与 Jev 的并发对比 |\n"
        "| P29–31 | Jev / EC2 按需 / 一年 RI 计费与两张成本交叉图 |\n"
        "| P32–34 | 选型结论、演示与仓库链接、internal only 附录 |\n\n"
        "合并重复的通用流程、榜单评分说明、支付耗时总结、部署图及并发说明。"
        "客服闭环展开移出主线，固定流程的未完成状态和执行保护继续保留。"
        "原未来微调建议并入已完成的微调流程。"
        "本版没有增加模型路由优化章节，也没有重新调用或训练模型。\n\n"
        "路由总览使用与客服页相同的三轮原始记录，避免单次与均值混用。"
        "输入优化和微调的实测指标分别保留，Tetris 效果不外推为支付或路由准确率。"
        "**P34 保留 internal only；未推送 GitHub。**\n\n"
        "[构建清单](./build-manifest.json) · [核验记录](./render-audit.json)。"
        "脚本：`scripts/build_presentation_v22.py`、`scripts/verify_presentation_v22.py`。\n"
    )
    print(json.dumps({"pptx": str(target), "pages": PAGES, "removed": list(REMOVED)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
