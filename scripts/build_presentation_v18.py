"""Repair oversized P20/P21 notes while preserving every slide/media part byte for byte."""

import copy
import hashlib
import json
import posixpath
from datetime import UTC, datetime
from pathlib import Path
from zipfile import ZipFile

from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/presentations/v18"
BASE = next((ROOT / "docs/presentations/v17").glob("*.pptx"))
NAME = "Strands-Decider-v18-P20-P21兼容修复版-34页-20261016"
NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}
NOTES = {
    20: [
        "中英文工单索引：同一任务，三个决策模型选择了不同的向量模型。",
        "实际请求：将中英文支付工单转换为知识库检索索引所需的向量，工单内容为："
        "USDC 已转账但商户订单仍显示待支付。",
        "先选择处理流程，再根据区域可用候选和第三方检索证据选择具体型号。"
        "用户没有指定优先级或目标模型，同阶段候选与输入一致。",
        "本批 Decider 选择 Embed English，Jev 和 GPT 选择 Embed Multilingual。"
        "输入中的检索均分为 English 30.65、Multilingual 59.23。"
        "Jev / GPT 的选择更符合中英文任务与现有证据；本批未执行下游检索效果验证。",
        "数据来自 2026-10-13 原始路由测试，每任务每模型一次，不代表通用准确率。"
        "GPT 路由器为 GPT-6 Sol low；Decider 为原版 v19 / g6.xlarge。",
        "详细报告：docs/MODEL_ROUTING_COMPARISON.md。"
        "完整原始备注另存 sources/original-notes-p20.txt；页面内容与截图未修改。",
    ],
    21: [
        "支付客服原始路由：流程选择一致，具体型号选择不同。",
        "实际请求：回复客户的问题：USDC 已转出，为什么订单仍显示待支付？"
        "当前链上确认数为 3/12。",
        "2026-10-16 原始流程三轮；每阶段后端耗时取均值，预热排除。"
        "三路都选择 customer_support；每侧两次判断均由对应模型完成，"
        "同阶段输入、候选 ID 与顺序相同，没有增强输入或程序代选。",
        "Decider：流程选择 96.0 ms，型号选择 353.2 ms，"
        "合计 449.2 ms，m04 → Claude Opus 5。",
        "Jev：流程选择 492.1 ms，型号选择 425.7 ms，"
        "合计 917.8 ms，m05 → Claude Opus 5.5。",
        "GPT-6 Sol low：流程选择 1297.8 ms，型号选择 1176.3 ms，"
        "合计 2474.1 ms，m16 → GPT-6 Luna。",
        "只比较路由判断，不代表目标模型已生成答复或业务已完成。"
        "三轮重复不构成稳定延迟、吞吐或通用选型准确率结论。",
        "完整请求、响应与三轮耗时保留于"
        " docs/presentations/v16/sources/support-routing-original.json。"
        "原始长备注另存 sources/original-notes-p21.txt；页面内容未修改。",
    ],
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    (OUT / "sources").mkdir(parents=True, exist_ok=True)
    base_sha = sha(BASE.read_bytes())
    previous = json.loads((BASE.parent / "build-manifest.json").read_text())
    changes, updates = [], {}
    with ZipFile(BASE) as src:
        presentation = etree.fromstring(src.read("ppt/presentation.xml"))
        ids = presentation.find("p:sldIdLst", NS)
        assert len(ids) == 34
        rels = {
            r.get("Id"): r.get("Target")
            for r in etree.fromstring(src.read("ppt/_rels/presentation.xml.rels"))
        }
        for page, paragraphs in NOTES.items():
            slide_part = posixpath.normpath(posixpath.join(
                "ppt", rels[ids[page - 1].get(f'{{{NS["r"]}}}id')]
            ))
            relpath = posixpath.join(
                posixpath.dirname(slide_part), "_rels", posixpath.basename(slide_part) + ".rels"
            )
            notes_rel = next(
                r for r in etree.fromstring(src.read(relpath))
                if r.get("Type").endswith("/notesSlide")
            )
            notes_part = posixpath.normpath(posixpath.join(
                posixpath.dirname(slide_part), notes_rel.get("Target")
            ))
            old_bytes = src.read(notes_part)
            root = etree.fromstring(old_bytes)
            bodies = root.xpath(
                ".//p:sp[p:nvSpPr/p:nvPr/p:ph[@type='body']]/p:txBody", namespaces=NS
            )
            assert len(bodies) == 1
            body = bodies[0]
            old_paragraphs = body.findall("a:p", NS)
            old_text = "\n".join(
                "".join(p.xpath(".//a:t/text()", namespaces=NS)) for p in old_paragraphs
            )
            archive = OUT / "sources" / f"original-notes-p{page}.txt"
            archive.write_text(old_text)
            for paragraph in old_paragraphs:
                body.remove(paragraph)
            for text in paragraphs:
                paragraph = etree.SubElement(body, f'{{{NS["a"]}}}p')
                run = etree.SubElement(paragraph, f'{{{NS["a"]}}}r')
                etree.SubElement(run, f'{{{NS["a"]}}}t').text = text
            new_bytes = etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)
            updates[notes_part] = new_bytes
            changes.append({
                "page": page, "slide_part": slide_part, "notes_part": notes_part,
                "before_characters": len(old_text), "after_characters": len("\n".join(paragraphs)),
                "before_paragraphs": len(old_paragraphs), "after_paragraphs": len(paragraphs),
                "before_xml_bytes": len(old_bytes), "after_xml_bytes": len(new_bytes),
                "archived_notes": str(archive.relative_to(ROOT)),
                "archived_notes_sha256": sha(archive.read_bytes()),
            })
            previous["slides"][page - 1]["notes"] = "\n".join(paragraphs)
        target = OUT / f"{NAME}.pptx"
        with ZipFile(target, "w") as dest:
            for item in src.infolist():
                dest.writestr(copy.copy(item), updates.get(item.filename, src.read(item.filename)))
        with ZipFile(target) as dest:
            assert src.namelist() == dest.namelist()
            changed_parts = [
                name for name in src.namelist() if src.read(name) != dest.read(name)
            ]
            assert set(changed_parts) == set(updates)
    assert sha(BASE.read_bytes()) == base_sha
    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "base_file": str(BASE.relative_to(ROOT)), "base_sha256": base_sha,
        "pptx_file": str(target.relative_to(ROOT)), "pptx_sha256": sha(target.read_bytes()),
        "pages": 34, "notes_updated_pages": [20, 21],
        "changes": changes, "changed_package_parts": changed_parts,
        "all_slide_xml_media_and_relationships_byte_identical": True,
        "original_file_unchanged": True, "internal_only_pages": [34],
        "slides": previous["slides"],
        "limitation": (
            "Oversized notes are a plausible compatibility risk. Native Microsoft PowerPoint "
            "failure has not been reproduced in this Linux environment."
        ),
    }
    (OUT / "build-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    )
    lines = ["# v18 讲稿与来源", "", "P20、P21 备注精简；完整原始备注在 sources 中保留。", ""]
    for meta in previous["slides"]:
        lines += [f'## {meta["page"]:02} · {meta["title"]}', "", meta["notes"], ""]
    (OUT / "讲稿与来源.md").write_text("\n".join(lines))
    print(json.dumps({"pptx": str(target), "changes": changes}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
