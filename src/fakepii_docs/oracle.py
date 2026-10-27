"""Oracle masker: masks a generated document *using its truth file*.

It exists to test scorers and the corpus itself, not to be a real masker:

* masking everything must make ``score`` report zero leaks and zero
  over-masking (no false positives in the scorer, and proof that every
  planted value is reachable);
* ``leave=[ids]`` keeps chosen PII values unmasked, giving hand-made leaks;
* ``mask_near_misses=True`` damages the near misses, giving over-masking.

Each masked character becomes ``X`` (whitespace is kept), so lengths and
layout stay the same. Matching tolerates markup between characters: XML tags
(split runs), zero-width characters, Markdown emphasis, the quote/comma/``&``
glue of spreadsheet string formulas and the string boundaries of PDF content
streams.
"""

from __future__ import annotations

import io
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any, Callable, Iterable

from .textmatch import CASE_INSENSITIVE_TYPES, ZERO_WIDTH
from .truth import load_truth

_ZW = "".join(sorted(ZERO_WIDTH))
_GAP_TEXT = rf'(?:<[^>]*>|[{_ZW}]|\*\*?|`|"\s*(?:,|&amp;|&)\s*")*'
_SPACE_TEXT = r"(?:\s|&nbsp;|&#160;|&#xa0;| )+"
_GAP_PDF = r"(?:\)[^()]*?\()?"
_SPACE_PDF = r"(?:\s|\xa0)+"


def _pattern(value: str, gap: str, space: str, pdf: bool, ignore_case: bool) -> re.Pattern:
    parts = []
    for ch in value:
        if ch.isspace():
            parts.append(f"({space})")
        elif pdf and ch in "()\\":
            parts.append(r"(\\?" + re.escape(ch) + ")")
        else:
            parts.append("(" + re.escape(ch) + ")")
    return re.compile(gap.join(parts), re.I if ignore_case else 0)


class _Masker:
    def __init__(self, truth: dict[str, Any], leave: Iterable[str], mask_near_misses: bool, pdf: bool = False):
        leave = set(leave)
        gap, space = (_GAP_PDF, _SPACE_PDF) if pdf else (_GAP_TEXT, _SPACE_TEXT)
        kept = [(e["value"], e["type"] in CASE_INSENSITIVE_TYPES) for e in truth["pii"] if e["id"] in leave]
        targets = {(e["value"], e["type"] in CASE_INSENSITIVE_TYPES) for e in truth["pii"] if e["id"] not in leave}
        protect = {e["context"] for e in truth["near_misses"]}
        if mask_near_misses:
            targets |= {(e["value"], False) for e in truth["near_misses"]}
            protect = set()
        # Longest first. A left-alone value is stashed once every longer target
        # has been handled, so shorter targets cannot eat into it.
        steps: list[tuple[int, bool, re.Pattern, set[int] | None]] = [
            (len(k), False, _pattern(k, gap, space, pdf, k_ic), None) for k, k_ic in kept]
        for value, ignore_case in targets:
            # Characters of a left-alone value inside a longer target stay as they are.
            keep: set[int] = set()
            for k, k_ic in kept:
                hay, needle = (value.lower(), k.lower()) if k_ic else (value, k)
                start = hay.find(needle)
                while start != -1:
                    keep.update(range(start, start + len(k)))
                    start = hay.find(needle, start + 1)
            steps.append((len(value), True, _pattern(value, gap, space, pdf, ignore_case), keep))
        steps.sort(key=lambda step: (-step[0], step[1]))
        self.steps = [(pat, keep) for _, _, pat, keep in steps]
        self.protect = [_pattern(v, gap, space, pdf, False) for v in sorted(protect, key=len, reverse=True)]

    @staticmethod
    def _x(m: re.Match, keep: set[int]) -> str:
        out, pos = [], m.start()
        for g in range(1, (m.lastindex or 0) + 1):
            s, e = m.span(g)
            out.append(m.string[pos:s])
            text = m.group(g)
            out.append(text if g - 1 in keep or text.isspace() or text.startswith("&") else "X")
            pos = e
        out.append(m.string[pos:m.end()])
        return "".join(out)

    def __call__(self, text: str) -> str:
        saved: list[str] = []

        def stash(m: re.Match) -> str:
            saved.append(m.group(0))
            return f"\ue100{len(saved) - 1}\ue101"

        for pat in self.protect:
            text = pat.sub(stash, text)
        for pat, keep in self.steps:
            text = pat.sub(stash if keep is None else (lambda m: self._x(m, keep)), text)
        return re.sub("\ue100(\\d+)\ue101", lambda m: saved[int(m.group(1))], text)


