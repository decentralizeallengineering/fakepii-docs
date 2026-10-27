"""The scorer: no false positives on a perfect masking, exact reports on
hand-made leaks (body and metadata), over-masking and round-trip fidelity."""

import io
import json
import re
import zipfile

import pytest

from fakepii_docs import FORMATS
from fakepii_docs.cli import main
from fakepii_docs.oracle import oracle_mask
from fakepii_docs.scorer import format_report, score
from fakepii_docs.textmatch import normalize

from .conftest import copy


def _inside(entry, value):
    a, b = normalize(entry["value"]), normalize(value)
    return a.lower() in b.lower() if entry["type"] == "email" else a in b


@pytest.mark.parametrize("fmt", FORMATS)
def test_perfect_masking_passes(corpus, work, fmt):
    doc, truth = corpus[fmt]["doc"], corpus[fmt]["data"]
    masked = oracle_mask(doc, truth, work / f"masked{doc.suffix}")
    report = score(doc, masked, truth)
    assert report["leaks"] == [] and report["over_masking"] == [] and report["warnings"] == []
    assert report["passed"]


@pytest.mark.parametrize("fmt", FORMATS)
def test_unmasked_document_leaks_everything(corpus, fmt):
    doc, truth = corpus[fmt]["doc"], corpus[fmt]["data"]
    report = score(doc, doc, truth)
    assert {leak["id"] for leak in report["leaks"]} == {e["id"] for e in truth["pii"]}
    assert not report["passed"]


@pytest.mark.parametrize("fmt", FORMATS)
def test_each_left_value_is_reported_exactly(corpus, work, fmt):
    """Leave one value unmasked at a time: the report names it (and any value
    that is a substring of it), and nothing else."""
    doc, truth = corpus[fmt]["doc"], corpus[fmt]["data"]
    entries = truth["pii"] if fmt != "pdf-image" else truth["pii"][:4]
    for entry in entries:
        masked = oracle_mask(doc, truth, work / f"leak{doc.suffix}", leave=[entry["id"]])
        report = score(doc, masked, truth)
        if fmt == "pdf-image" and all("bboxes" in loc for loc in entry["locations"]):
            expected = {entry["id"]}  # pixels are compared per truth box only
        else:
            expected = {e["id"] for e in truth["pii"] if _inside(e, entry["value"])}
        assert {leak["id"] for leak in report["leaks"]} == expected, entry
        assert report["over_masking"] == []


def test_hand_made_leak_in_text(corpus, work):
    doc, truth = corpus["txt"]["doc"], corpus["txt"]["data"]
    masked = oracle_mask(doc, truth, work / "masked.txt")
    email = next(e for e in truth["pii"] if e["type"] == "email")
    lines = masked.read_text("utf-8").split("\n")
    lines[3] += f" (cc {email['value'].upper()})"  # a masker that missed an upper-cased address
    masked.write_text("\n".join(lines), "utf-8", newline="")
    report = score(doc, masked, truth)
    assert [(leak["id"], leak["locations"]) for leak in report["leaks"]] == [(email["id"], ["line[4]"])]
    assert not report["passed"]
    assert "LEAKS: 1 of" in format_report(report)


def test_hand_made_leak_in_metadata(corpus, work):
    """Body perfectly masked, but the author is put back into docProps/core.xml."""
    doc, truth = corpus["docx"]["doc"], corpus["docx"]["data"]
    masked = oracle_mask(doc, truth, work / "masked.docx")
    author = next(e for e in truth["pii"] if any(loc["path"] == "core/creator" for loc in e["locations"]))
    buf = io.BytesIO()
    with zipfile.ZipFile(masked) as zin, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info)
            if info.filename == "docProps/core.xml":
                data = re.sub(rb"<dc:creator>[^<]*</dc:creator>",
                              f"<dc:creator>{author['value']}</dc:creator>".encode(), data)
            zout.writestr(info, data)
    masked.write_bytes(buf.getvalue())
    report = score(doc, masked, truth)
    leaked = {leak["id"]: leak for leak in report["leaks"]}
    assert set(leaked) == {author["id"]} | {e["id"] for e in truth["pii"] if _inside(e, author["value"])}
    assert leaked[author["id"]]["locations"] == ["docProps/core.xml#core/creator"]
    assert "docProps/core.xml" in leaked[author["id"]]["raw"]


def test_hand_made_leak_in_split_runs_is_found(corpus, work):
    """Re-insert a name split over three differently formatted runs."""
    doc, truth = corpus["docx"]["doc"], corpus["docx"]["data"]
    masked = oracle_mask(doc, truth, work / "masked.docx")
    name = next(e for e in truth["pii"] if e["type"] == "person_name")
    first, last = name["value"].split(" ")
    runs = (f'<w:r><w:rPr><w:b/></w:rPr><w:t>{first[:2]}</w:t></w:r>'
            f'<w:r><w:rPr><w:i/></w:rPr><w:t xml:space="preserve">{first[2:]} </w:t></w:r>'
            f'<w:r><w:t>{last}</w:t></w:r>')
    buf = io.BytesIO()
    with zipfile.ZipFile(masked) as zin, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info)
            if info.filename == "word/document.xml":
                data = data.replace(b"</w:body>", f"<w:p>{runs}</w:p></w:body>".encode())
            zout.writestr(info, data)
    masked.write_bytes(buf.getvalue())
    report = score(doc, masked, truth)
    leak = next(leak for leak in report["leaks"] if leak["id"] == name["id"])
    assert len(leak["locations"]) == 1 and leak["locations"][0].startswith("word/document.xml#body/p[")


