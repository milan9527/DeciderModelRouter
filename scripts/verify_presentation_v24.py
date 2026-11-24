"""Check v24 source attribution, slide preservation, links and PDF rendering."""

import json
import math
import posixpath
from datetime import UTC, datetime
from zipfile import ZipFile

import pymupdf as fitz
from build_presentation_v24 import MODIFIED_PAGES, NEW_PAGES, OUT, PAGES, ROOT, V
from lxml import etree
from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.util import Inches


def normal(text):
    return "".join(text.split()).replace("₀", "0")


def body(slide):
    return [s._element.xml for s in slide.shapes if Inches(.70) <= s.top < Inches(7.03)]


def main():
    manifest = V.load(OUT / "build-manifest.json")
    target, base = ROOT / manifest["pptx_file"], ROOT / manifest["base_file"]
    prs, original = Presentation(target), Presentation(base)
    pdf = fitz.open(target.with_suffix(".pdf"))
    assert len(prs.slides) == len(pdf) == PAGES
    assert V.digest(base) == manifest["base_sha256"]
    assert V.digest(target) == manifest["pptx_sha256"]
    assert manifest["new_pages"] == NEW_PAGES
    assert manifest["modified_pages"] == MODIFIED_PAGES
    for item in manifest["evidence"]:
        assert V.digest(ROOT / item["path"]) == item["sha256"], item["path"]
    for index, slide in enumerate(original.slides, 1):
        assert (OUT / "sources/v23-notes" / f"page-{index:02}.txt").read_text() == (
            slide.notes_slide.notes_text_frame.text
        )
    with ZipFile(target) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert len(names) == len(set(names))
        for name in names:
            if not name.endswith(".rels"):
                continue
            directory = posixpath.dirname(posixpath.dirname(name))
            for rel in etree.fromstring(archive.read(name)):
                if rel.get("TargetMode") != "External":
                    resolved = posixpath.normpath(posixpath.join(directory, rel.get("Target"))).lstrip("/")
                    assert resolved in names, (name, resolved)
    missing, bounds, preserved, tiles = [], [], [], []
    image_count = 0
    previews = OUT / "previews"
    previews.mkdir(exist_ok=True)
    for index, (slide, page, meta) in enumerate(zip(prs.slides, pdf, manifest["slides"], strict=True), 1):
        text = normal(page.get_text())
        assert any(s.has_text_frame and s.text == f"{index:02} / {PAGES}" for s in slide.shapes)
        assert any(s.has_text_frame and s.text == meta["section"] for s in slide.shapes)
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text.strip():
                assert not Inches(6.5) <= shape.top < Inches(7.03), (index, shape.text)
                if normal(shape.text) not in text:
                    missing.append({"page": index, "text": shape.text})
            if min(shape.left, shape.top) < 0 or (
                shape.left + shape.width > prs.slide_width + 12
                or shape.top + shape.height > prs.slide_height + 12
            ):
                bounds.append({"page": index, "shape": shape.name})
        assert slide.notes_slide.notes_text_frame.text == meta["notes"]
        assert len(meta["notes"]) <= 2600
        if meta["source"] == "v23":
            before = original.slides[meta["v23_page"] - 1]
            assert body(slide) == body(before), index
            images = [s.image.blob for s in slide.shapes if s.shape_type == 13]
            assert images == [s.image.blob for s in before.shapes if s.shape_type == 13], index
            image_count += len(images)
            preserved.append(index)
        preview = previews / f"page-{index:02}.png"
        page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(preview)
        with Image.open(preview) as image:
            image.thumbnail((384, 216))
            tile = Image.new("RGB", (404, 248), "#e7edf2")
            tile.paste(image, ((404 - image.width) // 2, 6))
            ImageDraw.Draw(tile).text((12, 226), f"{index:02}", fill="#203448")
            tiles.append(tile)
    assert len(preserved) == 34
    texts = [normal(page.get_text()) for page in pdf]
    for phrase in ("Jev官方发布数据", "70–500ms", "40–200×", "SystemOne", "大模型的补充"):
        assert phrase in texts[0], phrase
    for phrase in ("已实现", "客户项目", "潜在场景", "DPO+LoRA", "90%", "100ms", "客户提供"):
        assert phrase in texts[7], phrase
    for phrase in ("1GPU/8会话", "80ms", "209ms", "477ms", "原生CUDA", "无入站规则"):
        assert phrase in texts[32], phrase
    assert "A2A接入待实施" in texts[33]
    assert all(p in texts[33] for p in ("Runtime", "Identity", "Gateway", "Memory", "Observability", "Evaluations"))
    evaluations = V.load(OUT / "sources/agentcore-evaluations.json")
    for row in evaluations:
        assert row["actual_choice"] == row["expected_choice"]
        assert row["actual_choice"] in texts[34]
        assert all(r["value"] == 1.0 and r["label"] == "Correct" for r in row["evaluation_results"])
    assert "48spans" in texts[34] and "4/4Correct" in texts[34]
    assert "AgentCoreGPU方案" in texts[39]
    claims = V.load(OUT / "sources/new-claims.json")
    assert claims["customer_case"]["choice_accuracy"] == .90
    assert claims["customer_case"]["independently_retested_in_this_project"] is False
    assert claims["potential_case"]["measured_results"] is None
    assert manifest["new_model_calls"] == manifest["new_training_jobs"] == manifest["new_deployments"] == 0
    assert manifest["a2a_deployed"] is False
    internal = [i + 1 for i, text in enumerate(texts) if "internalonly" in text]
    assert internal == [41]
    for name, indexes, columns in (
        ("slides-overview", list(range(PAGES)), 4),
        ("updated-overview", [p - 1 for p in sorted(NEW_PAGES + MODIFIED_PAGES)], 3),
    ):
        sheet = Image.new("RGB", (columns * 404, math.ceil(len(indexes) / columns) * 248), "#e7edf2")
        for number, index in enumerate(indexes):
            sheet.paste(tiles[index], ((number % columns) * 404, (number // columns) * 248))
        sheet.save(OUT / f"{name}.png")
    report = {
        "audited_at": datetime.now(UTC).isoformat(), "pages": PAGES,
        "new_pages": NEW_PAGES, "modified_pages": MODIFIED_PAGES,
        "unmodified_v23_body_pages": preserved, "unchanged_image_count": image_count,
        "all_34_retained_bodies_unchanged": True, "all_retained_images_unchanged": True,
        "all_original_notes_archived": True, "base_file_unchanged": True,
        "notes_max_characters": max(len(s["notes"]) for s in manifest["slides"]),
        "internal_only_pages": internal, "package_relationships_valid": True,
        "source_checksums_valid": True, "official_speed_claim_attributed": True,
        "customer_metrics_attributed_to_user": True, "a2a_marked_as_planned": True,
        "agentcore_results_checked_against_recorded_json": True,
        "rendered_text_missing": missing, "shape_bounds_issues": bounds,
        "pptx_sha256": V.digest(target), "pdf_sha256": V.digest(target.with_suffix(".pdf")),
    }
    (OUT / "render-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    assert not missing and not bounds


if __name__ == "__main__":
    main()
