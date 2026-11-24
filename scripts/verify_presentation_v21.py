"""Verify exact v19 content/notes preservation, introduction sources and rendering."""

import json
import math
import posixpath
from datetime import UTC, datetime
from zipfile import ZipFile

import pymupdf as fitz
from build_presentation_v21 import OUT, ROOT
from build_presentation_v21 import V as V21
from lxml import etree
from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.util import Inches


def normal(text):
    return "".join(text.split())


def body(slide):
    return [shape._element.xml for shape in slide.shapes if shape.top < Inches(7.03)]


def main():
    manifest = json.loads((OUT / "build-manifest.json").read_text())
    target = ROOT / manifest["pptx_file"]
    base = ROOT / manifest["base_file"]
    prs, original = Presentation(target), Presentation(base)
    pdf = fitz.open(target.with_suffix(".pdf"))
    assert len(prs.slides) == len(pdf) == 42
    assert V21.digest(base) == manifest["base_sha256"]
    assert V21.digest(target) == manifest["pptx_sha256"]
    assert V21.digest(ROOT / manifest["intro_source_file"]) == manifest["intro_source_sha256"]
    for item in manifest["evidence"]:
        assert V21.digest(ROOT / item["path"]) == item["sha256"]
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
    missing, bounds, preserved, tiles = [], [], [], []
    for index, (slide, page, meta) in enumerate(zip(prs.slides, pdf, manifest["slides"], strict=True), 1):
        text = normal(page.get_text())
        assert any(s.has_text_frame and s.text == f"{index:02} / 42" for s in slide.shapes)
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text.strip():
                assert not Inches(6.5) <= shape.top < Inches(7.03), (index, shape.text)
                # LibreOffice may extract typographic subscripts as ordinary characters.
                wanted = normal(shape.text).replace("₀", "0")
                if wanted not in text.replace("₀", "0"):
                    missing.append({"page": index, "text": shape.text})
            if min(shape.left, shape.top) < 0 or (
                shape.left + shape.width > prs.slide_width + 12
                or shape.top + shape.height > prs.slide_height + 12
            ):
                bounds.append({"page": index, "shape": shape.name})
        assert slide.notes_slide.notes_text_frame.text == meta["notes"]
        if meta["source"] == "v19":
            old_page = meta["v19_page"]
            assert index == (1 if old_page == 1 else old_page + 3)
            before = original.slides[old_page - 1]
            assert body(slide) == body(before), index
            assert [s.image.blob for s in slide.shapes if s.shape_type == 13] == [
                s.image.blob for s in before.shapes if s.shape_type == 13
            ], index
            assert slide.notes_slide.notes_text_frame.text == before.notes_slide.notes_text_frame.text
            preserved.append({"v19_page": old_page, "v21_page": index})
        preview = previews / f"page-{index:02}.png"
        page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(preview)
        with Image.open(preview) as image:
            image.thumbnail((384, 216))
            tile = Image.new("RGB", (404, 248), "#e7edf2")
            tile.paste(image, ((404 - image.width) // 2, 6))
            ImageDraw.Draw(tile).text((12, 226), f"{index:02}", fill="#203448")
            tiles.append(tile)
    assert len(preserved) == 39
    assert [x["v19_page"] for x in preserved] == list(range(1, 40))
    assert "闭环由新事实推动" in normal(pdf[19].get_text())
    assert "60.35%" in normal(pdf[29].get_text()) and "99.95%" in normal(pdf[29].get_text())
    internal = [i + 1 for i, page in enumerate(pdf) if "internalonly" in normal(page.get_text())]
    assert internal == [42]
    rows = json.loads((OUT / "sources/jevbench-rows.json").read_text())
    jev = next(row for row in rows if row["key"] == "jev-1.13.0")
    assert sum(row["ranked"] is True for row in rows) == 39
    assert "39个上榜系统" in normal(pdf[1].get_text())
    for value in (
        f'{jev["jevbench_score"]:.1f}',
        f'{(jev["axes"]["intelligence"] + jev["axes"]["calibration"]) / 2:.1f}',
    ):
        assert value in normal(pdf[3].get_text())
    assert any(link.get("uri") == "https://benchmarkheaven.com/jev-models/api"
               for link in pdf[3].get_links())
    for name, indexes, columns in (
        ("slides-overview", list(range(42)), 4), ("updated-overview", [1, 2, 3], 3),
    ):
        sheet = Image.new("RGB", (columns * 404, math.ceil(len(indexes) / columns) * 248), "#e7edf2")
        for number, index in enumerate(indexes):
            sheet.paste(tiles[index], ((number % columns) * 404, (number // columns) * 248))
        sheet.save(OUT / f"{name}.png")
    report = {
        "audited_at": datetime.now(UTC).isoformat(), "pages": 42, "new_pages": [2, 3, 4],
        "preserved_v19_content_images_and_notes": preserved,
        "all_39_original_slides_preserved_in_order": True,
        "base_and_intro_source_files_unchanged": True,
        "internal_only_pages": internal, "package_relationships_valid": True,
        "source_checksums_valid": True, "new_metrics_match_public_source": True,
        "rendered_text_missing": missing, "shape_bounds_issues": bounds,
        "pptx_sha256": V21.digest(target), "pdf_sha256": V21.digest(target.with_suffix(".pdf")),
    }
    (OUT / "render-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items()
                      if key != "preserved_v19_content_images_and_notes"}, ensure_ascii=False, indent=2))
    assert not missing and not bounds


if __name__ == "__main__":
    main()
