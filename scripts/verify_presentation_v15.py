"""Check rendered additions and preservation of all thirty existing slides."""

import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.util import Inches

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/presentations/v15"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normal(text):
    return "".join(text.split())


def body_xml(slide):
    return [shape._element.xml for shape in slide.shapes if shape.top < Inches(7.03)]


def images(slide):
    return [hashlib.sha256(shape.image.blob).hexdigest()
            for shape in slide.shapes if shape.shape_type == 13]


def main():
    manifest = json.loads((OUT / "build-manifest.json").read_text())
    path = ROOT / manifest["pptx_file"]
    original = Presentation(ROOT / manifest["base_file"])
    prs = Presentation(path)
    pdf = fitz.open(path.with_suffix(".pdf"))
    assert len(prs.slides) == len(pdf) == manifest["pages"] == 32
    assert digest(path) == manifest["pptx_sha256"]
    assert digest(ROOT / manifest["base_file"]) == manifest["base_sha256"]
    for evidence in manifest["evidence"]:
        assert digest(ROOT / evidence["path"]) == evidence["sha256"]
    previews = OUT / "previews"
    previews.mkdir(exist_ok=True)
    missing, bounds, preserved, tiles = [], [], [], []
    for index, (slide, page, meta) in enumerate(zip(prs.slides, pdf, manifest["slides"], strict=True), 1):
        text = normal(page.get_text())
        assert any(s.has_text_frame and s.text == f"{index:02} / 32" for s in slide.shapes)
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text.strip():
                assert not Inches(6.5) <= shape.top < Inches(7.03)
                if normal(shape.text) not in text:
                    missing.append({"page": index, "text": shape.text})
            if min(shape.left, shape.top) < 0 or (
                shape.left + shape.width > prs.slide_width + 12
                or shape.top + shape.height > prs.slide_height + 12
            ):
                bounds.append({"page": index, "shape": shape.name})
        assert meta["notes"] == slide.notes_slide.notes_text_frame.text
        if meta["source"] == "v14":
            before = original.slides[meta["v14_page"] - 1]
            assert body_xml(slide) == body_xml(before), index
            assert images(slide) == images(before), index
            assert slide.notes_slide.notes_text_frame.text == before.notes_slide.notes_text_frame.text
            preserved.append(index)
        preview = previews / f"page-{index:02}.png"
        page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(preview)
        with Image.open(preview) as im:
            im.thumbnail((384, 216))
            tile = Image.new("RGB", (404, 248), "#e7edf2")
            tile.paste(im, ((404 - im.width) // 2, 6))
            ImageDraw.Draw(tile).text((12, 226), f"{index:02}", fill="#203448")
            tiles.append(tile)
    assert len(preserved) == 30
    support = json.loads((OUT / "sources/support-original-comparison.json").read_text())
    rendered = normal(pdf[18].get_text())
    assert normal(support["task"]) in rendered
    for row in support["routes"].values():
        assert f'{row["route_mean_ms"]:.1f}ms' in rendered
    for expected in ("34.08", "3.50", "504", "40秒", "30分钟", "原始流程"):
        assert expected in rendered
    internal_pages = [i + 1 for i, page in enumerate(pdf) if "internalonly" in normal(page.get_text())]
    assert internal_pages == [32]
    assert "原始输入" in pdf[31].get_text()
    assert all(label in pdf[31].get_text() for label in ("m00", "m01", "m02", "3 次"))
    for name, indexes in (("slides-overview", list(range(32))), ("updated-overview", [18, 31])):
        columns = min(4, len(indexes))
        sheet = Image.new("RGB", (columns * 404, math.ceil(len(indexes) / columns) * 248), "#e7edf2")
        for n, index in enumerate(indexes):
            sheet.paste(tiles[index], ((n % columns) * 404, (n // columns) * 248))
        sheet.save(OUT / f"{name}.png")
    report = {
        "audited_at": datetime.now(UTC).isoformat(), "pages": 32,
        "new_pages": [19, 32], "internal_only_pages": internal_pages,
        "preserved_v14_body_xml_images_and_notes": preserved,
        "optimization_pages_unchanged": True, "base_file_unchanged": True,
        "rendered_text_missing": missing, "shape_bounds_issues": bounds,
        "lower_explanatory_rows": 0,
        "pptx_sha256": digest(path), "pdf_sha256": digest(path.with_suffix(".pdf")),
    }
    (OUT / "render-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    assert not missing and not bounds


if __name__ == "__main__":
    main()