def oracle_mask(document: str | Path, truth: str | Path | dict[str, Any], out: str | Path, *,
                leave: Iterable[str] = (), mask_near_misses: bool = False) -> Path:
    truth = load_truth(truth) if not isinstance(truth, dict) else truth
    document, out = Path(document), Path(out)
    fmt = truth["document"]["format"]
    if fmt in ("docx", "xlsx", "pptx"):
        _mask_zip(document, out, _Masker(truth, leave, mask_near_misses))
    elif fmt == "pdf":
        _mask_pdf(document, out, _Masker(truth, leave, mask_near_misses, pdf=True),
                  _Masker(truth, leave, mask_near_misses))
    elif fmt == "pdf-image":
        _mask_raster(document, out, truth, set(leave), mask_near_misses, _Masker(truth, leave, mask_near_misses))
    else:
        masker = _Masker(truth, leave, mask_near_misses)
        out.write_bytes(masker(document.read_bytes().decode("utf-8")).encode("utf-8"))
    return out


def _mask_zip(src: Path, out: Path, masker: Callable[[str], str]) -> None:
    def rewrite(blob: bytes) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(blob)) as zin, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                data = zin.read(info)
                if data.startswith(b"PK\x03\x04"):
                    data = rewrite(data)
                else:
                    try:
                        data = masker(data.decode("utf-8")).encode("utf-8")
                    except UnicodeDecodeError:
                        pass
                zout.writestr(info, data)
        return buf.getvalue()

    out.write_bytes(rewrite(src.read_bytes()))


def _mask_pdf(src: Path, out: Path, stream_masker: _Masker, string_masker: _Masker) -> None:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import (ArrayObject, DictionaryObject, IndirectObject, NameObject, StreamObject,
                               TextStringObject)

    writer = PdfWriter(clone_from=PdfReader(str(src)))

    # Every object is visited once from writer._objects, so references are
    # not followed here.
    def fix(obj):
        if isinstance(obj, IndirectObject):
            return obj
        if isinstance(obj, TextStringObject):
            return TextStringObject(string_masker(str(obj)))
        if isinstance(obj, DictionaryObject):
            for k in list(obj.keys()):
                obj[NameObject(k)] = fix(obj.raw_get(k))
            if isinstance(obj, StreamObject) and obj.get("/Subtype") != "/Image":
                data = obj.get_data().decode("latin-1")
                masked = (string_masker if obj.get("/Type") == "/Metadata" else stream_masker)(data)
                if masked != data:
                    obj.set_data(masked.encode("latin-1"))
            return obj
        if isinstance(obj, ArrayObject):
            for i in range(len(obj)):
                obj[i] = fix(list.__getitem__(obj, i))
        return obj

    for i in range(len(writer._objects)):
        if writer._objects[i] is not None:
            writer._objects[i] = fix(writer._objects[i])
    with open(out, "wb") as fh:
        writer.write(fh)


def _gray_png(pixels) -> bytes:
    import struct
    import zlib

    h, w = pixels.shape
    raw = b"".join(b"\x00" + bytes(row) for row in pixels.astype("uint8"))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def _mask_raster(src: Path, out: Path, truth: dict[str, Any], leave: set[str], mask_near: bool,
                 masker: _Masker) -> None:
    import numpy as np
    import pdfplumber
    from fpdf import FPDF
    from pypdf import PdfReader

    from .generators.pdf_gen import RASTER_DPI

    scale = RASTER_DPI / 72.0
    boxes: dict[int, list[list[float]]] = {}
    for entry in truth["pii"] + (truth["near_misses"] if mask_near else []):
        if entry["id"] in leave:
            continue
        for loc in entry["locations"]:
            for bbox in loc.get("bboxes", []):
                boxes.setdefault(int(loc["part"][5:-1]), []).append(bbox)
    info = PdfReader(str(src)).metadata or {}
    pdf = FPDF(unit="pt")
    for key, setter in (("/Author", pdf.set_author), ("/Title", pdf.set_title), ("/Subject", pdf.set_subject),
                        ("/Keywords", pdf.set_keywords), ("/Creator", pdf.set_creator)):
        if key in info:
            setter(masker(str(info[key])))
    with pdfplumber.open(str(src)) as doc:
        for n, page in enumerate(doc.pages, 1):
            pixels = np.array(page.to_image(resolution=RASTER_DPI).original.convert("L"))
            for x0, top, x1, bottom in boxes.get(n, []):
                pixels[int(top * scale) - 2:int(np.ceil(bottom * scale)) + 2,
                       int(x0 * scale) - 2:int(np.ceil(x1 * scale)) + 2] = 0
            pdf.add_page(format=(float(page.width), float(page.height)))
            pdf.image(io.BytesIO(_gray_png(pixels)), x=0, y=0, w=float(page.width), h=float(page.height))
    out.write_bytes(bytes(pdf.output()))


def copy_unmasked(document: str | Path, out: str | Path) -> Path:
    """The ideal unmasker output: the original bytes."""
    shutil.copyfile(document, out)
    return Path(out)
