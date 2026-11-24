"""Apply the requested cover, ordering and diagram edits to v24."""

import json
import re
import shutil
from datetime import UTC, datetime

import build_presentation_v10 as V
import presentation_layout as L
from build_presentation_v5 import replace_text
from pptx import Presentation
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches

ROOT = V.ROOT
OUT = ROOT / "docs/presentations/v25"
BASE = next((ROOT / "docs/presentations/v24").glob("*.pptx"))
TITLE = "Strands Decider决策模型与Jev的对比测试和业务场景演示"
NAME = "Strands-Decider-v25-决策模型对比与业务场景演示-41页-20261026"
PAGES = 41
REMOVED_TEXT = "同一个 approved，可能来自分类头、候选评分或文本生成；都需要核对业务条件。"
ARCHITECTURE = "docs/diagrams/agentcore-gpu-architecture.png"
ARCHITECTURE_URL = (
    "https://github.com/milan9527/StrandsDecider/blob/main/"
    "docs/diagrams/agentcore-gpu-architecture.svg"
)
# Page references in the request refer to v24.
ORDER = [None, *range(1, 8), *range(9, 16), *range(17, 24), 8, *range(24, 42)]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def chapter(page):
    if page == 1:
        return "封面"
    if page <= 8:
        return "01  快速决策 · 大模型的补充"
    if page == 9:
        return "02  三模型演示架构"
    if page <= 15:
        return "03  支付客服 · 从误选到输入优化"
    if page <= 19:
        return "04  模型路由 · 看速度，也看选择"
    if page <= 21:
        return "05  图片决策 · 从截图到业务动作"
    if page == 22:
        return "06  其他场景 · 适用边界"
    if page == 23:
        return "07  适用场景 · 已实现与潜在应用"
    if page <= 28:
        return "08  领域微调 · 从数据到结果"
    if page <= 31:
        return "09  vLLM · 部署与性能"
    if page <= 35:
        return "10  AgentCore · GPU Runtime 与 Agent 协作"
    if page <= 38:
        return "11  Jev 与 EC2 · 成本选择"
    return "12  总结与资料" if page < 41 else "附录  internal only · 候选顺序诊断"


def update_notes(text, old_page):
    if old_page == 4:
        text = text.replace(REMOVED_TEXT, "")
    if old_page == 8:
        text = text.replace("实测在后续页展开", "实测已在前面的演示页展开")
    if old_page == 9:
        text = text.replace("图片决策见P21–22", "图片决策见P20–21")
    if old_page == 39:
        text = text.replace("本版P23", "本版P22")
    return text


