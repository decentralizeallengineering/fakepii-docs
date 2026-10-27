"""Scan every generated document back and check the truth file is exact:
every truth location is found, with the right count, and nothing that looks
like a planted value is found anywhere the truth does not say."""

import copy

import numpy as np
import pdfplumber
import pytest

from fakepii_docs import FORMATS
from fakepii_docs.generators.pdf_gen import source_layout_pdf
from fakepii_docs.people import make_plan
from fakepii_docs.scan import verify_truth

from .conftest import SEED


@pytest.mark.parametrize("fmt", FORMATS)
def test_truth_matches_document(corpus, fmt):
    assert verify_truth(corpus[fmt]["doc"], corpus[fmt]["data"]) == []


@pytest.mark.parametrize("fmt", ["docx", "xlsx", "pptx", "pdf", "txt"])
@pytest.mark.parametrize("seed", [0, 1, 7, 2024])
def test_truth_matches_document_other_seeds(tmp_path, fmt, seed):
    from fakepii_docs import generate_document
    from fakepii_docs.truth import load_truth

    doc, truth = generate_document(fmt, tmp_path, seed)
    assert verify_truth(doc, load_truth(truth)) == []


def test_verify_catches_missing_location(corpus):
    truth = copy.deepcopy(corpus["docx"]["data"])
    entry = next(e for e in truth["pii"] if len(e["locations"]) > 1)
    dropped = entry["locations"].pop()
    problems = verify_truth(corpus["docx"]["doc"], truth)
    assert any(entry["id"] in p and dropped["path"] in p and "truth=0" in p for p in problems)


def test_verify_catches_wrong_count_and_phantom_location(corpus):
    truth = copy.deepcopy(corpus["xlsx"]["data"])
    truth["pii"][0]["locations"][0]["count"] += 1
    truth["near_misses"][0]["locations"].append(
        {"part": "xl/worksheets/sheet1.xml", "path": "sheet[1]!Z99", "count": 1, "visible": True, "techniques": []})
    problems = verify_truth(corpus["xlsx"]["doc"], truth)
    assert any(truth["pii"][0]["id"] in p for p in problems)
    assert any("Z99" in p and "found=0" in p for p in problems)


def test_verify_catches_untracked_value(corpus):
    truth = copy.deepcopy(corpus["pptx"]["data"])
    # Pretend a value the generator never planted were PII: "Hours" is a chart category.
    truth["pii"].append({"id": "pii-999", "type": "first_name", "value": "Hours", "entity": "person-1",
                         "locations": [{"part": "ppt/slides/slide3.xml", "path": "shape[1]/p[1]", "count": 1,
                                        "visible": True, "techniques": []}]})
    problems = verify_truth(corpus["pptx"]["doc"], truth)
    assert any("pii-999" in p and "chart1.xml" in p for p in problems)


def test_image_pdf_has_no_text_layer_and_boxes_have_ink(corpus):
    doc, truth = corpus["pdf-image"]["doc"], corpus["pdf-image"]["data"]
    scale = 100 / 72
    with pdfplumber.open(doc) as pdf:
        assert all(not page.chars for page in pdf.pages)
        pages = [np.asarray(p.to_image(resolution=100).original.convert("L")) for p in pdf.pages]
    boxes = 0
    for entry in truth["pii"] + truth["near_misses"]:
        for loc in entry["locations"]:
            for x0, top, x1, bottom in loc.get("bboxes", []):
                crop = pages[int(loc["part"][5:-1]) - 1][int(top * scale):int(bottom * scale) + 1,
                                                        int(x0 * scale):int(x1 * scale) + 1]
                assert crop.min() < 100, (entry["id"], loc)  # dark glyph pixels inside the box
                boxes += 1
            if "bboxes" in loc:
                assert len(loc["bboxes"]) == loc["count"]
    assert boxes >= 25


def test_image_pdf_raster_lines_match_source_layout(corpus, tmp_path):
    """The raster locations name pdfplumber lines of the text PDF the image
    was rendered from; scan that source and compare."""
    truth = copy.deepcopy(corpus["pdf-image"]["data"])
    source = tmp_path / "source.pdf"
    source.write_bytes(source_layout_pdf(make_plan(SEED, "pdf-image")))
    for entry in truth["pii"] + truth["near_misses"]:
        entry["locations"] = [dict(loc, path=loc["path"].removeprefix("raster/"))
                              for loc in entry["locations"] if "bboxes" in loc or loc["part"] == "info"]
    truth["pii"] = [e for e in truth["pii"] if e["locations"]]
    truth["document"]["format"] = "pdf"  # scan it as the text PDF it is
    assert verify_truth(source, truth) == []
