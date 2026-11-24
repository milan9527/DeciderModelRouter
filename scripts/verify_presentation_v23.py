"""Validate v23 evidence, unchanged v22 bodies, package links and rendered text."""

import json
import math
import posixpath
from datetime import UTC, datetime
from zipfile import ZipFile

import pymupdf as fitz
from build_presentation_v23 import OUT, PAGES, PROVIDERS, ROOT, V
from lxml import etree
from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.util import Inches


def normal(value):
    return "".join(value.split()).replace("₀", "0")


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
    assert manifest["new_pages"] == [20, 21]
    for item in manifest["evidence"]:
        assert V.digest(ROOT / item["path"]) == item["sha256"], item["path"]
    for original_page, slide in enumerate(original.slides, 1):
        assert (OUT / "sources/v22-notes" / f"page-{original_page:02}.txt").read_text() == (
            slide.notes_slide.notes_text_frame.text
        )
    for item in manifest["new_images"]:
        with Image.open(ROOT / item["source"]) as source, Image.open(ROOT / item["output"]) as cropped:
            assert source.crop(item["crop"]).tobytes() == cropped.tobytes()
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
        if meta["source"] == "v22":
            before = original.slides[meta["v22_page"] - 1]
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
    data = V.load(OUT / "sources/vision-triple-2026-10-23.json")
    texts = [normal(page.get_text()) for page in pdf]
    for provider in PROVIDERS:
        rows = [r for r in data["results"] if r["provider"] == provider]
        total = correct = 0
        for row in rows:
            response = row["response"]
            for question, expected in response["expected"].items():
                answer = response["answers"][question]
                actual = answer.get("choice") if answer["type"] == "choice" else (
                    answer["value"] if "value" in answer else answer["noul"] >= .5
                )
                correct += actual == expected
                total += 1
        assert total == 28 and correct == data["summary"][provider]["correct"]
        assert f"{correct}/28" in texts[20]
        mean = sum(r["response"]["backend_total_ms"] for r in rows) / len(rows)
        assert f"{mean:,.1f}ms" in texts[20]
        for group in ("payment", "support", "receipt"):
            values = data["summary"][provider]["groups"][group]
            assert f'{values["correct"]}/{values["questions"]}·{values["mean_backend_ms"]:,.1f}ms' in texts[20]
    assert "12张合成截图" in texts[20] and "36/36" in texts[20]
    internal = [i + 1 for i, text in enumerate(texts) if "internalonly" in text]
    assert internal == [36]
    for name, indexes, columns in (
        ("slides-overview", list(range(PAGES)), 4),
        ("updated-overview", [19, 20], 2),
    ):
        sheet = Image.new("RGB", (columns * 404, math.ceil(len(indexes) / columns) * 248), "#e7edf2")
        for number, index in enumerate(indexes):
            sheet.paste(tiles[index], ((number % columns) * 404, (number // columns) * 248))
        sheet.save(OUT / f"{name}.png")
    report = {
        "audited_at": datetime.now(UTC).isoformat(), "pages": PAGES, "new_pages": [20, 21],
        "unmodified_v22_body_pages": preserved, "unchanged_image_count": image_count,
        "all_34_retained_bodies_unchanged": True, "all_retained_images_unchanged": True,
        "all_original_notes_archived": True, "base_file_unchanged": True,
        "notes_max_characters": max(len(s["notes"]) for s in manifest["slides"]),
        "internal_only_pages": internal, "package_relationships_valid": True,
        "source_checksums_valid": True, "screenshot_pixels_unmodified_except_crop": True,
        "vision_counts_recomputed_from_raw_answers": True,
        "vision_means_recomputed_from_request_times": True,
        "rendered_text_missing": missing, "shape_bounds_issues": bounds,
        "pptx_sha256": V.digest(target), "pdf_sha256": V.digest(target.with_suffix(".pdf")),
    }
    (OUT / "render-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    assert not missing and not bounds


if __name__ == "__main__":
    main()
