"""Check reuse, public-source screenshots, real experiment values and rendering."""

import json
import math
import posixpath
from datetime import UTC, datetime
from zipfile import ZipFile

import pymupdf as fitz
from build_presentation_v20 import BOARD, OUT, ROOT
from build_presentation_v20 import V as V20
from lxml import etree
from PIL import Image, ImageChops, ImageDraw
from pptx import Presentation
from pptx.util import Inches


def normal(text):
    return "".join(text.split())


def content(slide):
    return [s._element.xml for s in slide.shapes if Inches(.70) <= s.top < Inches(7.03)]


def main():
    manifest = json.loads((OUT / "build-manifest.json").read_text())
    target = ROOT / manifest["pptx_file"]
    base = ROOT / manifest["base_file"]
    prs, original = Presentation(target), Presentation(base)
    pdf = fitz.open(target.with_suffix(".pdf"))
    assert len(prs.slides) == len(pdf) == manifest["pages"] == 40
    assert manifest["reused_pages"] == 31
    assert manifest["new_pages"] == [2, 4, 6, 15, 18, 23, 24, 25, 31]
    assert manifest["removed_v19_pages"] == [5, 6, 8, 17, 19, 23, 30, 32]
    assert V20.digest(target) == manifest["pptx_sha256"]
    assert V20.digest(base) == manifest["base_sha256"]
    for item in manifest["evidence"]:
        assert V20.digest(ROOT / item["path"]) == item["sha256"]
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
    previews = OUT / "previews"
    previews.mkdir(exist_ok=True)
    missing, bounds, preserved, notes_lengths, tiles = [], [], [], [], []
    for index, (slide, page, meta) in enumerate(zip(prs.slides, pdf, manifest["slides"], strict=True), 1):
        text = normal(page.get_text())
        assert any(s.has_text_frame and s.text == f"{index:02} / 40" for s in slide.shapes)
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
        notes = slide.notes_slide.notes_text_frame.text
        notes_lengths.append(len(notes))
        assert len(notes) <= 2600 and notes == meta["notes"], (index, len(notes))
        if meta["source"] == "v19":
            before = original.slides[meta["v19_page"] - 1]
            assert [s.image.blob for s in slide.shapes if s.shape_type == 13] == [
                s.image.blob for s in before.shapes if s.shape_type == 13
            ], index
            if not meta["content_modified"]:
                assert content(slide) == content(before), index
                preserved.append(index)
        preview = previews / f"page-{index:02}.png"
        page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(preview)
        with Image.open(preview) as im:
            im.thumbnail((384, 216))
            tile = Image.new("RGB", (404, 248), "#e7edf2")
            tile.paste(im, ((404 - im.width) // 2, 6))
            ImageDraw.Draw(tile).text((12, 226), f"{index:02}", fill="#203448")
            tiles.append(tile)
    assert len(preserved) == 26
    assert "闭环" not in "".join(pdf[i].get_text() for i in list(range(9, 12)) + list(range(18, 22)))
    internal = [index + 1 for index, page in enumerate(pdf) if "internalonly" in normal(page.get_text())]
    assert internal == [40]
    # Actual screenshot pixels are only cropped, not changed.
    shot = manifest["board_screenshot"]
    with Image.open(OUT / shot["original"]) as source, Image.open(OUT / shot["rendered"]) as cropped:
        assert ImageChops.difference(source.crop(shot["crop"]), cropped).getbbox() is None
    screenshot_manifest = json.loads((OUT / "sources/web/screenshot.json").read_text())
    assert V20.digest(OUT / shot["original"]) == screenshot_manifest["sha256"]
    assert any(link.get("uri") == BOARD for link in pdf[5].get_links())
    rows = json.loads((OUT / "sources/web/jevbench-rows.json").read_text())
    jev = next(r for r in rows if r["key"] == "jev-1.13.0")
    assert sum(r["ranked"] is True for r in rows) == 39
    board_text = normal(pdf[5].get_text())
    for value in [
        f'{jev["jevbench_score"]:.1f}',
        f'{(jev["axes"]["intelligence"] + jev["axes"]["calibration"]) / 2:.1f}',
        f'{jev["speed"]["p50_s_adjusted"]:.2f}s',
    ]:
        assert value in board_text
    route = json.loads((ROOT / "docs/results/model-evidence-2026-10-16/summary.json").read_text())
    route_text, improvement_text = normal(pdf[14].get_text()), normal(pdf[24].get_text())
    for providers in route["cases"].values():
        for result in providers.values():
            assert f'{result["original_route"]["mean_ms"]:.1f}ms' in route_text
    for row in route["providers"].values():
        assert f'{row["original"]["mean_ms"]:.1f}→{row["optimized"]["mean_ms"]:.1f}ms' in improvement_text
    assert "未证明路由准确率提高" in improvement_text
    assert "57.124/100;Chineseretrieval4.178/100." in normal(pdf[23].get_text())
    assert "14/20→18/20" in normal(pdf[20].get_text())
    assert "60.35%" in normal(pdf[28].get_text()) and "99.95%" in normal(pdf[28].get_text())
    assert "35.44%" in normal(pdf[28].get_text()) and "99.85%" in normal(pdf[28].get_text())
    assert "10/10" in normal(pdf[29].get_text())
    assert "下一步方案" in normal(pdf[30].get_text())
    assert "Decider/vLLM" in normal(pdf[8].get_text())
    for page in (34, 35, 36):
        assert "RI" in pdf[page].get_text()
    for name, indexes, columns in [
        ("slides-overview", list(range(40)), 4),
        ("updated-overview", [p - 1 for p in manifest["new_pages"]], 3),
        ("optimization-overview", list(range(17, 31)), 4),
    ]:
        sheet = Image.new("RGB", (columns * 404, math.ceil(len(indexes) / columns) * 248), "#e7edf2")
        for number, index in enumerate(indexes):
            sheet.paste(tiles[index], ((number % columns) * 404, (number // columns) * 248))
        sheet.save(OUT / f"{name}.png")
    audit = {
        "audited_at": datetime.now(UTC).isoformat(), "pages": 40,
        "reused_pages": 31, "unchanged_content_pages": preserved,
        "new_pages": manifest["new_pages"], "internal_only_pages": internal,
        "customer_closed_loop_removed": True, "source_checksums_valid": True,
        "all_reused_images_byte_identical": True, "unchanged_source_pptx": True,
        "board_screenshot_crop_only": True, "metrics_match_recorded_results": True,
        "notes_max_characters": max(notes_lengths), "package_relationships_valid": True,
        "rendered_text_missing": missing, "shape_bounds_issues": bounds,
        "lower_explanatory_rows": 0,
        "pptx_sha256": V20.digest(target), "pdf_sha256": V20.digest(target.with_suffix(".pdf")),
    }
    (OUT / "render-audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    assert not missing and not bounds


if __name__ == "__main__":
    main()
