"""Replace v25 P20 with a successful image decision from recorded live results."""

import json
import shutil
from datetime import UTC, datetime

import build_presentation_v10 as V
import presentation_layout as L
from PIL import Image
from pptx import Presentation
from pptx.util import Inches

ROOT = V.ROOT
OUT = ROOT / "docs/presentations/v26"
BASE = next((ROOT / "docs/presentations/v25").glob("*.pptx"))
NAME = "Strands-Decider-v26-图片决策成功案例-41页-20261026"
PAGES = 41
CASE_ID = "case-01"
DATA = "docs/results/vision-triple-2026-10-23.json"
INPUT_IMAGE = "app/vision_assets/case-01.png"
PROVIDERS = ("decider", "jev", "bedrock")
TITLE = "图片决策成功案例：支付完成，三个模型均选对"


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def actual_value(answer):
    if answer["type"] == "choice":
        return answer["choice"]
    return answer["value"] if "value" in answer else answer["noul"] >= .5


def main():
    for folder in ("assets", "sources/v25-notes"):
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
        target = OUT / "sources/v25-notes" / f'page-{meta["page"]:02}.txt'
        target.write_text(meta["notes"])
        V.record(target)

    data = V.load(ROOT / DATA)
    rows = [r for r in data["results"] if r["case_id"] == CASE_ID]
    by_provider = {row["provider"]: row for row in rows}
    assert len(rows) == 3 and set(by_provider) == set(PROVIDERS)
    for row in rows:
        assert row["status"] == 200 and row["response"]["source"] == "live"
        assert row["response"]["expected"] == {
            "status": "completed", "next_action": "confirm", "paid": True,
        }
        assert all(
            actual_value(row["response"]["answers"][question]) == expected
            for question, expected in row["response"]["expected"].items()
        )
        assert row["response"]["request"]["image"]["sha256"] == V.digest(ROOT / INPUT_IMAGE)
    assert len({r["response"]["input_fingerprint"] for r in rows}) == 1
    assert all(rows[0]["response"]["request"] == r["response"]["request"] for r in rows)
    saved_rows = OUT / "sources/image-success-case-01.json"
    save(saved_rows, {
        "source_file": DATA, "source_sha256": V.digest(ROOT / DATA),
        "started_at": data["started_at"], "finished_at": data["finished_at"],
        "case_id": CASE_ID, "rows": rows,
    })
    V.record(saved_rows)
    image = OUT / "assets/payment-success-case-01.png"
    shutil.copy2(ROOT / INPUT_IMAGE, image)
    V.record(image)
    with Image.open(image) as source:
        assert source.size == (448, 448)

    prs = Presentation(BASE)
    old_ids = list(prs.slides._sldIdLst)
    section = previous["slides"][19]["section"]
    notes = (
        "本页替换v25 P20缺少交易编号的误选案例，展示已保存的成功图片决策。"
        "使用2026-10-23 04:07 UTC起正式12图批次中的case-01，没有新增调用、截图伪造或答案修改。"
        "\n左侧为模型实际收到的448×448原图，显示Payment successful、129.00 USDC、Confirmed 12/12、Done。"
        "图片是演示用合成支付截图，不代表真实交易收款。"
        "三模型共用图片字节、上下文、三个独立问题与候选顺序，input_fingerprint相同，"
        "不预先OCR，也不把预期标签传给模型。"
        "\n实际问题：截图付款状态是什么？仅依据截图客服下一步应如何处理？"
        "截图是否明确显示付款成功完成？"
        "状态候选completed/pending/failed/unknown；动作候选confirm/wait/retry/review；"
        "第三问为noul/Boolean。每模型一次请求独立回答这三个问题，应用没有用第一问的返回改写后两问。"
        "\nDecider、Jev、GPT均返回status=completed、next_action=confirm、paid=true"
        "（Decider/Jev按noul>=0.5转为是）。每模型3/3项符合该图的预定义样例预期，总共9/9。"
        "Decider原始P(已支付)=0.9366，Jev=0.99，GPT返回Boolean true；"
        "不把概率/置信度称为正确率，也不补造GPT概率。"
        "\n三次backend_total_ms分别为169.108、565.222、1254.14，页面显示169.1、565.2、1254.1ms。"
        "这些是同图整组问题的单次业务后端耗时，不是单问题平均，也不是12图批次均值。"
        "Jev含Lambda转发。模型为v19官方视觉引擎、jev-1.14.0、GPT-6.1 Sol low。"
        "右侧为从原始JSON排版的结果卡片，不伪装成重新采集的浏览器截图。"
        "\n成功指图片分类和动作选择符合预期；没有实际执行付款或联系客户。"
        "P21完整批次的27/28、28/28、28/28及均值保持原样，"
        "本页成功样例不替代整体结果，也不证明全部图片都正确。"
        "\n原始数据：" + DATA + "；本页摘录：sources/image-success-case-01.json。"
        "\n实际图片：" + INPUT_IMAGE + "，本页副本assets/payment-success-case-01.png。"
    )
    s = V.base(
        prs, section, TITLE,
        "同一张支付截图、同一组问题与候选：付款状态、下一步动作、是否已支付。",
        "", notes,
    )
    for shape in list(s.shapes):
        if shape.has_text_frame and Inches(6.5) <= shape.top < Inches(7.03):
            s.shapes._spTree.remove(shape._element)
        elif shape.has_text_frame and shape.top > Inches(7) and " / " in shape.text:
            if shape.text.split(" / ")[0].isdigit():
                # Retain the existing text styling while setting the final page label.
                from build_presentation_v5 import replace_text
                replace_text(shape, shape.text, f"20 / {PAGES}")

    L.rect(s, .68, 2.18, 3.28, 3.82, L.WHITE, L.LINE, True)
    L.text(s, "实际输入：支付成功截图", .89, 2.36, 2.89, .35, 17, L.TEAL, True)
    L.picture(s, image, .95, 2.87, 2.74, 2.74)
    L.text(s, "129 USDC · 链上确认 12/12", .91, 5.72, 2.90, .28, 12.3, L.MUTED)

    for i, (provider, title, subtitle, color) in enumerate(zip(
        PROVIDERS,
        ("Strands Decider", "Jev", "GPT-6.1 Sol"),
        ("v19 官方视觉", "1.14.0", "low effort"),
        (L.TEAL, V.PURPLE, L.BLUE),
        strict=True,
    )):
        response = by_provider[provider]["response"]
        x = 4.15 + i * 2.87
        L.rect(s, x, 2.18, 2.73, 3.82, L.WHITE, L.LINE, True)
        L.rect(s, x, 2.18, .045, 3.82, color)
        L.text(s, title, x + .16, 2.34, 2.40, .34, 17, color, True)
        L.text(s, subtitle, x + .17, 2.76, 2.35, .25, 11.5, L.MUTED)
        L.text(s, f'{response["backend_total_ms"]:,.1f} ms',
               x + .17, 3.17, 2.35, .40, 25, color, True)
        L.text(s, "本次后端请求耗时", x + .19, 3.64, 2.33, .25, 11.5, L.MUTED)
        for y, label, value in (
            (4.04, "付款状态", "已完成 · completed"),
            (4.64, "下一步动作", "告知完成 · confirm"),
            (5.24, "是否已支付", "是 · true"),
        ):
            L.text(s, label, x + .17, y, 2.35, .22, 11.5, L.MUTED)
            L.text(s, value, x + .17, y + .25, 2.35, .29, 13.5, L.INK, True)
    V.callout(
        s, "成功结果：三个模型均为 3/3 项符合预期；Decider 本例响应最快。",
        y=6.18, size=16,
    )

    new_id = prs.slides._sldIdLst[-1]
    order = old_ids[:19] + [new_id] + old_ids[20:]
    keep = {node.rId for node in order}
    for node in list(prs.slides._sldIdLst):
        prs.slides._sldIdLst.remove(node)
        if node.rId not in keep:
            prs.part.drop_rel(node.rId)
    for node in order:
        prs.slides._sldIdLst.append(node)
    metas = []
    for page, slide in enumerate(prs.slides, 1):
        meta = {
            **previous["slides"][page - 1], "source": "v25", "v25_page": page,
            "content_modified": False,
        }
        if page == 20:
            meta.update(source="v25_reworked", title=TITLE, content_modified=True)
        meta.update(page=page, notes=slide.notes_slide.notes_text_frame.text)
        assert len(meta["notes"]) <= 2600
        metas.append(meta)
    prs.core_properties.subject = "v26 / P20 successful image-decision example"
    prs.core_properties.modified = datetime.now(UTC)
    target = OUT / f"{NAME}.pptx"
    prs.save(target)
    assert V.digest(BASE) == base_hash
    save(OUT / "build-manifest.json", {
        "created_at": datetime.now(UTC).isoformat(), "pages": PAGES,
        "base_file": str(BASE.relative_to(ROOT)), "base_sha256": base_hash,
        "pptx_file": str(target.relative_to(ROOT)), "pptx_sha256": V.digest(target),
        "modified_pages": [20], "slides": metas, "evidence": V.EVIDENCE,
        "retained_body_count": 40, "internal_only_pages": [41],
        "success_case": {
            "case_id": CASE_ID, "source_file": DATA, "source_sha256": V.digest(ROOT / DATA),
            "input_image": INPUT_IMAGE, "input_image_sha256": V.digest(ROOT / INPUT_IMAGE),
            "correct_by_provider": {p: 3 for p in PROVIDERS}, "questions_per_provider": 3,
            "backend_total_ms": {p: by_provider[p]["response"]["backend_total_ms"] for p in PROVIDERS},
        },
        "new_model_calls": 0, "new_training_jobs": 0, "new_deployments": 0, "github_push": False,
    })
    lines = ["# v26 讲稿与来源", "", "仅替换P20为成功图片案例；其余40页保留。", ""]
    for meta in metas:
        lines += [f'## P{meta["page"]} · {meta["title"]}', "", meta["notes"], ""]
    (OUT / "讲稿与来源.md").write_text("\n".join(lines))
    (OUT / "README.md").write_text(
        "# v26 · 图片决策成功案例\n\n"
        f"[PPTX](./{NAME}.pptx) · [PDF](./{NAME}.pdf) · "
        "[P20预览](./previews/page-20.png) · [全篇预览](./slides-overview.png) · "
        "[讲稿与来源](./讲稿与来源.md) · [核验记录](./render-audit.json)\n\n"
        "**基于v25，仍为41页，仅修改P20。** 使用支付成功原图case-01："
        "129 USDC、12/12确认。Decider、Jev、GPT的付款状态、下一步动作、是否已支付均符合预期，"
        "每模型3/3；单次整组问题后端耗时分别为169.1、565.2、1254.1ms。\n\n"
        "左侧为真实测试输入图；右侧为已保存的实际返回排版，未重画图片内信息或伪造网页截图。"
        "来源为2026-10-23正式12图批次，不是新增测试。P21完整批次结果保持原样。\n\n"
        "[原图](./assets/payment-success-case-01.png) · "
        "[三模型实际请求与返回](./sources/image-success-case-01.json) · [构建清单](./build-manifest.json)\n\n"
        "原封面、页面顺序和P33完整AgentCore架构图保留，P41继续标注internal only。"
        "本次没有新增模型调用、部署或GitHub推送。\n"
    )
    print(json.dumps({"pptx": str(target), "pages": PAGES, "modified_pages": [20]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
