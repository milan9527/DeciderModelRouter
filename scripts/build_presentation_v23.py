"""Add two image-decision slides to v22 using recorded API results and screenshots."""

import json
import re
import shutil
from datetime import UTC, datetime

import build_presentation_v10 as V
import presentation_layout as L
from build_presentation_v5 import replace_text
from PIL import Image
from pptx import Presentation
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches

ROOT = V.ROOT
OUT = ROOT / "docs/presentations/v23"
BASE = next((ROOT / "docs/presentations/v22").glob("*.pptx"))
NAME = "Strands-Decider-v23-业务验证与图片决策对比-36页-20261023"
PAGES = 36
INSERT_AFTER = 19
PROVIDERS = ("decider", "jev", "bedrock")
COLORS = (L.TEAL, V.PURPLE, L.BLUE)


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def chapter(page):
    if page <= 7:
        return "01  为什么需要决策模型"
    if page == 8:
        return "02  三模型演示架构"
    if page <= 15:
        return "03  支付客服 · 从误选到输入优化"
    if page <= 19:
        return "04  模型路由 · 看速度，也看选择"
    if page <= 21:
        return "05  图片决策 · 从截图到业务动作"
    if page == 22:
        return "06  其他场景 · 适用边界"
    if page <= 27:
        return "07  领域微调 · 从数据到结果"
    if page <= 30:
        return "08  vLLM · 部署与性能"
    if page <= 33:
        return "09  Jev 与 EC2 · 成本选择"
    return "10  总结与资料" if page < 36 else "附录  internal only · 候选顺序诊断"


def remap_notes(text, old_page):
    # Only references to the current presentation, not historical "P95" metrics.
    if old_page == 8:
        text = text.replace("本版P27", "本版P29").replace("比较见P28", "比较见P30")
        text += (
            "\n此图描述文字决策接入；新增图片决策见P20–21，"
            "使用独立的官方视觉推理服务，尚非vLLM视觉适配。"
        )
    if old_page == 32:
        text = text.replace("本版P20", "本版P22").replace("见P21–25", "见P23–27")
    return text


