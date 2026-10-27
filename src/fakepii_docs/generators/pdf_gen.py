"""PDF with a text layer, and image-only PDF (rendered text, no text layer).

Text PDF hard places: running header/footer on every page, name split across
font styles, glued tokens, a table with merged (spanning) cells, a mailto link
annotation, a sticky-note comment with an author, an AcroForm text field
value, image alt text in the structure tree, a bookmark, Info and XMP metadata.

The image PDF is the same visible layout rasterised with pdfium (via
pdfplumber) and re-embedded as one picture per page; its truth carries a
bounding box for every planted value so a scorer can compare pixels.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from fpdf import FPDF

from ..people import NearMiss, Person, Plan, split_points
from ..truth import TruthBuilder
from .common import FIXED_TIME, png_bytes

LEFT = 72.0
BODY_TOP = 84.0
LINE_STEP = 18.0
FONT_SIZE = 11
RASTER_DPI = 150


@dataclass
class _Plant:
    value: str
    type_: str | None  # None for near misses
    who: Person | NearMiss
    techniques: list[str]


@dataclass
class _Line:
    page: int
    number: int
    plants: list[_Plant] = field(default_factory=list)


class _Doc(FPDF):
    def __init__(self, header_text: str, footer_text: str):
        super().__init__(unit="pt", format="Letter")
        self.set_auto_page_break(False)
        self.c_margin = 0
        self.header_text, self.footer_text = header_text, footer_text
        self.set_creation_date(FIXED_TIME)

    def header(self) -> None:
        self.set_font("Helvetica", "", 9)
        self.set_xy(LEFT, 40)
        self.cell(text=self.header_text)

    def footer(self) -> None:
        self.set_font("Helvetica", "", 9)
        self.set_xy(LEFT, 748)
        self.cell(text=f"{self.footer_text} | page {self.page_no()}")


class _Layout:
    """Writes one visual line at a time and remembers what was planted where.

    Line numbers are what ``pdfplumber``'s ``extract_text`` yields: the
    running header is line 1, body lines follow, the footer is last.
    """

    def __init__(self, pdf: _Doc, header_plants: list[_Plant], footer_plants: list[_Plant]):
        self.pdf = pdf
        self.lines: list[_Line] = []
        self.header_plants, self.footer_plants = header_plants, footer_plants
        self.page = 0
        self.number = 0
        self.y = BODY_TOP

    def new_page(self) -> None:
        self.close_page()
        self.pdf.add_page()
        self.page += 1
        self.number = 1
        self.lines.append(_Line(self.page, 1, list(self.header_plants)))
        self.y = BODY_TOP

    def close_page(self) -> None:
        if self.page:
            self.lines.append(_Line(self.page, self.number + 1, list(self.footer_plants)))

    def line(self, chunks: list[tuple[str, str]], plants: list[_Plant] = (), *, x: float = LEFT) -> tuple[float, float]:
        """Write *chunks* (text, style) on a new line; return (x, y) of its start."""
        self.number += 1
        y = self.y
        self.pdf.set_xy(x, y)
        for text, style in chunks:
            self.pdf.set_font("Helvetica", style, FONT_SIZE)
            self.pdf.cell(w=self.pdf.get_string_width(text), h=14, text=text)
        self.lines.append(_Line(self.page, self.number, list(plants)))
        self.y += LINE_STEP
        return x, y

    def row(self, cells: list[tuple[str, float]], plants: list[_Plant]) -> None:
        """A bordered table row; each cell is (text, width). One visual line."""
        self.number += 1
        x = LEFT
        self.pdf.set_font("Helvetica", "", FONT_SIZE)
        for text, width in cells:
            self.pdf.rect(x, self.y - 2, width, LINE_STEP)
            self.pdf.set_xy(x + 4, self.y)
            self.pdf.cell(w=self.pdf.get_string_width(text), h=14, text=text)
            x += width
        self.lines.append(_Line(self.page, self.number, list(plants)))
        self.y += LINE_STEP


def _pii(value: str, type_: str, person: Person, *techniques: str) -> _Plant:
    return _Plant(value, type_, person, list(techniques))


def _near(nm: NearMiss) -> _Plant:
    return _Plant(nm.context, None, nm, ["near_miss"])


@dataclass
class _Extras:
    """Non-content PII the text PDF carries (recorded directly in the truth)."""

    form_rect: tuple[float, float, float, float] = (0, 0, 0, 0)


def _metadata(plan: Plan) -> dict[str, tuple[str, str, Person]]:
    a, b, _ = plan.people[:3]
    return {"author": (a.full, "person_name", a), "title": (b.full, "person_name", b),
            "subject": (a.username, "username", a), "keywords": (a.email, "email", a)}


def _build(plan: Plan, tb: TruthBuilder, raster: bool) -> tuple[_Doc, list[_Line], _Extras]:
    a, b, c = plan.people[:3]
    rng = plan.rng
    town, sku, order = (plan.near(k) for k in
                        ("place_named_like_surname", "product_code_phone_shape", "reference_number_ssn_shape"))
    pdf = _Doc(f"Confidential personnel summary - {a.full}", f"Questions: {b.email} | {b.phone}")
    layout = _Layout(pdf,
                     [_pii(a.full, "person_name", a, "header")],
                     [_pii(b.email, "email", b, "footer"), _pii(b.phone, "phone", b, "footer")])
    extras = _Extras()

    for key, (value, type_, person) in _metadata(plan).items():
        getattr(pdf, f"set_{key}")(value if key != "title" else f"Personnel summary: {value}")
        tb.pii(value, type_, person, "info", f"/{key.title()}", visible=False, techniques=["metadata"])
    pdf.set_creator("fakepii-docs synthetic test corpus")

    if not raster:
        pdf.set_xmp_metadata(
            '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">'
            f'<dc:creator><rdf:Seq><rdf:li>{c.full}</rdf:li></rdf:Seq></dc:creator>'
            '</rdf:Description></rdf:RDF>')
        tb.pii(c.full, "person_name", c, "xmp", "xmp", visible=False, techniques=["metadata", "xmp"])

    # ------------------------------------------------------------------ page 1
    layout.new_page()
    if not raster:
        pdf.start_section(f"Record: {a.full}")
        tb.pii(a.full, "person_name", a, "outline", "item[1]", visible=False, techniques=["bookmark"])
    layout.line([("Staff record", "B")])
    layout.line([(rng.choice(("Please review the schedule below before the Friday planning meeting.",
                              "All figures are provisional until the quarterly reconciliation closes.")), "")])
    styles = ("B", "I", "U")
    layout.line([("Employee: ", "")] + [(p, styles[i]) for i, p in enumerate(split_points(a.full, rng, 3))]
                + [(".", "")], [_pii(a.full, "person_name", a, "split_runs", "formatting")])
    layout.line([(f"Login {a.username} ({a.email}), backup line {a.phone_alt}.", "")],
                [_pii(a.username, "username", a, "glued"),
                 _pii(a.email, "email", a, "parentheses", "trailing_punctuation"),
                 _pii(a.phone_alt, "phone", a)])
    layout.line([(f"Dear {a.first}, please ask Ms. {b.last} to countersign.", "")],
                [_pii(a.first, "first_name", a), _pii(b.last, "last_name", b)])
    layout.line([("SSN on file: ", ""), (a.ssn[:5], "B"), (a.ssn[5:], ""), (f"; DOB {a.dob}.", "")],
                [_pii(a.ssn, "national_id", a, "split_runs"), _pii(a.dob, "date_of_birth", a)])

    prefix = "Write to "
    x, y = layout.line([(prefix, ""), (c.email, "U"), (" for payroll questions.", "")],
                       [_pii(c.email, "email", c, "hyperlink_text")])
    annots = 0
    if not raster:
        pdf.set_font("Helvetica", "", FONT_SIZE)
        lx = x + pdf.get_string_width(prefix)
        pdf.set_font("Helvetica", "U", FONT_SIZE)
        pdf.link(lx, y, pdf.get_string_width(c.email), 14, f"mailto:{c.email}")
        annots += 1
        tb.pii(c.email, "email", c, "page[1]", f"annot[{annots}]/URI", visible=False, techniques=["hyperlink_target"])

    x, y = layout.line([("Badge photo approved.", "")])
    if not raster:
        pdf.text_annotation(x + 140, y, f"Checked against {a.full}'s file; call {a.phone}.", title=b.full)
        annots += 1
        tb.pii(a.full, "person_name", a, "page[1]", f"annot[{annots}]/Contents", techniques=["comment"])
        tb.pii(a.phone, "phone", a, "page[1]", f"annot[{annots}]/Contents", techniques=["comment"])
        tb.pii(b.full, "person_name", b, "page[1]", f"annot[{annots}]/T", visible=False, techniques=["comment_author"])
        pdf.image(io.BytesIO(png_bytes()), x=460, y=y - 40, w=60, h=60, alt_text=f"Badge photo of {c.full}")
        tb.pii(c.full, "person_name", c, "struct", "alt[1]", visible=False, techniques=["alt_text"])
    else:
        pdf.image(io.BytesIO(png_bytes()), x=460, y=y - 40, w=60, h=60)

    x, y = layout.line([("Name on badge:", "")])
    extras.form_rect = (x + 100, 792 - (y + 14), x + 300, 792 - y)
    if not raster:
        annots += 1
        tb.pii(b.full, "person_name", b, "page[1]", f"annot[{annots}]/V", techniques=["form_field_value"])
    else:
        # In a scan the filled-in field is just ink on the page.
        pdf.set_xy(x + 100, y)
        pdf.set_font("Helvetica", "", FONT_SIZE)
        pdf.cell(w=pdf.get_string_width(b.full), h=14, text=b.full)
        layout.lines[-1].plants.append(_pii(b.full, "person_name", b, "form_field_value"))

    layout.line([("Address verified by the front office [1].", "")])
    layout.row([(f"Household record for {b.full}", 468)], [_pii(b.full, "person_name", b, "table", "merged_cell")])
    layout.row([(c.full, 140), (c.email_alt, 190), (c.phone, 138)],
               [_pii(c.full, "person_name", c, "table"), _pii(c.email_alt, "email", c, "table"),
                _pii(c.phone, "phone", c, "table")])
    layout.row([(f"Mailing address: {c.address}", 330), (c.dob, 138)],
               [_pii(c.address, "postal_address", c, "table", "merged_cell"),
                _pii(c.dob, "date_of_birth", c, "table")])
    layout.line([(f"The depot moved to {town.context} last spring.", "")], [_near(town)])
    layout.line([(f"Reorder {sku.context} and chase {order.context}.", "")], [_near(sku), _near(order)])
    layout.line([(f"[1] Home address: {a.address}. Mailing: {b.street}.", "")],
                [_pii(a.address, "postal_address", a, "footnote"), _pii(b.street, "street_address", b, "footnote")])

    # ------------------------------------------------------------------ page 2
    layout.new_page()
    layout.line([("Emergency contacts", "B")])
    layout.line([(f"Primary: {c.first} {c.last}, recorded on behalf of {b.first}.", "")],
                [_pii(c.full, "person_name", c, "nbsp"), _pii(b.first, "first_name", b)])
    layout.line([("This document is synthetic test data generated for masking evaluation.", "")])
    layout.close_page()
    return pdf, layout.lines, extras


def _add_form_field(src: bytes, rect: tuple[float, float, float, float], name: str, value: str) -> bytes:
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import (ArrayObject, BooleanObject, DictionaryObject, FloatObject, NameObject,
                               NumberObject, TextStringObject)

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(src)))
    page = writer.pages[0]
    helv = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica")}))
    widget = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Widget"),
        NameObject("/FT"): NameObject("/Tx"),
        NameObject("/T"): TextStringObject(name),
        NameObject("/V"): TextStringObject(value),
        NameObject("/DA"): TextStringObject("/Helv 10 Tf 0 g"),
        NameObject("/F"): NumberObject(4),
        NameObject("/Rect"): ArrayObject([FloatObject(v) for v in rect]),
        NameObject("/P"): page.indirect_reference,
    }))
    if "/Annots" not in page:
        page[NameObject("/Annots")] = ArrayObject()
    page["/Annots"].get_object().append(widget)
    writer._root_object[NameObject("/AcroForm")] = DictionaryObject({
        NameObject("/Fields"): ArrayObject([widget]),
        NameObject("/NeedAppearances"): BooleanObject(True),
        NameObject("/DA"): TextStringObject("/Helv 0 Tf 0 g"),
        NameObject("/DR"): DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/Helv"): helv})}),
    })
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def generate_pdf(plan: Plan, out_path: str | Path) -> TruthBuilder:
    tb = TruthBuilder("pdf", plan.seed, plan.people)
    pdf, lines, extras = _build(plan, tb, raster=False)
    data = _add_form_field(bytes(pdf.output()), extras.form_rect, "badge_name", plan.people[1].full)
    Path(out_path).write_bytes(data)
    for line in lines:
        for plant in line.plants:
            _record(tb, plant, f"page[{line.page}]", f"line[{line.number}]")
    return tb


def _record(tb: TruthBuilder, plant: _Plant, part: str, path: str, bboxes=None) -> None:
    if plant.type_ is None:
        tb.near(plant.who, part, path, techniques=plant.techniques, bboxes=bboxes)
    else:
        tb.pii(plant.value, plant.type_, plant.who, part, path, techniques=plant.techniques, bboxes=bboxes)


def _pattern(value: str) -> str:
    return r"\s+".join(re.escape(piece) for piece in value.split(" "))


def generate_pdf_image(plan: Plan, out_path: str | Path) -> TruthBuilder:
    import pdfplumber

    tb = TruthBuilder("pdf-image", plan.seed, plan.people)
    source, lines, _ = _build(plan, tb, raster=True)
    out = _Doc("", "")
    out.header = out.footer = lambda: None  # type: ignore[method-assign]
    for key, (value, type_, person) in _metadata(plan).items():
        getattr(out, f"set_{key}")(value if key != "title" else f"Personnel summary: {value}")
    out.set_creator("fakepii-docs synthetic test corpus")

    by_page: dict[int, list[_Line]] = {}
    for ln in lines:
        by_page.setdefault(ln.page, []).append(ln)

    with pdfplumber.open(io.BytesIO(bytes(source.output()))) as src:
        for n, page in enumerate(src.pages, 1):
            text_lines = page.extract_text_lines()
            for line in by_page.get(n, []):
                info = text_lines[line.number - 1]
                lo, hi = info["top"] - 2, info["bottom"] + 2
                values = {plant.value for plant in line.plants}
                found = {v: [(m["x0"], m["top"], m["x1"], m["bottom"]) for m in page.search(_pattern(v))
                             if lo <= m["top"] and m["bottom"] <= hi] for v in values}
                # A hit inside a longer planted value's hit belongs to that value.
                own = {v: [h for h in hits if not any(
                    o[0] - 0.5 <= h[0] and h[2] <= o[2] + 0.5
                    for w in values if len(w) > len(v) and v in w for o in found[w])]
                    for v, hits in found.items()}
                for plant in line.plants:
                    box = own[plant.value].pop(0)
                    _record(tb, plant, f"page[{n}]", f"raster/line[{line.number}]",
                            bboxes=[[round(v, 2) for v in box]])
            image = page.to_image(resolution=RASTER_DPI)
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            out.add_page(format=(float(page.width), float(page.height)))
            out.image(io.BytesIO(buf.getvalue()), x=0, y=0, w=float(page.width), h=float(page.height))
    Path(out_path).write_bytes(bytes(out.output()))
    return tb


def source_layout_pdf(plan: Plan) -> bytes:
    """The text PDF the image PDF was rendered from (for tests and debugging)."""
    tb = TruthBuilder("pdf", plan.seed, plan.people)
    pdf, _, _ = _build(plan, tb, raster=True)
    return bytes(pdf.output())