def main():
    for folder in ("assets", "sources/v24-notes"):
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
        target = OUT / "sources/v24-notes" / f'page-{meta["page"]:02}.txt'
        target.write_text(meta["notes"])
        V.record(target)
    for ext in ("png", "svg"):
        source = ROOT / "docs/diagrams" / f"agentcore-gpu-architecture.{ext}"
        target = OUT / "assets" / source.name
        shutil.copy2(source, target)
        V.record(target)

    prs = Presentation(BASE)
    original_ids = list(prs.slides._sldIdLst)
    cover = prs.slides.add_slide(prs.slide_layouts[6])
    cover.background.fill.solid()
    cover.background.fill.fore_color.rgb = L.rgb(L.NAVY)
    L.rect(cover, .0, .0, .18, 7.5, L.TEAL)
    L.rect(cover, .76, .87, .56, .065, L.GREEN)
    L.text(cover, "决策模型 · 实测与应用", .78, 1.18, 11.7, .42, 18, "B8D1D9", True)
    L.text(
        cover,
        "Strands Decider决策模型与Jev的\n对比测试和业务场景演示",
        .76, 2.33, 11.85, 1.79, 38, L.WHITE, True,
    )
    L.text(cover, "模型原理  /  业务验证  /  优化与部署",
           .80, 4.72, 11.55, .45, 21, "B8D1D9")
    for x, label, icon in (
        (.82, "快速决策", "timer"),
        (4.78, "业务场景", "workflow"),
        (8.73, "工程实践", "layers"),
    ):
        L.rect(cover, x, 5.76, 3.61, .76, "203A4B", radius=True)
        V.icon(cover, icon, x + .21, 5.96, "BEDD5C", .32)
        L.text(cover, label, x + .72, 5.96, 2.5, .35, 18, L.WHITE, True)
    L.text(cover, "01 / 41", 11.64, 7.12, 1.0, .29, 11, "B8D1D9", True, PP_ALIGN.RIGHT)
    cover.notes_slide.notes_text_frame.text = (
        TITLE + "。\n新增封面；原v24首页的Jev官方速度数据保留在本版P2。"
        "本版调整基于v24页码，保留业务实测、客户案例归属和AgentCore验证边界。"
    )
    cover_id = prs.slides._sldIdLst[-1]

    # Remove exactly the requested callout, including its otherwise empty backing.
    bert = prs.slides[3]
    matches = [s for s in bert.shapes if s.has_text_frame and s.text == REMOVED_TEXT]
    assert len(matches) == 1
    removed_shapes = []
    for shape in list(bert.shapes):
        if (shape.has_text_frame and shape.text == REMOVED_TEXT) or (
            shape.shape_type == 1 and shape.top == Inches(6.03)
            and shape.left == Inches(.68) and shape.width == Inches(11.98)
        ):
            removed_shapes.append(shape._element.xml)
            bert.shapes._spTree.remove(shape._element)
    assert len(removed_shapes) == 2

    # Use the original complete diagram unchanged; no cropping or redrawing.
    architecture = prs.slides[32]
    for shape in list(architecture.shapes):
        if shape.top < Inches(7.03):
            architecture.shapes._spTree.remove(shape._element)
    architecture.background.fill.solid()
    architecture.background.fill.fore_color.rgb = L.rgb(L.WHITE)
    png = OUT / "assets/agentcore-gpu-architecture.png"
    L.picture(architecture, png, .10, .03, 13.13, 6.96)
    picture = architecture.shapes[-1]
    picture.click_action.hyperlink.address = ARCHITECTURE_URL
    architecture.notes_slide.notes_text_frame.text = (
        "本页直接使用原完整AgentCore GPU EC2架构图，未裁剪、重绘或改写图片内文字。"
        "PNG与docs/diagrams/agentcore-gpu-architecture.png字节一致；SVG另存于assets。"
        "点击整图可打开主项目的SVG源文件。\n"
        "图中实线为已验证的GPU Runtime、单实例业务会话复用、S3制品、观测与评估，"
        "灰色虚线为后续优化流程。公网子网与无入站规则保持实际部署口径。"
        "一个原生session对应一台EC2，八个业务会话由应用管理。"
        "原生CUDA forward串行，24次决策成功；模型均值80ms，单并发Runtime往返209ms。"
        "指标为2026-10-26预热后小样本，不是持续吞吐或生产准确率。\n"
        "完整部署说明：https://github.com/milan9527/StrandsDecider/blob/main/docs/AGENTCORE_GPU.md"
    )

    assert len(ORDER) == PAGES and 16 not in ORDER
    ids = [cover_id if old is None else original_ids[old - 1] for old in ORDER]
    keep = {node.rId for node in ids}
    for node in list(prs.slides._sldIdLst):
        prs.slides._sldIdLst.remove(node)
        if node.rId not in keep:
            prs.part.drop_rel(node.rId)
    for node in ids:
        prs.slides._sldIdLst.append(node)

    metas = []
    for page, (old, slide) in enumerate(zip(ORDER, prs.slides, strict=True), 1):
        if old is None:
            meta = {"source": "new", "v24_page": None, "title": TITLE, "content_modified": True}
        else:
            meta = {
                **previous["slides"][old - 1],
                "source": "v24_reworked" if old in (4, 33) else "v24",
                "v24_page": old, "content_modified": old in (4, 33),
            }
            if old == 33:
                meta["title"] = "AgentCore GPU EC2：原完整架构图"
            slide.notes_slide.notes_text_frame.text = update_notes(
                slide.notes_slide.notes_text_frame.text, old)
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            if Inches(.30) <= shape.top <= Inches(.36) and shape.text:
                replace_text(shape, shape.text, chapter(page))
            if shape.top > Inches(7) and re.fullmatch(r"\d+ / \d+", shape.text):
                replace_text(shape, shape.text, f"{page:02} / {PAGES}")
        meta.update(page=page, section=chapter(page), notes=slide.notes_slide.notes_text_frame.text)
        assert len(meta["notes"]) <= 2600
        metas.append(meta)
    prs.core_properties.title = TITLE
    prs.core_properties.subject = "v25 / cover, reordered use cases and original AgentCore architecture"
    prs.core_properties.modified = datetime.now(UTC)
    target = OUT / f"{NAME}.pptx"
    prs.save(target)
    assert V.digest(BASE) == base_hash
    save(OUT / "build-manifest.json", {
        "created_at": datetime.now(UTC).isoformat(), "pages": PAGES,
        "base_file": str(BASE.relative_to(ROOT)), "base_sha256": base_hash,
        "pptx_file": str(target.relative_to(ROOT)), "pptx_sha256": V.digest(target),
        "new_pages": [1], "modified_pages": [5, 33],
        "deleted_v24_pages": [16], "moved_page": {"v24_page": 8, "after_v24_page": 23, "v25_page": 23},
        "v24_page_order": ORDER, "slides": metas, "evidence": V.EVIDENCE,
        "retained_body_count": 38, "internal_only_pages": [41],
        "original_architecture": {
            "source": ARCHITECTURE, "sha256": V.digest(ROOT / ARCHITECTURE),
            "slide": 33, "crop": None, "redrawn": False,
            "embedded_png_sha256": V.digest(png),
        },
        "new_model_calls": 0, "new_training_jobs": 0, "new_deployments": 0, "github_push": False,
    })
    lines = ["# v25 讲稿与来源", "", "页码调整以v24为依据。原始备注已归档，v24文件保持不变。", ""]
    for meta in metas:
        lines += [f'## P{meta["page"]} · {meta["title"]}', "", meta["notes"], ""]
    (OUT / "讲稿与来源.md").write_text("\n".join(lines))
    (OUT / "README.md").write_text(
        "# v25 · 决策模型对比与业务场景演示\n\n"
        f"[PPTX](./{NAME}.pptx) · [PDF](./{NAME}.pdf) · "
        "[全篇预览](./slides-overview.png) · [修改页预览](./updated-overview.png) · "
        "[讲稿与来源](./讲稿与来源.md) · [核验记录](./render-audit.json)\n\n"
        "**基于v24，仍为41页。** 新增封面，删除原P16；其余按要求移动或局部调整。\n\n"
        "| 修改 | v25位置 |\n| --- | --- |\n"
        f"| 新增封面：{TITLE} | P1 |\n"
        "| 原首页的Jev速度与互补定位 | P2，内容保留 |\n"
        "| 原P4删除指定的approved解释及底色条 | P5 |\n"
        "| 原P16退款案例页 | 删除 |\n"
        "| 原P8适用场景移到原P23之后 | P23，紧接P22其他场景验证 |\n"
        "| 原P33改为原完整架构图 | P33，原PNG直接嵌入，无裁剪、无重绘 |\n\n"
        "P24起的微调、vLLM、AgentCore、成本与总结保持原页码。"
        "其余38页正文和图片保留；页码、章节及当前版本的备注交叉引用同步更新。"
        "P41继续标注internal only。\n\n"
        "[原完整架构图SVG](./assets/agentcore-gpu-architecture.svg) · "
        "[原PNG](./assets/agentcore-gpu-architecture.png) · [构建清单](./build-manifest.json)\n\n"
        "本次只更新演示文件，没有新增模型测试、部署或GitHub推送。\n"
    )
    (OUT / "调整说明.md").write_text(
        "# v25 调整说明\n\n"
        "用户指定页码均指v24。新封面为P1；原P1–7顺延为P2–8。"
        "原P8移到原P23之后，成为新版P23。原P16删除，原P17–23前移为P16–22。"
        "原P24–41页码保持不变。\n\n"
        "原P4仅删除指定文字及其底色条，保留BERT、Decider/Jev、GPT对比表。"
        "P33清除简化重绘内容，直接插入既有完整架构PNG，保持字节及画面完整；"
        "PPT中的图片链接指向公开仓库的SVG。\n\n"
        "原v24保留不变，讲稿、来源与当前版本页码同步更新。\n"
    )
    print(json.dumps({"pptx": str(target), "pages": PAGES, "moved_case_page": 23}, ensure_ascii=False))


if __name__ == "__main__":
    main()
