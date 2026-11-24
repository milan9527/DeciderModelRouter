"""Verify the successful image case and that all other v25 slides are preserved."""

import json
import math
import posixpath
from datetime import UTC, datetime
from zipfile import ZipFile

import pymupdf as fitz
from build_presentation_v26 import DATA, INPUT_IMAGE, OUT, PAGES, PROVIDERS, ROOT, V, actual_value
from lxml import etree
from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.util import Inches


def normal(text):
    return "".join(text.split()).replace("₀", "0")


def main():
    manifest = V.load(OUT / "build-manifest.json")
    target, base = ROOT / manifest["pptx_file"], ROOT / manifest["base_file"]
    prs, original = Presentation(target), Presentation(base)
    pdf = fitz.open(target.with_suffix(".pdf"))
    assert len(prs.slides) == len(pdf) == PAGES
    assert V.digest(base) == manifest["base_sha256"]
    assert V.digest(target) == manifest["pptx_sha256"]
    assert manifest["modified_pages"] == [20]
    for item in manifest["evidence"]:
        assert V.digest(ROOT / item["path"]) == item["sha256"], item["path"]
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
        assert (OUT / "sources/v25-notes" / f"page-{index:02}.txt").read_text() == (
            original.slides[index - 1].notes_slide.notes_text_frame.text
        )
        if index != 20:
            before = original.slides[index - 1]
            assert [s._element.xml for s in slide.shapes] == [s._element.xml for s in before.shapes], index
            assert slide.notes_slide.notes_text_frame.text == before.notes_slide.notes_text_frame.text
            images = [s.image.blob for s in slide.shapes if s.shape_type == 13]
            assert images == [s.image.blob for s in before.shapes if s.shape_type == 13]
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
    assert len(preserved) == 40
    data = V.load(ROOT / DATA)
    recorded_rows = [r for r in data["results"] if r["case_id"] == "case-01"]
    saved = V.load(OUT / "sources/image-success-case-01.json")
    assert saved["rows"] == recorded_rows
    assert len(recorded_rows) == 3 and {r["provider"] for r in recorded_rows} == set(PROVIDERS)
    texts = [normal(page.get_text()) for page in pdf]
    for row in recorded_rows:
        response = row["response"]
        assert row["status"] == 200 and response["source"] == "live"
        assert all(actual_value(response["answers"][q]) == value for q, value in response["expected"].items())
        assert response["request"]["image"]["sha256"] == V.digest(ROOT / INPUT_IMAGE)
        assert f'{response["backend_total_ms"]:,.1f}ms' in texts[19]
    assert len({r["response"]["input_fingerprint"] for r in recorded_rows}) == 1
    assert all(p in texts[19] for p in ("3/3", "completed", "confirm", "true", "三个模型均选对"))
    images = [s for s in prs.slides[19].shapes if s.shape_type == 13]
    assert len(images) == 1 and images[0].image.blob == (ROOT / INPUT_IMAGE).read_bytes()
    assert all(getattr(images[0], f"crop_{side}") == 0 for side in ("left", "top", "right", "bottom"))
    assert "27/28" in texts[20]  # Keep the complete batch's original result.
    internal = [i + 1 for i, text in enumerate(texts) if "internalonly" in text]
    assert internal == [41]
    sheet = Image.new("RGB", (4 * 404, math.ceil(PAGES / 4) * 248), "#e7edf2")
    for number, tile in enumerate(tiles):
        sheet.paste(tile, ((number % 4) * 404, (number // 4) * 248))
    sheet.save(OUT / "slides-overview.png")
    tiles[19].save(OUT / "updated-overview.png")
    report = {
        "audited_at": datetime.now(UTC).isoformat(), "pages": PAGES, "modified_pages": [20],
        "unmodified_v25_pages": preserved, "unchanged_image_count": image_count,
        "all_40_retained_pages_and_notes_unchanged": True,
        "case_01_three_models_three_correct_answers_each": True,
        "source_requests_and_responses_unmodified": True,
        "input_image_embedded_byte_identical_uncropped": True,
        "timings_checked_against_live_records": True,
        "p21_complete_batch_results_unchanged": True,
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
