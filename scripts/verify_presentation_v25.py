"""Verify v25 edits, original diagram bytes, retained slides and PDF rendering."""

import json
import math
import posixpath
from datetime import UTC, datetime
from zipfile import ZipFile

import pymupdf as fitz
from build_presentation_v25 import ARCHITECTURE, ORDER, OUT, PAGES, REMOVED_TEXT, ROOT, TITLE, V
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
    assert manifest["v24_page_order"] == ORDER
    assert ORDER[21:24] == [23, 8, 24] and 16 not in ORDER
    for item in manifest["evidence"]:
        assert V.digest(ROOT / item["path"]) == item["sha256"], item["path"]
    for index, slide in enumerate(original.slides, 1):
        assert (OUT / "sources/v24-notes" / f"page-{index:02}.txt").read_text() == (
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
        if index not in (1, 33):
            assert any(s.has_text_frame and s.text == meta["section"] for s in slide.shapes)
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text.strip():
                assert not Inches(6.5) <= shape.top < Inches(7.03), (index, shape.text)
                assert REMOVED_TEXT not in shape.text
                if normal(shape.text) not in text:
                    missing.append({"page": index, "text": shape.text})
            if min(shape.left, shape.top) < 0 or (
                shape.left + shape.width > prs.slide_width + 12
                or shape.top + shape.height > prs.slide_height + 12
            ):
                bounds.append({"page": index, "shape": shape.name})
        assert slide.notes_slide.notes_text_frame.text == meta["notes"]
        assert len(meta["notes"]) <= 2600
        if meta["source"] == "v24":
            before = original.slides[meta["v24_page"] - 1]
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
    assert len(preserved) == 38
    # The comparison table is byte-for-byte unchanged after removing its callout.
    old_bert = [
        s._element.xml for s in original.slides[3].shapes
        if Inches(.70) <= s.top < Inches(6.0)
    ]
    assert body(prs.slides[4]) == old_bert
    diagram = [s for s in prs.slides[32].shapes if s.shape_type == 13]
    assert len(diagram) == 1
    assert diagram[0].image.blob == (ROOT / ARCHITECTURE).read_bytes()
    assert all(getattr(diagram[0], f"crop_{side}") == 0 for side in ("left", "top", "right", "bottom"))
    assert abs(diagram[0].width / diagram[0].height - 2880 / 1860) < 1e-5
    texts = [normal(page.get_text()) for page in pdf]
    assert normal(TITLE) in texts[0]
    assert "70–500ms" in texts[1] and "40–200×" in texts[1]
    assert "BERT分类器" in texts[4]
    assert normal(REMOVED_TEXT) not in "".join(texts)
    assert "退款仍会选错：原始返回与执行结果必须分开看" not in "".join(texts)
    assert "扩展验证" in texts[21]
    assert "适用场景" in texts[22] and "90%" in texts[22] and "100ms" in texts[22]
    assert "A2A接入待实施" in texts[33] and "4/4Correct" in texts[34]
    internal = [i + 1 for i, text in enumerate(texts) if "internalonly" in text]
    assert internal == [41]
    for name, indexes, columns in (
        ("slides-overview", list(range(PAGES)), 4),
        ("updated-overview", [0, 1, 4, 21, 22, 32], 3),
    ):
        sheet = Image.new("RGB", (columns * 404, math.ceil(len(indexes) / columns) * 248), "#e7edf2")
        for number, index in enumerate(indexes):
            sheet.paste(tiles[index], ((number % columns) * 404, (number // columns) * 248))
        sheet.save(OUT / f"{name}.png")
    report = {
        "audited_at": datetime.now(UTC).isoformat(), "pages": PAGES,
        "cover_title_verified": True, "deleted_v24_page_16": True,
        "case_page_after_v24_page_23": True, "case_page_number": 23,
        "requested_bert_callout_removed": True, "bert_table_unchanged": True,
        "original_architecture_embedded_byte_identical": True,
        "architecture_uncropped": True, "architecture_aspect_ratio_preserved": True,
        "unmodified_v24_body_pages": preserved, "unchanged_image_count": image_count,
        "all_38_retained_bodies_unchanged": True, "all_original_notes_archived": True,
        "base_file_unchanged": True, "internal_only_pages": internal,
        "package_relationships_valid": True, "source_checksums_valid": True,
        "rendered_text_missing": missing, "shape_bounds_issues": bounds,
        "pptx_sha256": V.digest(target), "pdf_sha256": V.digest(target.with_suffix(".pdf")),
    }
    (OUT / "render-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    assert not missing and not bounds


if __name__ == "__main__":
    main()
