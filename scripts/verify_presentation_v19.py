"""Verify source values, preserved slides, PPTX relationships and PDF rendering."""

import json
import math
import posixpath
from datetime import UTC, datetime
from zipfile import ZipFile

import pymupdf as fitz
from build_presentation_v19 import OUT, ROOT, WALK
from build_presentation_v19 import V as V19
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
    path = ROOT / manifest["pptx_file"]
    base = ROOT / manifest["base_file"]
    prs, original = Presentation(path), Presentation(base)
    pdf = fitz.open(path.with_suffix(".pdf"))
    assert len(prs.slides) == len(pdf) == manifest["pages"] == 39
    assert V19.digest(path) == manifest["pptx_sha256"]
    assert V19.digest(base) == manifest["base_sha256"]
    for item in manifest["source_snapshots"]:
        assert V19.digest(ROOT / item["snapshot"]) == item["sha256"]
    for item in manifest["evidence"]:
        assert V19.digest(ROOT / item["path"]) == item["sha256"]
    with ZipFile(path) as package:
        assert package.testzip() is None
        names = package.namelist()
        assert len(names) == len(set(names))
        for name in names:
            if not name.endswith(".rels"):
                continue
            folder = posixpath.dirname(posixpath.dirname(name))
            for rel in etree.fromstring(package.read(name)):
                if rel.get("TargetMode") == "External":
                    continue
                target = rel.get("Target")
                resolved = posixpath.normpath(posixpath.join(folder, target)).lstrip("/")
                assert resolved in names, (name, target, resolved)
    previews = OUT / "previews"
    previews.mkdir(exist_ok=True)
    missing, bounds, preserved, tiles, notes_lengths = [], [], [], [], []
    for index, (slide, page, meta) in enumerate(zip(prs.slides, pdf, manifest["slides"], strict=True), 1):
        pdf_text = normal(page.get_text())
        assert any(s.has_text_frame and s.text == f"{index:02} / 39" for s in slide.shapes)
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text.strip():
                assert not Inches(6.5) <= shape.top < Inches(7.03), (index, shape.text)
                if normal(shape.text) not in pdf_text:
                    missing.append({"page": index, "text": shape.text})
            if min(shape.left, shape.top) < 0 or (
                shape.left + shape.width > prs.slide_width + 12
                or shape.top + shape.height > prs.slide_height + 12
            ):
                bounds.append({"page": index, "shape": shape.name})
        notes = slide.notes_slide.notes_text_frame.text
        notes_lengths.append(len(notes))
        assert meta["notes"] == notes
        if meta["source"] == "new":
            assert len(notes) < 2000
        if meta["source"] == "v18" and not meta["body_modified"]:
            before = original.slides[meta["v18_page"] - 1]
            assert body(slide) == body(before), index
            assert [s.image.blob for s in slide.shapes if s.shape_type == 13] == [
                s.image.blob for s in before.shapes if s.shape_type == 13
            ], index
            assert notes == before.notes_slide.notes_text_frame.text
            preserved.append(index)
        preview = previews / f"page-{index:02}.png"
        page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(preview)
        with Image.open(preview) as im:
            im.thumbnail((384, 216))
            tile = Image.new("RGB", (404, 248), "#e7edf2")
            tile.paste(im, ((404 - im.width) // 2, 6))
            ImageDraw.Draw(tile).text((12, 226), f"{index:02}", fill="#203448")
            tiles.append(tile)
    source = OUT / "sources/reports/2026-10-16"
    summary = json.loads((source / "summary.json").read_text())
    training = json.loads((source / "training.json").read_text())
    replay = json.loads((source / "replay-verification.json").read_text())
    assert len(preserved) == 30
    assert notes_lengths[19] < 1000 and notes_lengths[20] < 1000
    # Assert every displayed result against its saved source report.
    test_text = normal(pdf[26].get_text())
    for model in summary["models"].values():
        choice = model["choices"]
        for number in (choice["canonical"]["accuracy"], choice["reversed_and_renamed"]["accuracy"],
                       choice["same_actual_choice_fraction"]):
            assert f"{number * 100:.2f}%" in test_text
    for row in training["history"]:
        assert f'{row["validation"]["accuracy"] * 100:.2f}%' in test_text
    assert "2,067/2,068" in test_text
    assert "2,068" in normal(pdf[26].get_text())
    games_text = normal(pdf[27].get_text())
    for policy in ("original", "model"):
        for model in ("baseline", "adapted"):
            game = summary["models"][model]["games"][policy]
            assert f'{game["mean_lines"]:.1f}' in games_text
            assert f'{game["mean_pieces"]:.1f}' in games_text
    assert "34/500" in games_text and "10/10" in games_text
    assert all(g["raw_choice_matches_applied_placement"] and g["board_replay_matches"]
               for g in replay["games"])
    assert sum(g["verified_live_moves"] for g in replay["games"]) == 895
    assert training["initial_lora_sha256"] != training["final_lora_sha256"]
    assert training["initial_head_sha256"] != training["final_head_sha256"]
    assert "完整训练约64分钟" in normal(pdf[25].get_text())
    assert "label=2" in normal(pdf[24].get_text())
    for count in ("12,590", "2,120", "2,090", "2,068"):
        assert count in normal(pdf[23].get_text())
    internal = [i + 1 for i, page in enumerate(pdf) if "internalonly" in normal(page.get_text())]
    assert internal == [39]
    assert any(link.get("uri") == WALK for link in pdf[23].get_links())
    assert any(link.get("uri") == WALK for link in pdf[37].get_links())
    for name, indexes in (
        ("slides-overview", list(range(39))),
        ("updated-overview", [23, 24, 25, 26, 27]),
    ):
        columns = min(3 if name == "updated-overview" else 4, len(indexes))
        sheet = Image.new("RGB", (columns * 404, math.ceil(len(indexes) / columns) * 248), "#e7edf2")
        for n, index in enumerate(indexes):
            sheet.paste(tiles[index], ((n % columns) * 404, (n // columns) * 248))
        sheet.save(OUT / f"{name}.png")
    report = {
        "audited_at": datetime.now(UTC).isoformat(), "pages": 39,
        "new_pages": manifest["new_pages"], "internal_only_pages": internal,
        "preserved_v18_body_xml_images_and_notes": preserved,
        "base_file_unchanged": True, "source_checksums_valid": True,
        "metrics_match_raw_reports": True, "package_relationships_valid": True,
        "new_notes_character_counts": notes_lengths[23:28],
        "p20_p21_short_notes_preserved": True,
        "rendered_text_missing": missing, "shape_bounds_issues": bounds,
        "lower_explanatory_rows": 0,
        "pptx_sha256": V19.digest(path), "pdf_sha256": V19.digest(path.with_suffix(".pdf")),
    }
    (OUT / "render-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    assert not missing and not bounds


if __name__ == "__main__":
    main()