def test_over_masking_of_near_misses(corpus, work):
    for fmt in ("docx", "csv", "pdf-image"):
        doc, truth = corpus[fmt]["doc"], corpus[fmt]["data"]
        masked = oracle_mask(doc, truth, work / f"over{doc.suffix}", mask_near_misses=True)
        report = score(doc, masked, truth)
        assert report["leaks"] == []
        assert {o["id"] for o in report["over_masking"]} == {n["id"] for n in truth["near_misses"]}, fmt
        assert not report["passed"]


def test_surname_left_in_place_name_is_not_a_leak(corpus, work):
    """'<Surname> Township' survives masking: that is correct, not a leak."""
    doc, truth = corpus["md"]["doc"], corpus["md"]["data"]
    town = next(n for n in truth["near_misses"] if n["kind"] == "place_named_like_surname")
    masked = oracle_mask(doc, truth, work / "masked.md")
    assert town["context"] in masked.read_text("utf-8")
    assert score(doc, masked, truth)["passed"]


@pytest.mark.parametrize("fmt", FORMATS)
def test_round_trip_identical_passes(corpus, work, fmt):
    doc, truth = corpus[fmt]["doc"], corpus[fmt]["data"]
    masked = oracle_mask(doc, truth, work / f"masked{doc.suffix}")
    unmasked = copy(doc, work / f"unmasked{doc.suffix}")
    report = score(doc, masked, truth, unmasked)
    rt = report["round_trip"]
    assert rt["passed"] and rt["byte_identical_file"] and rt["mismatches"] == []
    assert report["passed"]


def test_round_trip_detects_one_wrong_byte(corpus, work):
    doc, truth = corpus["docx"]["doc"], corpus["docx"]["data"]
    masked = oracle_mask(doc, truth, work / "masked.docx")
    phone = next(e for e in truth["pii"] if e["type"] == "phone"
                 and any(loc["part"] == "word/document.xml" for loc in e["locations"]))
    wrong = phone["value"][:-1] + ("0" if phone["value"][-1] != "0" else "1")
    buf = io.BytesIO()
    with zipfile.ZipFile(doc) as zin, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info)
            if info.filename == "word/document.xml":
                data = data.replace(phone["value"].encode(), wrong.encode(), 1)
            zout.writestr(info, data)
    unmasked = work / "unmasked.docx"
    unmasked.write_bytes(buf.getvalue())
    rt = score(doc, masked, truth, unmasked)["round_trip"]
    assert not rt["passed"]
    assert len(rt["mismatches"]) == 1
    where = next(loc for loc in phone["locations"] if loc["part"] == "word/document.xml")
    assert rt["mismatches"][0]["where"] == f"word/document.xml#{where['path']}"
    assert rt["mismatches"][0]["got"][rt["mismatches"][0]["first_diff"]] == wrong[-1]


def test_round_trip_hidden_change_is_reported_separately(corpus, work):
    doc, truth = corpus["xlsx"]["doc"], corpus["xlsx"]["data"]
    buf = io.BytesIO()
    with zipfile.ZipFile(doc) as zin, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info)
            if info.filename == "docProps/core.xml":
                data = re.sub(rb"(<dc:creator[^>]*>)[^<]*", rb"\1someone else", data)
            zout.writestr(info, data)
    unmasked = work / "unmasked.xlsx"
    unmasked.write_bytes(buf.getvalue())
    rt = score(doc, oracle_mask(doc, truth, work / "m.xlsx"), truth, unmasked)["round_trip"]
    assert rt["passed"] and not rt["byte_identical_file"]
    assert [m["where"] for m in rt["hidden_mismatches"]] == ["docProps/core.xml#core/creator"]


def test_round_trip_image_pdf_detects_masked_page(corpus, work):
    doc, truth = corpus["pdf-image"]["doc"], corpus["pdf-image"]["data"]
    masked = oracle_mask(doc, truth, work / "masked.pdf")
    rt = score(doc, masked, truth, unmasked=masked)["round_trip"]  # "unmasking" that changed nothing
    assert not rt["passed"] and rt["raster_mismatches"]


def test_cli_generate_verify_and_score(tmp_path, capsys):
    out = tmp_path / "c"
    assert main(["generate", str(out), "--seed", "5", "--formats", "txt,docx"]) == 0
    doc, truth = out / "fakepii-5-txt.txt", out / "fakepii-5-txt.txt.truth.json"
    assert main(["verify", str(doc), str(truth)]) == 0
    masked = oracle_mask(doc, truth, tmp_path / "m.txt")
    assert main(["score", str(doc), str(masked), str(truth), "--unmasked", str(doc)]) == 0
    assert "RESULT: PASS" in capsys.readouterr().out
    assert main(["score", str(doc), str(doc), str(truth), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["leak_summary"]["values_leaked"] == report["leak_summary"]["values_total"]
    assert main(["schema"]) == 0
    assert json.loads(capsys.readouterr().out)["title"] == "fakepii-docs ground truth"


def test_cli_rejects_unknown_format(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["generate", str(tmp_path), "--formats", "rtf"])
    assert exc.value.code == 2
