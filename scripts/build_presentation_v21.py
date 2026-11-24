"""Add three introduction slides to v19; preserve all 39 original slides."""

import copy
import io
import json
import re
import shutil
from datetime import UTC, datetime

import build_presentation_v10 as V
import presentation_layout as L
from build_presentation_v5 import replace_text
from pptx import Presentation
from pptx.oxml.ns import qn
from pptx.util import Inches

ROOT = V.ROOT
OUT = ROOT / "docs/presentations/v21"
BASE = next((ROOT / "docs/presentations/v19").glob("*.pptx"))
INTRO = next((ROOT / "docs/presentations/v20").glob("*.pptx"))
NAME = "Strands-Decider-v21-v19基础新增市场趋势与原理-42页-20261021"
PAGES = 42


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def copy_intro(prs, source):
    """Copy editable shapes and their image/link relationships, without merging decks."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = L.rgb(L.BG)
    mapped = {}
    for shape in source.shapes:
        node = copy.deepcopy(shape._element)
        for element in node.iter():
            for key in ("r:embed", "r:link", "r:id"):
                attribute = qn(key)
                old = element.get(attribute)
                if old is None:
                    continue
                if old not in mapped:
                    rel = source.part.rels[old]
                    if rel.is_external:
                        mapped[old] = slide.part.relate_to(rel.target_ref, rel.reltype, is_external=True)
                    elif rel.reltype.endswith("/image"):
                        _, mapped[old] = slide.part.get_or_add_image_part(io.BytesIO(rel.target_part.blob))
                    else:
                        raise ValueError(f"Unexpected introduction relationship: {rel.reltype}")
                element.set(attribute, mapped[old])
        slide.shapes._spTree.insert_element_before(node, "p:extLst")
    slide.notes_slide.notes_text_frame.text = source.notes_slide.notes_text_frame.text
    return slide


def main():
    for directory in ("assets", "sources", "screenshots"):
        (OUT / directory).mkdir(parents=True, exist_ok=True)
    V.OUT, V.ASSETS, V.SOURCES = OUT, OUT / "assets", OUT / "sources"
    V.EVIDENCE.clear()
    V.SLIDES.clear()
    base_hash = V.digest(BASE)
    intro_hash = V.digest(INTRO)
    previous = V.load(BASE.parent / "build-manifest.json")
    intro_meta = V.load(INTRO.parent / "build-manifest.json")
    for name in ("strands-introduction.txt", "jev-introduction.txt",
                 "jevbench-rows.json", "screenshot.json", "manifest.json"):
        target = OUT / "sources" / name
        shutil.copy2(INTRO.parent / "sources/web" / name, target)
        V.record(target)
    for name in ("jevbench-leaderboard.png", "jevbench-ranking-detail.png"):
        target = OUT / "screenshots" / name
        shutil.copy2(INTRO.parent / "screenshots" / name, target)
        V.record(target)
    prs, intro = Presentation(BASE), Presentation(INTRO)
    originals = list(prs.slides._sldIdLst)
    order, metas = [], []

    def keep(page):
        order.append(originals[page - 1])
        metas.append({**previous["slides"][page - 1], "source": "v19",
                      "v19_page": page, "body_modified": False, "notes_modified": False})

    def use_intro(page):
        slide = copy_intro(prs, intro.slides[page - 1])
        order.append(prs.slides._sldIdLst[-1])
        metas.append({**intro_meta["slides"][page - 1], "source": "introduction",
                      "intro_source": f"v20 P{page}", "body_modified": False})
        return slide

    keep(1)
    use_intro(2)
    slide = V.base(
        prs, "01  决策模型 · 通用原理",
        "把输出限定为可执行选择，让模型专注于判断",
        "提交业务事实、问题与候选项；输出候选 ID、陈述支持程度或评分，交给软件处理。",
        "",
        "新增概念说明页，不是新增实测。决策模型强调输入状态与类型化输出；"
        "有限输出空间减少文本生成开销，使高频判断更易接入软件。"
        "Strands Decider 使用基座、LoRA 与 pointer head；Jev 为专有 System One API，"
        "不能根据相似接口推断二者内部架构相同。Jev发布文描述并行采样；"
        "Strands公开实现用单次前向得到候选分数，其详细原理在后续保留的v19原页说明。"
        "choice是当前候选中的选择；noul表示对陈述的支持程度，不能未经校准视为真实业务发生概率；"
        "score是给定范围内的评分。输出格式受到约束不代表语义选择一定正确。"
        "一次请求可以含共享上下文的多个独立判断；依赖新执行结果的判断需要重新提交事实。"
        "\n来源：https://strandsagents.com/blog/introducing-strands-decider/\n"
        "https://typesafe.ai/blog/introducing-system-one-models-and-jev",
    )
    order.append(prs.slides._sldIdLst[-1])
    metas.append({**V.SLIDES[-1], "source": "new", "body_modified": True})
    V.flow(slide, [
        ("读取业务事实", "任务状态、证据、上下文", "receipt-text"),
        ("定义判断问题", "候选集合或输出范围", "list-filter"),
        ("模型完成判断", "计算选择与支持程度", "brain-circuit"),
        ("返回类型化结果", "软件执行并验收结果", "clipboard-check"),
    ], y=2.20, h=1.90)
    for index, (title, body, color) in enumerate([
        ("Choice · 选择", "从当前候选中返回一个 ID\n如：等待确认 / 立即入账", L.TEAL),
        ("Noul · 陈述判断", "判断事实是否支持给定陈述\n如：是否已有足够付款证据", V.PURPLE),
        ("Score · 评分", "在指定范围内给出等级或分数\n如：工单紧急程度", L.BLUE),
    ]):
        V.card(slide, .74 + 4.04 * index, 4.56, 3.83, 1.51, title, body, color, size=14.5)
    L.text(slide, "适合高频、有限动作的判断；格式正确仍需验证选择是否符合业务事实。",
           .80, 6.24, 11.8, .32, 14, L.TEAL, True)
    board = use_intro(6)
    board.notes_slide.notes_text_frame.text = (
        board.notes_slide.notes_text_frame.text.replace(
            "下一页 Decider 231 题是项目自测", "后续 Decider 231 题是项目自测"
        )
        + "\n新增页中的能力分按官网 Capability=(Intelligence+Calibration)/2 解释。"
        "后续 v19 原表按用户要求原样保留，其中 Jev 63.6 是 Intelligence 单轴值；"
        "不应将该单轴与本页的能力分77.1混同。"
    )
    metas[-1]["notes_modified"] = True
    for page in range(2, 40):
        keep(page)
    for node in list(prs.slides._sldIdLst):
        prs.slides._sldIdLst.remove(node)
    for node in order:
        prs.slides._sldIdLst.append(node)
    assert len(prs.slides) == PAGES
    for index, (slide, meta) in enumerate(zip(prs.slides, metas, strict=True), 1):
        for shape in list(slide.shapes):
            if shape.has_text_frame and not shape.text.strip() and Inches(6.5) <= shape.top < Inches(7.03):
                shape._element.getparent().remove(shape._element)
            elif shape.has_text_frame and shape.top > Inches(7) and re.fullmatch(r"\d+ / \d+", shape.text):
                replace_text(shape, shape.text, f"{index:02} / {PAGES}")
        meta.update(page=index, notes=slide.notes_slide.notes_text_frame.text)
    prs.core_properties.title = "Strands Decider：v19 基础新增市场趋势与原理"
    prs.core_properties.subject = "v21 / 42 pages / v19 intact with three introductory slides"
    prs.core_properties.modified = datetime.now(UTC)
    target = OUT / f"{NAME}.pptx"
    prs.save(target)
    assert V.digest(BASE) == base_hash and V.digest(INTRO) == intro_hash
    save(OUT / "build-manifest.json", {
        "created_at": datetime.now(UTC).isoformat(),
        "base_file": str(BASE.relative_to(ROOT)), "base_sha256": base_hash,
        "pptx_file": str(target.relative_to(ROOT)), "pptx_sha256": V.digest(target),
        "intro_source_file": str(INTRO.relative_to(ROOT)), "intro_source_sha256": intro_hash,
        "pages": PAGES, "new_pages": [2, 3, 4], "preserved_v19_pages": list(range(1, 40)),
        "all_v19_content_images_and_notes_preserved": True,
        "internal_only_pages": [42], "evidence": V.EVIDENCE, "slides": metas,
        "new_model_calls": 0, "new_training_jobs": 0,
    })
    lines = ["# v21 讲稿与来源", "", "以 v19 为底稿，只在封面之后增加三页。",
             "v19 全部 39 页的正文、图片、备注和相对顺序保留，仅更新页码。",
             "继承备注里的页码沿用原版本，可通过 build-manifest.json 中的 v19_page 查找。",
             "新增榜单截图采集于 2026-10-21；原图及来源保存在本目录。", ""]
    for meta in metas:
        lines += [f'## {meta["page"]:02} · {meta["title"]}', "", meta["notes"], ""]
    (OUT / "讲稿与来源.md").write_text("\n".join(lines))
    (OUT / "README.md").write_text(
        "# v21 · v19 基础新增市场趋势与原理\n\n"
        f"[PPTX](./{NAME}.pptx) · [PDF](./{NAME}.pdf) · "
        "[新增页预览](./updated-overview.png) · [讲稿与来源](./讲稿与来源.md)\n\n"
        "**42 页，以 v19 为底稿。** 原封面之后新增：\n\n"
        "- P2：决策模型市场趋势——开源权重、托管 API 与专用评测。\n"
        "- P3：通用原理——业务事实、判断问题、有限候选和类型化输出。\n"
        "- P4：JevBench 实际榜单截图与指标说明。\n\n"
        "v19 的全部 39 页按原顺序保留，正文、截图、架构、闭环、优化、微调、vLLM、成本及备注均未改动。"
        "只更新页码：原 P1 仍为 P1，原 P2–39 顺延为 P5–42。"
        "**原末页 internal only 标记保留于 P42。** v19、v20 文件不覆盖，未推送 GitHub。\n\n"
        "新增趋势页和榜单页复用已完成的介绍材料；其余内容不采用 v20 的重排或修改。"
        "JevBench 截图采集于 2026-10-21，来源为 "
        "[公开 API 榜单](https://benchmarkheaven.com/jev-models/api)。\n\n"
        "构建：`scripts/build_presentation_v21.py`；核验：`scripts/verify_presentation_v21.py`。\n\n"
        "[构建清单](./build-manifest.json) · [逐页保留与渲染核验](./render-audit.json)。\n"
    )
    print(json.dumps({"pptx": str(target), "pages": PAGES, "new_pages": [2, 3, 4]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
