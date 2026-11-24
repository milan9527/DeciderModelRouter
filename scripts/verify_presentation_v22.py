"""Check v22 source preservation, revised metrics, package structure and rendering."""

import json
import math
import posixpath
from datetime import UTC, datetime
from zipfile import ZipFile

import pymupdf as fitz
from build_presentation_v22 import ORDER, OUT, PAGES, REMOVED, ROOT, V
from lxml import etree
from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.util import Inches


def normal(text):
    return "".join(text.split()).replace("₀", "0")


def body(slide):
    return [
        shape._element.xml for shape in slide.shapes
        if Inches(.70) <= shape.top < Inches(7.03)
    ]


def main():
    manifest = json.loads((OUT / "build-manifest.json").read_text())
    target = ROOT / manifest["pptx_file"]
    base = ROOT / manifest["base_file"]
    prs, original = Presentation(target), Presentation(base)
    pdf = fitz.open(target.with_suffix(".pdf"))
    assert len(prs.slides) == len(pdf) == PAGES
    assert manifest["v21_order"] == ORDER
    assert set(range(1, 43)) - set(ORDER) == set(REMOVED)
    assert V.digest(base) == manifest["base_sha256"]
    assert V.digest(target) == manifest["pptx_sha256"]
    for item in manifest["evidence"]:
        assert V.digest(ROOT / item["path"]) == item["sha256"]
    for index, slide in enumerate(original.slides, 1):
        assert (OUT / "sources/v21-notes" / f"page-{index:02}.txt").read_text() == (
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
    previews = OUT / "previews"
    previews.mkdir(exist_ok=True)
    missing, bounds, preserved, tiles = [], [], [], []
    image_count = 0
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
        before = original.slides[meta["v21_page"] - 1]
        if not meta["content_modified"]:
            assert body(slide) == body(before), index
            preserved.append(index)
        images = [s.image.blob for s in slide.shapes if s.shape_type == 13]
        assert images == [s.image.blob for s in before.shapes if s.shape_type == 13], index
        image_count += len(images)
        preview = previews / f"page-{index:02}.png"
        page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(preview)
        with Image.open(preview) as image:
            image.thumbnail((384, 216))
            tile = Image.new("RGB", (404, 248), "#e7edf2")
            tile.paste(image, ((404 - image.width) // 2, 6))
            ImageDraw.Draw(tile).text((12, 226), f"{index:02}", fill="#203448")
            tiles.append(tile)
    texts = [normal(page.get_text()) for page in pdf]
    for value in ("14/20", "18/20"):
        assert value in texts[13], value
    for value in ("91.5", "388.5", "1271.9"):
        assert value in texts[31], value
    for value in ("60.35%", "99.95%", "35.44%", "99.85%"):
        assert value in texts[23], value
    assert "10/10" in texts[24]
    assert "vLLM" in texts[7] and "共享GPUVPC" in texts[7]
    assert not any("闭环由新事实推动" in text for text in texts)
    route = json.loads((OUT / "sources/routing-summary.json").read_text())
    for case in ("support", "code", "analysis", "retrieval"):
        for provider in ("decider", "jev", "bedrock"):
            value = route["cases"][case][provider]["original_route"]["mean_ms"]
            assert f"{value:.1f}ms" in texts[17], (case, provider, value)
    rows = json.loads((OUT / "sources/jevbench-rows.json").read_text())
    jev = next(row for row in rows if row["key"] == "jev-1.13.0")
    assert sum(row["ranked"] is True for row in rows) == 39
    assert "39个上榜系统" in texts[1]
    for value in (
        f'{jev["jevbench_score"]:.1f}',
        f'{(jev["axes"]["intelligence"] + jev["axes"]["calibration"]) / 2:.1f}',
    ):
        assert value in texts[5]
    assert any(link.get("uri") == "https://benchmarkheaven.com/jev-models/api"
               for link in pdf[5].get_links())
    internal = [i + 1 for i, text in enumerate(texts) if "internalonly" in text]
    assert internal == [34]
    for name, indexes, columns in (
        ("slides-overview", list(range(PAGES)), 4),
        ("updated-overview", [0, 2, 3, 7, 9, 10, 11, 12, 13, 15, 17, 19, 20, 25, 27, 31], 4),
    ):
        sheet = Image.new("RGB", (columns * 404, math.ceil(len(indexes) / columns) * 248), "#e7edf2")
        for number, index in enumerate(indexes):
            sheet.paste(tiles[index], ((number % columns) * 404, (number // columns) * 248))
        sheet.save(OUT / f"{name}.png")
    report = {
        "audited_at": datetime.now(UTC).isoformat(), "pages": PAGES,
        "removed_v21_pages": list(REMOVED), "v21_order": ORDER,
        "unmodified_body_pages": preserved, "unchanged_image_count": image_count,
        "all_retained_images_unchanged": True, "all_42_original_notes_archived": True,
        "base_file_unchanged": True, "notes_max_characters": max(len(s["notes"]) for s in manifest["slides"]),
        "internal_only_pages": internal, "package_relationships_valid": True,
        "source_checksums_valid": True, "routing_metrics_match_saved_three_round_source": True,
        "rendered_text_missing": missing, "shape_bounds_issues": bounds,
        "pptx_sha256": V.digest(target), "pdf_sha256": V.digest(target.with_suffix(".pdf")),
    }
    (OUT / "render-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    assert not missing and not bounds


if __name__ == "__main__":
    main()