def main():
    for folder in ("assets", "sources/v22-notes", "screenshots"):
        (OUT / folder).mkdir(parents=True, exist_ok=True)
    V.OUT, V.ASSETS, V.SOURCES = OUT, OUT / "assets", OUT / "sources"
    V.EVIDENCE.clear()
    V.IMAGES.clear()
    V.SLIDES.clear()
    base_hash = V.digest(BASE)
    previous = V.load(BASE.parent / "build-manifest.json")
    for item in previous["evidence"]:
        source = ROOT / item["path"]
        assert V.digest(source) == item["sha256"], source
        if source.is_relative_to(BASE.parent):
            target = OUT / source.relative_to(BASE.parent)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            V.record(target)
        else:
            V.record(source)
    for meta in previous["slides"]:
        target = OUT / "sources/v22-notes" / f'page-{meta["page"]:02}.txt'
        target.write_text(meta["notes"])
        V.record(target)

    provenance = []
    for relative, destination in (
        ("docs/results/vision-triple-2026-10-23.json", "sources/vision-triple-2026-10-23.json"),
        ("docs/results/vision-triple-browser-2026-10-23.json", "sources/vision-browser-2026-10-23.json"),
        ("docs/VISION.md", "sources/VISION.md"),
        ("app/vision_assets/catalog.json", "sources/vision-catalog.json"),
        ("docs/images/vision/triple-receipt.png", "screenshots/vision-triple-receipt.png"),
        ("frontend/static/vision/case-11.png", "screenshots/receipt-case-11.png"),
    ):
        source, target = ROOT / relative, OUT / destination
        shutil.copy2(source, target)
        V.record(target)
        provenance.append({
            "source": relative, "snapshot": str(target.relative_to(ROOT)),
            "sha256": V.digest(target),
        })
    results = V.load(OUT / "sources/vision-triple-2026-10-23.json")
    summary = results["summary"]
    assert len(results["results"]) == 36 and len(results["warmup"]) == 3
    for provider in PROVIDERS:
        rows = [r for r in results["results"] if r["provider"] == provider]
        assert len(rows) == 12 and all(r["status"] == 200 for r in rows)
        assert summary[provider]["planned_questions"] == 28
        mean = sum(r["response"]["backend_total_ms"] for r in rows) / len(rows)
        assert abs(mean - summary[provider]["mean_backend_ms"]) < 1e-8
    for case_id in {r["case_id"] for r in results["results"]}:
        group = [r["response"] for r in results["results"] if r["case_id"] == case_id]
        assert len({r["input_fingerprint"] for r in group}) == 1
    assert [summary[p]["correct"] for p in PROVIDERS] == [27, 28, 28]
    browser = V.load(OUT / "sources/vision-browser-2026-10-23.json")
    screenshot_calls = [
        c["body"] for c in browser["calls"]
        if c["body"]["case_id"] == "case-11" and c["body"]["variant"] == "original"
    ]
    assert len(screenshot_calls) == 3
    assert {c["provider"]: c["answers"]["action"]["choice"] for c in screenshot_calls} == {
        "decider": "accept", "jev": "request_reference", "bedrock": "request_reference",
    }
    screenshot = OUT / "screenshots/vision-triple-receipt.png"
    crops = []
    for provider, crop in zip(PROVIDERS, (
        (261, 714, 685, 1174), (701, 714, 1125, 1146), (1140, 714, 1564, 1137),
    ), strict=True):
        cropped = OUT / "assets" / f"vision-receipt-{provider}.png"
        with Image.open(screenshot) as image:
            image.crop(crop).save(cropped)
        V.record(cropped)
        crops.append((cropped, crop))
        V.IMAGES.append({
            "source": str(screenshot.relative_to(ROOT)), "crop": list(crop),
            "output": str(cropped.relative_to(ROOT)), "sha256": V.digest(cropped),
            "operation": "crop only; all displayed model outputs and timings unchanged",
        })

    prs = Presentation(BASE)
    original_ids = list(prs.slides._sldIdLst)
    new_meta = []

    def new(title, subtitle, notes):
        slide = V.base(prs, chapter(20), title, subtitle, "", notes)
        # The user removed the explanatory footer row in earlier versions.
        for shape in list(slide.shapes):
            if shape.has_text_frame and Inches(6.5) <= shape.top < Inches(7.03):
                slide.shapes._spTree.remove(shape._element)
        new_meta.append({
            "source": "new", "v22_page": None, "title": title,
            "content_modified": True, "notes": notes,
        })
        return slide

    notes20 = (
        "承接文字模型路由，第三个业务演示直接将截图提交给三个模型。"
        "覆盖支付状态4图×3问题、中文客服4图×1问题、凭证完整性4图×3问题，共12图28项判断。"
        "预设或上传图片统一成448×448 PNG，三侧图片字节、上下文、问题、候选含义与顺序一致；"
        "不预先OCR，样例预期不发送给模型。每模型一次请求独立回答同组问题，"
        "不是先用字段答案改写动作问题，也不真正执行付款或接收凭证。"
        "\nDecider沿用v19官方视觉编码器和决策头，在EC2 g6.xlarge/L4独立容器运行；"
        "不是本项目文字vLLM服务的图片适配。Jev实际返回jev-1.14.0，"
        "经ECS→Lambda→System One提交图片asset；该图片API为试验功能。"
        "GPT-6.1 Sol low经Bedrock Converse图片块返回候选ID或Boolean。"
        "\n截图为2026-10-23上线浏览器实时验收case-11，裁掉导航、其他字段和折叠区，"
        "保留原有三列模型名称、后端/浏览器耗时、动作和预期提示，未修改图片内返回。"
        "本次截图后端耗时Decider172.2ms/Jev420.1ms/GPT约1.01s。"
        "下一页均值来自另存的12图完整批次，不用截图单次时间替代批次均值。"
        "三者都将signature判为true、reference判为false；Decider动作accept，"
        "Jev/GPT动作request_reference。Decider字段识别正确，但组合条件动作未符合样例预期。"
        "这只能定位可观察的差异，不能据此断言模型内部推理的具体原因。"
        "\n来源：sources/vision-browser-2026-10-23.json、sources/vision-catalog.json、"
        "screenshots/vision-triple-receipt.png、sources/VISION.md。"
        "\n演示：https://d3i7ogfx5ml7dc.cloudfront.net/#vision"
        "\n官方视觉能力：https://github.com/strands-labs/strands-decider/blob/main/docs/vision.md"
        "\nJev图片接口：https://docs.typesafe.ai/images"
    )
    s = new(
        "第三个任务：读懂图片，直接选择业务动作",
        "支付状态、中文客服分流、凭证完整性；三模型收到同一图片、问题与候选。",
        notes20,
    )
    flow_items = [
        ("同一张图片", "448×448 PNG · 直接读取图像", "scan-eye"),
        ("一次提交多项判断", "共用上下文、问题和候选条件", "list-filter"),
        ("比较选择与耗时", "保留候选 ID / 是或否及实际返回", "arrow-left-right"),
    ]
    for i, (title, body, icon) in enumerate(flow_items):
        x = .68 + i * 4.06
        L.rect(s, x, 2.12, 3.83, .84, L.WHITE, L.LINE, True)
        V.icon(s, icon, x + .14, 2.27, COLORS[i], .30)
        L.text(s, title, x + .56, 2.22, 3.11, .30, 16, COLORS[i], True)
        L.text(s, body, x + .56, 2.61, 3.11, .25, 11.7, L.MUTED)
        if i < 2:
            L.line(s, [(x + 3.87, 2.54), (x + 4.02, 2.54)], L.MUTED, 1.4, True)

    L.rect(s, .68, 3.18, 2.68, 2.99, L.WHITE, L.LINE, True)
    L.text(s, "实际输入：编号缺失", .85, 3.32, 2.36, .31, 16, L.NAVY, True)
    L.picture(s, OUT / "screenshots/receipt-case-11.png", .88, 3.73, 2.28, 1.58)
    L.text(s, "接收条件\n交易编号和签名必须齐全。",
           .86, 5.42, 2.33, .64, 13.2, L.INK)
    for i, (cropped, crop) in enumerate(crops):
        height = 2.97 * (crop[3] - crop[1]) / (crop[2] - crop[0])
        L.picture(s, cropped, 3.54 + i * 3.075, 3.18, 2.97, height)
    # This is part of the case analysis, not a methodology footer.
    L.text(s, "三者均识别出缺少编号；Decider 选“接收”，Jev / GPT 选“补编号”。",
           3.58, 6.46, 9.05, .28, 13.4, L.TEAL, True)

    notes21 = (
        "2026-10-23T04:07:59Z至04:08:22Z，通过线上业务API实测12张清晰合成截图。"
        "每模型先预热一次，正式请求按图片顺序执行，同一图三模型并发；"
        "共3次预热另计、36次正式请求全部HTTP200，每模型12次请求、28项独立判断。"
        "12组input_fingerprint逐一相同。各次请求包含1项或3项独立判断，"
        "延迟为backend_total_ms整次业务API耗时，不能称为单问题或逐步推理延迟。"
        "包括服务等待、图片处理及模型往返；Jev包括Lambda转发，GPT为Bedrock调用。"
        "不包含浏览器至API网络；没有测本批并发吞吐或长期尾延迟。"
        "\n总体：Decider27/28、均值178.1432ms、中位数171.1785ms；"
        "Jev28/28、均值424.6036ms、中位数397.9985ms；"
        "GPT28/28、均值1539.1652ms、中位数1208.3675ms。"
        "支付：三侧12/12，均值170.2193/469.5495/2107.3458ms。"
        "客服：三侧4/4，均值188.8950/418.6625/1273.2473ms。"
        "凭证：11/12、12/12、12/12，均值175.3153/385.5988/1236.9025ms。"
        "\n符合预期计数按调用前冻结的样例标签，每问题一次评分；"
        "noul按0.5转换是/否。GPT只返回Boolean/候选，不补造概率。"
        "唯一不符合为case-11动作：Decideraccept，预期request_reference；"
        "它的两个字段答案与Jev/GPT一致。对高频清晰截图的分流与状态判断，"
        "Decider本批响应更快且符合预期；凭证接收涉及组合条件，需要单独验收。"
        "后续可优化条件表达、增加缺失字段反例，并按领域样本微调后复测；"
        "本次没有更新权重，没有宣称已消除该错误。"
        "12图28项是小样本验收，不代表真实票据通用准确率或生产SLA。"
        "\n数据：sources/vision-triple-2026-10-23.json；"
        "方法与部署：sources/VISION.md。"
        "\n报告：https://github.com/milan9527/StrandsDecider/blob/main/docs/VISION.md"
    )
    s = new(
        "图片实测：Decider 响应最快，27/28 项符合预期",
        "12 张合成截图 · 每模型 28 项判断 · 36/36 次调用成功；耗时为后端整次请求耗时。",
        notes21,
    )
    for i, (provider, title, color) in enumerate(zip(
        PROVIDERS, ("Decider · v19 官方视觉", "Jev · 1.14.0", "GPT-6.1 Sol · low"), COLORS, strict=True,
    )):
        x = .68 + i * 4.06
        value = summary[provider]
        L.rect(s, x, 2.17, 3.83, 1.55, L.WHITE, L.LINE, True)
        L.rect(s, x, 2.17, .045, 1.55, color)
        L.text(s, title, x + .17, 2.30, 3.49, .32, 16.5, color, True)
        L.text(s, f'{value["mean_backend_ms"]:,.1f} ms',
               x + .17, 2.78, 2.34, .44, 29, color, True)
        L.text(s, f'{value["correct"]}/28', x + 2.64, 2.81, 1.01, .39,
               23, color, True, PP_ALIGN.RIGHT)
        L.text(s, "平均后端耗时", x + .18, 3.38, 2.15, .22, 11.5, L.MUTED)
        L.text(s, "符合样例预期", x + 2.47, 3.38, 1.16, .22,
               11.5, L.MUTED, align=PP_ALIGN.RIGHT)

    L.text(s, "按场景比较：符合预期数 / 判断数 · 平均请求耗时",
           .70, 3.98, 11.9, .30, 14, L.MUTED)
    rows = []
    for group, label in (("payment", "支付状态"), ("support", "中文客服分流"), ("receipt", "凭证完整性")):
        row = [label]
        for provider in PROVIDERS:
            value = summary[provider]["groups"][group]
            row.append(f'{value["correct"]}/{value["questions"]} · {value["mean_backend_ms"]:,.1f} ms')
        rows.append(row)
    V.table(s, ["场景", "Decider", "Jev", "GPT"], rows, [2.03, 3.34, 3.32, 3.31],
            x=.68, y=4.40, row_h=.42, size=15)
    V.callout(s, "Decider 适合高频截图分流与状态判断；凭证组合条件仍需优化、复测。",
              y=6.16, size=15)

    new_ids = list(prs.slides._sldIdLst)[len(original_ids):]
    order = original_ids[:INSERT_AFTER] + new_ids + original_ids[INSERT_AFTER:]
    for node in list(prs.slides._sldIdLst):
        prs.slides._sldIdLst.remove(node)
    for node in order:
        prs.slides._sldIdLst.append(node)
    metas = []
    for index, slide in enumerate(prs.slides, 1):
        old = index if index <= INSERT_AFTER else index - 2 if index > INSERT_AFTER + 2 else None
        if old:
            meta = {
                **previous["slides"][old - 1], "source": "v22", "v22_page": old,
                "content_modified": False,
            }
            slide.notes_slide.notes_text_frame.text = remap_notes(
                slide.notes_slide.notes_text_frame.text, old,
            )
        else:
            meta = new_meta[index - INSERT_AFTER - 1]
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            if Inches(.30) <= shape.top <= Inches(.36) and shape.text:
                replace_text(shape, shape.text, chapter(index))
            if shape.top > Inches(7) and re.fullmatch(r"\d+ / \d+", shape.text):
                replace_text(shape, shape.text, f"{index:02} / {PAGES}")
        meta.update({
            "page": index, "section": chapter(index),
            "notes": slide.notes_slide.notes_text_frame.text,
        })
        assert len(meta["notes"]) <= 2600
        metas.append(meta)
    assert len(prs.slides) == PAGES
    prs.core_properties.title = "Strands Decider：业务验证与图片决策对比"
    prs.core_properties.subject = "v23 / 36 slides / two image-decision slides added to v22"
    prs.core_properties.modified = datetime.now(UTC)
    target = OUT / f"{NAME}.pptx"
    prs.save(target)
    assert V.digest(BASE) == base_hash
    save(OUT / "build-manifest.json", {
        "created_at": datetime.now(UTC).isoformat(), "pages": PAGES,
        "base_file": str(BASE.relative_to(ROOT)), "base_sha256": base_hash,
        "pptx_file": str(target.relative_to(ROOT)), "pptx_sha256": V.digest(target),
        "insert_after_v22_page": INSERT_AFTER, "new_pages": [20, 21], "slides": metas,
        "evidence": V.EVIDENCE, "new_source_provenance": provenance, "new_images": V.IMAGES,
        "internal_only_pages": [36], "all_34_retained_bodies_unchanged": True,
        "all_retained_images_unchanged": True, "new_model_calls": 0,
        "new_training_jobs": 0, "vision_models": {p: summary[p]["models"] for p in PROVIDERS},
    })
    lines = [
        "# v23 讲稿与来源", "",
        "在 v22 模型路由之后加入 P20–21 图片决策。原 34 页正文、数据和图片保留，"
        "章号、页码及当前版本的备注交叉引用顺延。", "",
    ]
    for meta in metas:
        lines += [f'## {meta["page"]:02} · {meta["title"]}', "", meta["notes"], ""]
    (OUT / "讲稿与来源.md").write_text("\n".join(lines))
    report = [
        "# v23 调整说明", "",
        "以 v22 的 34 页为基础，只新增两页，放在模型路由之后、其他场景验证之前。", "",
        "| v23 | v22 | 内容 |", "| --- | --- | --- |",
        "| P1–19 | P1–19 | 原有内容保留 |",
        "| P20 | 新增 | 图片决策流程、输入图片、三模型实际返回截图与凭证动作差异 |",
        "| P21 | 新增 | 12 图、28 项判断的三模型速度与效果；按三个场景分列 |",
        "| P22–36 | P20–34 | 原有内容保留，页码与章节顺延 |", "",
        "P20 截图来自 2026-10-23 浏览器实时验收；P21 来自当日完整 12 图批次。"
        "截图仅裁剪，保留原始单次耗时，不替换为完整批次均值。"
        "实验方法、接口路径、样本范围及小样本结论放在讲稿与备注中。", "",
        "图片 Decider 是 EC2 上的官方 v19 视觉推理，文字模块继续使用共享 vLLM。"
        "本版没有新增模型调用、训练、部署或 GitHub 推送。"
        "P36 延续 internal only 标注。原始版本及原备注保留。", "",
    ]
    (OUT / "调整说明.md").write_text("\n".join(report))
    (OUT / "README.md").write_text(
        "# v23 · 业务验证与图片决策对比\n\n"
        f"[PPTX](./{NAME}.pptx) · [PDF](./{NAME}.pdf) · "
        "[全篇预览](./slides-overview.png) · [新增两页](./updated-overview.png) · "
        "[讲稿与来源](./讲稿与来源.md) · [调整说明](./调整说明.md)\n\n"
        "**基于 v22，新增图片决策两页，共 36 页。** 放在模型路由之后；"
        "其余正文、数据、截图和流程图保留。\n\n"
        "- **P20：图片决策如何调用。** 三类场景、同图同问题的输入方式，"
        "以及缺少交易编号时 Decider / Jev / GPT 的实际选择截图。\n"
        "- **P21：速度与业务效果。** 12 图、每模型 28 项判断，"
        "Decider 178.1 ms / 27/28；Jev 424.6 ms / 28/28；"
        "GPT 1,539.2 ms / 28/28，并列出支付、中文客服、凭证三个场景。\n\n"
        "耗时为每张图片整组问题的后端请求均值；3 次预热另计，"
        "36 次正式请求全部成功。结果只描述本批清晰合成截图。"
        "P20 浏览器截图的单次返回和 P21 完整批次均值分别保留来源。\n\n"
        "| 页码 | 内容 |\n| --- | --- |\n"
        "| P1–8 | 决策模型趋势、原理、BERT / GPT 对比、榜单与架构 |\n"
        "| P9–15 | 支付客服原始流程、条件优化、结果与剩余差异 |\n"
        "| P16–19 | 模型路由原始流程与三模型对比 |\n"
        "| P20–21 | 图片决策：实际输入输出、延迟与结果 |\n"
        "| P22 | 其他场景验证结果 |\n"
        "| P23–27 | 领域微调流程、标签、训练与效果 |\n"
        "| P28–30 | vLLM 部署与并发性能 |\n"
        "| P31–33 | EC2 按需、一年 RI 与 Jev 成本 |\n"
        "| P34–36 | 选型、演示链接、internal only 附录 |\n\n"
        "[图片模块文档](../../VISION.md) · "
        "[正式批次](./sources/vision-triple-2026-10-23.json) · "
        "[浏览器实测](./sources/vision-browser-2026-10-23.json) · "
        "[构建清单](./build-manifest.json) · [核验记录](./render-audit.json)\n"
    )
    print(json.dumps({"pptx": str(target), "pages": PAGES, "new_pages": [20, 21]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
