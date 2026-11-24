"""Check measured JevBench values, rendering and exact preservation of v16 slides."""

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
OUT = ROOT / "docs/presentations/v17"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normal(text):
    return "".join(text.split())


def body(slide):
    return [shape._element.xml for shape in slide.shapes if shape.top < Inches(7.03)]


def main():
    manifest = json.loads((OUT / "build-manifest.json").read_text())
    path = ROOT / manifest["pptx_file"]
    prs, original = Presentation(path), Presentation(ROOT / manifest["base_file"])
    pdf = fitz.open(path.with_suffix(".pdf"))
    assert len(prs.slides) == len(pdf) == manifest["pages"] == 34
    assert digest(path) == manifest["pptx_sha256"]
    assert digest(ROOT / manifest["base_file"]) == manifest["base_sha256"]
    for item in manifest["evidence"]:
        assert digest(ROOT / item["path"]) == item["sha256"]
    previews = OUT / "previews"
    previews.mkdir(exist_ok=True)
    missing, bounds, preserved, tiles = [], [], [], []
    for index, (slide, page, meta) in enumerate(zip(prs.slides, pdf, manifest["slides"], strict=True), 1):
        text = normal(page.get_text())
        assert any(s.has_text_frame and s.text == f"{index:02} / 34" for s in slide.shapes)
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
        if meta["source"] == "v16":
            before = original.slides[meta["v16_page"] - 1]
            assert body(slide) == body(before), index
            assert [s.image.blob for s in slide.shapes if s.shape_type == 13] == [
                s.image.blob for s in before.shapes if s.shape_type == 13
            ], index
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
    summary = json.loads((OUT / "sources/summary.json").read_text())
    analysis = json.loads((OUT / "sources/analysis.json").read_text())
    score = json.loads((OUT / "sources/leaderboard-score.json").read_text())
    results_text, score_text = normal(pdf[6].get_text()), normal(pdf[7].get_text())
    expected = [
        f'{summary["accuracy"] * 100:.2f}%', "168/231", "0/231", "100%",
        f'{summary["latency"]["p50_s"] * 1000:.1f}ms',
        f'{summary["latency"]["p95_s"] * 1000:.1f}ms',
        f'{summary["brier_mean"]:.4f}', f'{summary["ece"]["ece"]:.4f}',
    ]
    for row in analysis["tiers"].values():
        expected += [f'{row["correct"]}/{row["n"]}', f'{row["accuracy"] * 100:.2f}%']
    for family in ("tool_selection", "intent", "long_policy", "temporal_numeric"):
        row = summary["per_family"][family]
        expected += [f'{row["n_correct"]}/{row["n_attempted"]}', f'{row["accuracy"] * 100:.2f}%']
    assert all(value in results_text for value in expected), expected
    assert score["official_composite"] is None and score["rank"] is None
    assert f'{score["speed_projections"]["self_hosted_rule_axis"]:.2f}' in score_text
    cost = score["cost_projections"]["cases"]["mean_call_time_allocation"][0]
    assert f'{cost["cost_axis"]:.2f}' in score_text
    assert f'${cost["usd_per_1000"]:.5f}/千次' in score_text
    assert "Decider正式总分暂无" in score_text
    assert "不能直接与Jev的榜单综合分71.49比较" in score_text
    assert "原版v19" in results_text
    assert len(preserved) == 32
    internal = [i + 1 for i, page in enumerate(pdf) if "internalonly" in normal(page.get_text())]
    assert internal == [34]
    for name, indexes in (("slides-overview", list(range(34))), ("updated-overview", [6, 7])):
        columns = min(4, len(indexes))
        sheet = Image.new("RGB", (columns * 404, math.ceil(len(indexes) / columns) * 248), "#e7edf2")
        for n, index in enumerate(indexes):
            sheet.paste(tiles[index], ((n % columns) * 404, (n // columns) * 248))
        sheet.save(OUT / f"{name}.png")
    report = {
        "audited_at": datetime.now(UTC).isoformat(), "pages": 34,
        "new_pages": [7, 8], "internal_only_pages": internal,
        "preserved_v16_body_xml_images_and_notes": preserved,
        "base_file_unchanged": True,
        "measured_public_results_match_source": True,
        "official_score_explicitly_unavailable": True,
        "rendered_text_missing": missing, "shape_bounds_issues": bounds,
        "lower_explanatory_rows": 0,
        "pptx_sha256": digest(path), "pdf_sha256": digest(path.with_suffix(".pdf")),
    }
    (OUT / "render-audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    assert not missing and not bounds


if __name__ == "__main__":
    main()
