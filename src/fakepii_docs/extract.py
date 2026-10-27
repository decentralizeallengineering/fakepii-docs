"""Read every text-bearing place out of a document.

Two views are offered:

``extract_segments(path)``
    Structured view. Each :class:`Segment` names a ``part`` (zip member, PDF
    page, ``info`` ...) and a ``path`` inside it, using the same location
    grammar the generators write into the ground truth. This code reads the raw
    package XML with :mod:`xml.etree.ElementTree` and does not reuse the
    writer libraries, so it is an independent check on what the generators
    claim they wrote.

``raw_texts(path)``
    Catch-all view used for leak detection. Every zip member (recursing into
    embedded packages), every PDF object, string and (decompressed) stream, the
    raw bytes of text files. Markup is stripped so split runs re-join, and
    attribute values are listed separately.

Location grammar (``part`` / ``path``)
--------------------------------------
docx  ``word/document.xml`` ``body/p[3]``, ``body/tbl[1]/tr[2]/tc[1]/p[1]``,
      ``body/p[4]/hyperlink[1]@target`` (part = the ``.rels``),
      ``body/p[5]/drawing[1]@descr``, ``body/p[6]/ffdata[1]@default``,
      ``body/p[7]/sdt[1]@tag``, ``body/p[8]/deleted``, ``body/p[8]/del[1]@author``;
      ``word/header1.xml`` ``p[1]``; ``word/comments.xml`` ``comment[0]/p[1]``,
      ``comment[0]@author``; ``word/footnotes.xml`` ``footnote[1]/p[1]``;
      ``docProps/core.xml`` ``core/creator``; ``docProps/app.xml`` ``app/Company``.
xlsx  ``xl/workbook.xml`` ``sheet[2]@name``; ``xl/sharedStrings.xml`` or the sheet
      part ``sheet[1]!B4``; ``sheet[1]!C5@formula``; ``sheet[1]/oddHeader``;
      ``sheet[1]!D4@hyperlink``; comments part ``sheet[1]!D4@comment`` and
      ``@comment_author``; ``xl/charts/chart1.xml`` ``title[1]``.
pptx  ``ppt/slides/slide1.xml`` ``shape[2]/p[1]``, ``shape[3]/tbl/tr[1]/tc[1]/p[1]``,
      ``shape[4]@descr``, ``shape[2]/p[1]/hlink[1]@target`` (part = ``.rels``);
      ``ppt/notesSlides/notesSlide1.xml`` ``shape[2]/p[1]``; charts ``title[1]``.
pdf   ``info`` ``/Author``; ``xmp`` ``xmp``; ``page[1]`` ``line[4]`` (pdfplumber
      ``extract_text`` line), ``annot[2]/Contents``, ``annot[2]/URI``,
      ``annot[3]/V``; ``struct`` ``alt[1]``; ``outline`` ``item[1]``.
csv   ``""`` ``row[3]/col[2]`` (1-based, header is row 1).
txt   ``""`` ``line[7]`` (line where the match starts).
md    ``""`` ``line[7]`` (rendered text: emphasis removed, entities decoded,
      link text kept), ``line[9]/link[1]@target``, ``line[11]/image[1]@alt``,
      ``line[2]/comment``, ``front_matter/author``.
"""

from __future__ import annotations

import csv
import html
import io
import re
import zipfile
import zlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote
from xml.etree import ElementTree as ET


@dataclass(frozen=True)
class Segment:
    part: str
    path: str
    text: str
    visible: bool
    # When True, ``path`` is ignored and each match is located as ``line[n]``
    # from its offset in ``text``.
    line_mapped: bool = False


NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "vt": "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes",
}


def q(prefix: str, tag: str) -> str:
    return f"{{{NS[prefix]}}}{tag}"


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


R_ID = q("r", "id")


def detect_format(path: str | Path) -> str:
    suffix = Path(path).suffix.lower().lstrip(".")
    if suffix in ("docx", "xlsx", "pptx", "pdf", "csv", "txt", "md"):
        return suffix
    raise ValueError(f"unsupported file type: {path}")


def extract_segments(path: str | Path) -> list[Segment]:
    fmt = detect_format(path)
    if fmt in ("docx", "xlsx", "pptx"):
        with zipfile.ZipFile(path) as zf:
            return {"docx": _docx, "xlsx": _xlsx, "pptx": _pptx}[fmt](_Package(zf))
    if fmt == "pdf":
        return _pdf(path)
    text = Path(path).read_bytes().decode("utf-8-sig", errors="replace")
    if fmt == "csv":
        return _csv(text)
    if fmt == "md":
        return _md(text)
    return [Segment("", "", text, True, line_mapped=True)]


# --------------------------------------------------------------------------- OOXML

class _Package:
    def __init__(self, zf: zipfile.ZipFile):
        self.zf = zf
        self.names = set(zf.namelist())

    def xml(self, name: str) -> ET.Element | None:
        if name not in self.names:
            return None
        return ET.fromstring(self.zf.read(name))

    @staticmethod
    def rels_name(part: str) -> str:
        p = PurePosixPath(part)
        return str(p.parent / "_rels" / (p.name + ".rels"))

    def rels(self, part: str) -> dict[str, tuple[str, str]]:
        """rId -> (resolved target, TargetMode) for *part*."""
        root = self.xml(self.rels_name(part))
        out: dict[str, tuple[str, str]] = {}
        if root is None:
            return out
        base = PurePosixPath(part).parent
        for rel in root:
            target = rel.get("Target", "")
            mode = rel.get("TargetMode", "Internal")
            if mode != "External":
                target = _resolve(base, target)
            out[rel.get("Id", "")] = (target, mode)
        return out


def _resolve(base: PurePosixPath, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    parts: list[str] = []
    for piece in (base / target).parts:
        if piece == "..":
            if parts:
                parts.pop()
        elif piece != ".":
            parts.append(piece)
    return "/".join(parts)


def _props(pkg: _Package, out: list[Segment]) -> None:
    core = pkg.xml("docProps/core.xml")
    if core is not None:
        for el in core:
            if el.text and el.text.strip():
                out.append(Segment("docProps/core.xml", f"core/{local(el.tag)}", el.text, False))
    app = pkg.xml("docProps/app.xml")
    if app is not None:
        for path, text in _leaf_paths(app, "app"):
            out.append(Segment("docProps/app.xml", path, text, False))
    custom = pkg.xml("docProps/custom.xml")
    if custom is not None:
        for prop in custom:
            text = "".join(prop.itertext())
            if text:
                out.append(Segment("docProps/custom.xml", f"custom/{prop.get('name')}", text, False))


def _leaf_paths(el: ET.Element, prefix: str):
    counts: Counter[str] = Counter()
    totals = Counter(local(c.tag) for c in el)
    for child in el:
        name = local(child.tag)
        counts[name] += 1
        path = f"{prefix}/{name}" + (f"[{counts[name]}]" if totals[name] > 1 else "")
        if len(child):
            yield from _leaf_paths(child, path)
        elif child.text and child.text.strip():
            yield path, child.text


# ---------------------------------------------------------------------------- docx

W = {t: q("w", t) for t in (
    "p", "tbl", "tr", "tc", "sdt", "sdtContent", "sdtPr", "t", "tab", "br", "cr", "noBreakHyphen",
    "delText", "instrText", "txbxContent", "hyperlink", "ffData", "del", "ins", "comment",
    "footnote", "endnote", "body", "alias", "tag", "name", "textInput", "default", "helpText",
    "statusText", "val", "author", "id", "type", "initials")}
WP_DOCPR = q("wp", "docPr")


class _DocxCtx:
    def __init__(self, part: str, rels: dict[str, tuple[str, str]], rels_part: str, out: list[Segment]):
        self.part, self.rels, self.rels_part, self.out = part, rels, rels_part, out

    def emit(self, path: str, text: str, visible: bool, part: str | None = None) -> None:
        self.out.append(Segment(part or self.part, path, text, visible))


def _docx_blocks(container: ET.Element, prefix: str, ctx: _DocxCtx) -> None:
    counts: Counter[str] = Counter()
    for child in container:
        if child.tag == W["p"]:
            counts["p"] += 1
            _docx_para(child, f"{prefix}p[{counts['p']}]", ctx)
        elif child.tag == W["tbl"]:
            counts["tbl"] += 1
            tpath = f"{prefix}tbl[{counts['tbl']}]"
            for r, tr in enumerate(child.findall(W["tr"]), 1):
                for c, tc in enumerate(tr.findall(W["tc"]), 1):
                    _docx_blocks(tc, f"{tpath}/tr[{r}]/tc[{c}]/", ctx)
        elif child.tag == W["sdt"]:
            counts["sdt"] += 1
            spath = f"{prefix}sdt[{counts['sdt']}]"
            _docx_sdt_props(child, spath, ctx)
            content = child.find(W["sdtContent"])
            if content is not None:
                _docx_blocks(content, spath + "/", ctx)


def _docx_sdt_props(sdt: ET.Element, path: str, ctx: _DocxCtx) -> None:
    props = sdt.find(W["sdtPr"])
    if props is None:
        return
    for tag in ("alias", "tag"):
        el = props.find(W[tag])
        if el is not None and el.get(W["val"]):
            ctx.emit(f"{path}@{tag}", el.get(W["val"]), False)


def _docx_para(p: ET.Element, path: str, ctx: _DocxCtx) -> None:
    buf: list[str] = []
    deleted: list[str] = []
    instr: list[str] = []
    counts: Counter[str] = Counter()

    def walk(el: ET.Element) -> None:
        for ch in el:
            tag = ch.tag
            if tag == W["t"]:
                buf.append(ch.text or "")
            elif tag == W["tab"]:
                buf.append("\t")
            elif tag in (W["br"], W["cr"]):
                buf.append("\n")
            elif tag == W["noBreakHyphen"]:
                buf.append("-")
            elif tag == W["delText"]:
                deleted.append(ch.text or "")
            elif tag == W["instrText"]:
                instr.append(ch.text or "")
            elif tag == W["txbxContent"]:
                counts["txbx"] += 1
                _docx_blocks(ch, f"{path}/txbx[{counts['txbx']}]/", ctx)
            elif tag == W["sdtPr"]:
                continue
            else:
                if tag == W["hyperlink"] and ch.get(R_ID):
                    counts["hyperlink"] += 1
                    target, _ = ctx.rels.get(ch.get(R_ID), ("", ""))
                    ctx.emit(f"{path}/hyperlink[{counts['hyperlink']}]@target", unquote(target),
                             False, part=ctx.rels_part)
                elif tag == WP_DOCPR:
                    counts["drawing"] += 1
                    for attr in ("descr", "title"):
                        if ch.get(attr):
                            ctx.emit(f"{path}/drawing[{counts['drawing']}]@{attr}", ch.get(attr), False)
                elif tag == W["ffData"]:
                    counts["ffdata"] += 1
                    fpath = f"{path}/ffdata[{counts['ffdata']}]"
                    for sub, label in ((W["name"], "name"), (W["helpText"], "help"),
                                       (W["statusText"], "status")):
                        node = ch.find(sub)
                        if node is not None and node.get(W["val"]):
                            ctx.emit(f"{fpath}@{label}", node.get(W["val"]), False)
                    default = ch.find(f"{W['textInput']}/{W['default']}")
                    if default is not None and default.get(W["val"]):
                        ctx.emit(f"{fpath}@default", default.get(W["val"]), False)
                elif tag in (W["del"], W["ins"]):
                    key = local(tag)
                    counts[key] += 1
                    if ch.get(W["author"]):
                        ctx.emit(f"{path}/{key}[{counts[key]}]@author", ch.get(W["author"]), False)
                elif tag == W["sdt"]:
                    counts["sdt"] += 1
                    _docx_sdt_props(ch, f"{path}/sdt[{counts['sdt']}]", ctx)
                walk(ch)

    walk(p)
    ctx.emit(path, "".join(buf), True)
    if deleted:
        ctx.emit(f"{path}/deleted", "".join(deleted), False)
    if instr:
        ctx.emit(f"{path}/instr", "".join(instr), False)


def _docx(pkg: _Package) -> list[Segment]:
    out: list[Segment] = []
    _props(pkg, out)
    for name in sorted(pkg.names):
        if not name.startswith("word/") or not name.endswith(".xml") or "/_rels/" in name:
            continue
        root = pkg.xml(name)
        ctx = _DocxCtx(name, pkg.rels(name), pkg.rels_name(name), out)
        tag = local(root.tag)
        if tag == "document":
            body = root.find(W["body"])
            if body is not None:
                _docx_blocks(body, "body/", ctx)
        elif tag in ("hdr", "ftr"):
            _docx_blocks(root, "", ctx)
        elif tag in ("comments", "footnotes", "endnotes"):
            for note in root:
                if note.get(W["type"]) in ("separator", "continuationSeparator", "continuationNotice"):
                    continue
                kind = local(note.tag)
                npath = f"{kind}[{note.get(W['id'])}]"
                for attr in ("author", "initials"):
                    if note.get(W[attr]):
                        ctx.emit(f"{npath}@{attr}", note.get(W[attr]), False)
                _docx_blocks(note, npath + "/", ctx)
        elif tag == "people":
            for k, person in enumerate(root, 1):
                for attr_name, value in person.attrib.items():
                    if local(attr_name) == "author":
                        ctx.emit(f"person[{k}]@author", value, False)
    return out


# ---------------------------------------------------------------------------- pptx

SHAPE_TAGS = {q("p", t) for t in ("sp", "grpSp", "graphicFrame", "cxnSp", "pic", "contentPart")}


def _a_para(p: ET.Element, path: str, ctx: _DocxCtx) -> None:
    buf: list[str] = []
    links = 0
    for ch in p:
        tag = local(ch.tag)
        if tag in ("r", "fld"):
            t = ch.find(q("a", "t"))
            buf.append(t.text or "" if t is not None else "")
            rpr = ch.find(q("a", "rPr"))
            if rpr is not None:
                for hl_tag in ("hlinkClick", "hlinkMouseOver"):
                    hl = rpr.find(q("a", hl_tag))
                    if hl is not None and hl.get(R_ID):
                        links += 1
                        target, _ = ctx.rels.get(hl.get(R_ID), ("", ""))
                        ctx.emit(f"{path}/hlink[{links}]@target", unquote(target), False,
                                 part=ctx.rels_part)
        elif tag == "br":
            buf.append("\n")
    ctx.emit(path, "".join(buf), True)


def _a_text_body(body: ET.Element, prefix: str, ctx: _DocxCtx) -> None:
    for j, p in enumerate(body.findall(q("a", "p")), 1):
        _a_para(p, f"{prefix}p[{j}]", ctx)


def _pptx_shapes(tree: ET.Element, prefix: str, ctx: _DocxCtx) -> None:
    k = 0
    for el in tree:
        if el.tag not in SHAPE_TAGS:
            continue
        k += 1
        spath = f"{prefix}shape[{k}]"
        nv = el[0] if len(el) else None
        cnv = nv.find(q("p", "cNvPr")) if nv is not None else None
        if cnv is not None:
            for attr in ("descr", "title"):
                if cnv.get(attr):
                    ctx.emit(f"{spath}@{attr}", cnv.get(attr), False)
            hl = cnv.find(q("a", "hlinkClick"))
            if hl is not None and hl.get(R_ID):
                target, _ = ctx.rels.get(hl.get(R_ID), ("", ""))
                ctx.emit(f"{spath}@hlink", unquote(target), False, part=ctx.rels_part)
        if el.tag == q("p", "grpSp"):
            _pptx_shapes(el, spath + "/", ctx)
        body = el.find(q("p", "txBody"))
        if body is not None:
            _a_text_body(body, spath + "/", ctx)
        tbl = el.find(f"{q('a', 'graphic')}/{q('a', 'graphicData')}/{q('a', 'tbl')}")
        if tbl is not None:
            for r, tr in enumerate(tbl.findall(q("a", "tr")), 1):
                for c, tc in enumerate(tr.findall(q("a", "tc")), 1):
                    tbody = tc.find(q("a", "txBody"))
                    if tbody is not None:
                        _a_text_body(tbody, f"{spath}/tbl/tr[{r}]/tc[{c}]/", ctx)


def _pptx(pkg: _Package) -> list[Segment]:
    out: list[Segment] = []
    _props(pkg, out)
    for name in sorted(pkg.names):
        if "/_rels/" in name or not name.endswith(".xml"):
            continue
        ctx = _DocxCtx(name, pkg.rels(name), pkg.rels_name(name), out)
        if re.fullmatch(r"ppt/(slides/slide|notesSlides/notesSlide)\d+\.xml", name):
            tree = pkg.xml(name).find(f"{q('p', 'cSld')}/{q('p', 'spTree')}")
            if tree is not None:
                _pptx_shapes(tree, "", ctx)
        elif re.fullmatch(r"ppt/charts/chart\d+\.xml", name):
            _chart(pkg.xml(name), ctx)
        elif re.fullmatch(r"ppt/comments/\w+\.xml", name):
            for k, cm in enumerate(pkg.xml(name), 1):
                text = cm.find(q("p", "text"))
                if text is not None and text.text:
                    ctx.emit(f"comment[{k}]", text.text, True)
        elif name == "ppt/commentAuthors.xml":
            for k, author in enumerate(pkg.xml(name), 1):
                for attr in ("name", "initials"):
                    if author.get(attr):
                        ctx.emit(f"author[{k}]@{attr}", author.get(attr), False)
    return out


def _chart(root: ET.Element, ctx: _DocxCtx) -> None:
    for t, title in enumerate(root.iter(q("c", "title")), 1):
        paras = ["".join(x.text or "" for x in p.iter(q("a", "t"))) for p in title.iter(q("a", "p"))]
        values = [v.text or "" for v in title.iter(q("c", "v"))]
        ctx.emit(f"title[{t}]", "\n".join(paras + values), True)
    for k, cache in enumerate(root.iter(q("c", "strCache")), 1):
        for pt in cache.findall(q("c", "pt")):
            v = pt.find(q("c", "v"))
            ctx.emit(f"strCache[{k}]/pt[{pt.get('idx')}]", v.text or "" if v is not None else "", True)


# ---------------------------------------------------------------------------- xlsx

S = {t: q("s", t) for t in (
    "sheets", "sheet", "si", "t", "rPh", "sheetData", "row", "c", "v", "f", "is", "headerFooter",
    "hyperlinks", "hyperlink", "authors", "author", "commentList", "comment", "text",
    "definedNames", "definedName")}


def _si_text(si: ET.Element) -> str:
    # Concatenate all runs; skip phonetic hints (rPh), which are not displayed.
    phonetic = {id(t) for r in si.iter(S["rPh"]) for t in r.iter(S["t"])}
    return "".join(t.text or "" for t in si.iter(S["t"]) if id(t) not in phonetic)


def _xlsx(pkg: _Package) -> list[Segment]:
    out: list[Segment] = []
    _props(pkg, out)
    wb_part = "xl/workbook.xml"
    wb = pkg.xml(wb_part)
    wb_rels = pkg.rels(wb_part)
    ss_part = next((t for t, _ in wb_rels.values() if t.endswith("sharedStrings.xml")), "xl/sharedStrings.xml")
    ss_root = pkg.xml(ss_part)
    shared = [_si_text(si) for si in ss_root.findall(S["si"])] if ss_root is not None else []

    sheets = wb.find(S["sheets"])
    sheet_list = list(sheets) if sheets is not None else []
    sheet_names = [s.get("name", "") for s in sheet_list]
    sheet_values: dict[str, dict[str, str]] = {}
    formulas: list[tuple[int, str, str, str]] = []
    for i, sheet in enumerate(sheet_list, 1):
        out.append(Segment(wb_part, f"sheet[{i}]@name", sheet.get("name", ""), True))
        part = wb_rels.get(sheet.get(R_ID), ("", ""))[0]
        root = pkg.xml(part)
        if root is None:
            continue
        values: dict[str, str] = {}
        sheet_values[sheet.get("name", "")] = values
        data = root.find(S["sheetData"])
        for c in (data.iter(S["c"]) if data is not None else ()):
            ref, typ = c.get("r", ""), c.get("t", "n")
            v, f = c.find(S["v"]), c.find(S["f"])
            if f is not None and f.text:
                out.append(Segment(part, f"sheet[{i}]!{ref}@formula", f.text, False))
            if typ == "s" and v is not None:
                text, where = shared[int(v.text)], ss_part
            elif typ == "inlineStr":
                is_ = c.find(S["is"])
                text, where = (_si_text(is_) if is_ is not None else ""), part
            elif v is not None and (v.text or f is None):
                text, where = v.text or "", part
            elif f is not None and f.text:
                # No cached result (openpyxl never writes one): compute it.
                formulas.append((i, part, ref, f.text))
                continue
            else:
                continue
            values[ref] = text
            if text:
                out.append(Segment(where, f"sheet[{i}]!{ref}", text, True))
        hf = root.find(S["headerFooter"])
        if hf is not None:
            for el in hf:
                if el.text:
                    out.append(Segment(part, f"sheet[{i}]/{local(el.tag)}", el.text, True))
        rels = pkg.rels(part)
        links = root.find(S["hyperlinks"])
        for hl in (links if links is not None else ()):
            ref = hl.get("ref", "")
            if hl.get(R_ID):
                target, _ = rels.get(hl.get(R_ID), ("", ""))
                out.append(Segment(pkg.rels_name(part), f"sheet[{i}]!{ref}@hyperlink", unquote(target), False))
            for attr in ("location", "display", "tooltip"):
                if hl.get(attr):
                    out.append(Segment(part, f"sheet[{i}]!{ref}@hyperlink_{attr}", hl.get(attr), False))
        for target, _ in rels.values():
            if re.search(r"/comments?\d*\.xml$", target) and target in pkg.names:
                croot = pkg.xml(target)
                authors_el = croot.find(S["authors"])
                authors = [a.text or "" for a in authors_el] if authors_el is not None else []
                clist = croot.find(S["commentList"])
                for cm in (clist if clist is not None else ()):
                    ref = cm.get("ref", "")
                    text_el = cm.find(S["text"])
                    out.append(Segment(target, f"sheet[{i}]!{ref}@comment",
                                       _si_text(text_el) if text_el is not None else "", True))
                    aid = int(cm.get("authorId", "-1"))
                    if 0 <= aid < len(authors) and authors[aid]:
                        out.append(Segment(target, f"sheet[{i}]!{ref}@comment_author", authors[aid], False))

    names = wb.find(S["definedNames"])
    for k, dn in enumerate(names if names is not None else (), 1):
        out.append(Segment(wb_part, f"definedName[{k}]", dn.text or "", False))

    for i, part, ref, formula in formulas:
        result = evaluate_formula(formula, sheet_names[i - 1], sheet_values)
        if result:
            out.append(Segment(part, f"sheet[{i}]!{ref}", result, True))

    for name in sorted(pkg.names):
        if re.fullmatch(r"xl/charts/chart\d+\.xml", name):
            _chart(pkg.xml(name), _DocxCtx(name, {}, "", out))
        elif re.fullmatch(r"xl/drawings/drawing\d+\.xml", name):
            for k, cnv in enumerate(pkg.xml(name).iter(q("xdr", "cNvPr")), 1):
                for attr in ("descr", "title"):
                    if cnv.get(attr):
                        out.append(Segment(name, f"shape[{k}]@{attr}", cnv.get(attr), False))
    return out


_TOKEN = re.compile(r"""\s*(?:
    (?P<str>"(?:[^"]|"")*")
  | (?P<ref>(?:(?:'(?:[^']|'')+'|[A-Za-z_][\w.]*)!)?\$?[A-Z]{1,3}\$?\d+)(?![\w(])
  | (?P<func>[A-Za-z_][\w.]*)\s*\(
  | (?P<op>[&,()])
)""", re.X)


def evaluate_formula(formula: str, sheet: str, values: dict[str, dict[str, str]]) -> str | None:
    """Evaluate the string subset Excel uses to build names: string literals,
    cell references, ``&``, ``CONCAT``/``CONCATENATE``. Returns ``None`` for
    anything else. Formulas without cached values are how openpyxl writes
    them, so a leak scanner has to compute the displayed result itself."""
    src = formula[1:] if formula.startswith("=") else formula
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        if not m or m.end() == pos:
            if src[pos:].strip() == "":
                break
            return None
        kind = m.lastgroup
        tokens.append((kind, m.group(kind)))
        pos = m.end()
    tokens.append(("end", ""))
    idx = 0

    def peek():
        return tokens[idx]

    def take():
        nonlocal idx
        idx += 1
        return tokens[idx - 1]

    def expr() -> str:
        result = term()
        while peek() == ("op", "&"):
            take()
            result += term()
        return result

    def term() -> str:
        kind, text = take()
        if kind == "str":
            return text[1:-1].replace('""', '"')
        if kind == "ref":
            sheet_name, _, cell = text.rpartition("!")
            sheet_name = sheet_name.strip("'").replace("''", "'") if sheet_name else sheet
            return values.get(sheet_name, {}).get(cell.replace("$", ""), "")
        if kind == "func":
            name = text.upper().removeprefix("_XLFN.")
            if name not in ("CONCAT", "CONCATENATE"):
                raise ValueError(name)
            args = [expr()]
            while peek() == ("op", ","):
                take()
                args.append(expr())
            if take() != ("op", ")"):
                raise ValueError("expected )")
            return "".join(args)
        if (kind, text) == ("op", "("):
            inner = expr()
            if take() != ("op", ")"):
                raise ValueError("expected )")
            return inner
        raise ValueError(text)

    try:
        result = expr()
        return result if peek()[0] == "end" else None
    except (ValueError, IndexError):
        return None


# ----------------------------------------------------------------------------- pdf

def _pdf(path: str | Path) -> list[Segment]:
    import pdfplumber
    import pypdf

    out: list[Segment] = []
    reader = pypdf.PdfReader(str(path))
    for key, value in (reader.metadata or {}).items():
        out.append(Segment("info", str(key), str(value), False))
    root = reader.trailer["/Root"]
    if "/Metadata" in root:
        xmp = root["/Metadata"].get_object().get_data().decode("utf-8", errors="replace")
        out.append(Segment("xmp", "xmp", _strip_markup(xmp), False))

    with pdfplumber.open(str(path)) as pdf:
        for n, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            if text:
                for k, line in enumerate(text.split("\n"), 1):
                    out.append(Segment(f"page[{n}]", f"line[{k}]", line, True))

    for n, page in enumerate(reader.pages, 1):
        annots = page.get("/Annots")
        annots = annots.get_object() if annots is not None else []
        for k, ref in enumerate(annots, 1):
            annot = ref.get_object()
            for key, visible in (("/Contents", True), ("/V", True), ("/T", False), ("/TU", False)):
                if key in annot:
                    out.append(Segment(f"page[{n}]", f"annot[{k}]{key}", str(annot[key]), visible))
            action = annot.get("/A")
            if action is not None:
                action = action.get_object()
                if "/URI" in action:
                    out.append(Segment(f"page[{n}]", f"annot[{k}]/URI", unquote(str(action["/URI"])), False))

    if "/StructTreeRoot" in root:
        counters: Counter[str] = Counter()
        seen: set[int] = set()

        def walk(node) -> None:
            node = node.get_object() if hasattr(node, "get_object") else node
            if id(node) in seen:
                return
            seen.add(id(node))
            if isinstance(node, pypdf.generic.ArrayObject):
                for item in node:
                    walk(item)
            elif isinstance(node, pypdf.generic.DictionaryObject):
                for key, label in (("/Alt", "alt"), ("/ActualText", "actualtext"), ("/E", "expansion")):
                    if key in node:
                        counters[label] += 1
                        out.append(Segment("struct", f"{label}[{counters[label]}]", str(node[key]), False))
                if "/K" in node:
                    walk(node["/K"])

        walk(root["/StructTreeRoot"])

    titles: list[str] = []

    def flatten(items) -> None:
        for item in items:
            if isinstance(item, list):
                flatten(item)
            else:
                titles.append(str(item.title))

    try:
        flatten(reader.outline)
    except Exception:  # malformed outlines should not stop extraction
        pass
    for k, title in enumerate(titles, 1):
        out.append(Segment("outline", f"item[{k}]", title, False))
    return out


# ------------------------------------------------------------------------ csv / md

def _csv(text: str) -> list[Segment]:
    out = []
    for r, row in enumerate(csv.reader(io.StringIO(text, newline="")), 1):
        for c, field in enumerate(row, 1):
            out.append(Segment("", f"row[{r}]/col[{c}]", field, True))
    return out


_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]*)(?:\s+\"([^\"]*)\")?\)")
_MD_LINK = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)\s]*)(?:\s+\"([^\"]*)\")?\)")
_MD_AUTOLINK = re.compile(r"<((?:mailto:|https?://)[^>\s]+)>")
_MD_FOOTREF = re.compile(r"\[\^([^\]]+)\](:?)")
_ESCAPABLE = "\\`*_{}[]()#+-.!|<>~"


def render_markdown(text: str) -> tuple[str, list[Segment]]:
    """Rendered (reader-visible) text with the same line numbering as the
    source, plus hidden segments (front matter, link targets, image alt text,
    HTML comments). Covers CommonMark's inline subset that matters for
    matching; it is not a full Markdown renderer."""
    lines = text.split("\n")
    hidden: list[Segment] = []
    rendered: list[str] = []
    n = 0
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is not None:
            for line in lines[1:end]:
                key, sep, value = line.partition(":")
                if sep:
                    hidden.append(Segment("", f"front_matter/{key.strip()}", value.strip().strip('"'), False))
            rendered.extend([""] * (end + 1))
            n = end + 1
    comment_start: int | None = None
    comment_buf: list[str] = []
    while n < len(lines):
        line = lines[n]
        lineno = n + 1
        out_line = ""
        rest = line
        while rest:
            if comment_start is not None:
                end = rest.find("-->")
                if end == -1:
                    comment_buf.append(rest)
                    rest = ""
                    continue
                comment_buf.append(rest[:end])
                hidden.append(Segment("", f"line[{comment_start}]/comment", "\n".join(comment_buf), False))
                comment_start, comment_buf, rest = None, [], rest[end + 3:]
                continue
            start = rest.find("<!--")
            if start == -1:
                out_line += rest
                break
            out_line += rest[:start]
            comment_start, rest = lineno, rest[start + 4:]
        rendered.append(_render_inline(out_line, lineno, hidden))
        n += 1
    return "\n".join(rendered), hidden


def _render_inline(line: str, lineno: int, hidden: list[Segment]) -> str:
    escapes: list[str] = []

    def protect(m: re.Match) -> str:
        escapes.append(m.group(1))
        return f"{len(escapes) - 1}"

    line = re.sub(r"\\([" + re.escape(_ESCAPABLE) + "])", protect, line)
    images = 0

    def image(m: re.Match) -> str:
        nonlocal images
        images += 1
        hidden.append(Segment("", f"line[{lineno}]/image[{images}]@alt", m.group(1), False))
        hidden.append(Segment("", f"line[{lineno}]/image[{images}]@src", unquote(m.group(2)), False))
        if m.group(3):
            hidden.append(Segment("", f"line[{lineno}]/image[{images}]@title", m.group(3), False))
        return ""

    links = 0

    def link(m: re.Match) -> str:
        nonlocal links
        links += 1
        hidden.append(Segment("", f"line[{lineno}]/link[{links}]@target", unquote(m.group(2)), False))
        if m.group(3):
            hidden.append(Segment("", f"line[{lineno}]/link[{links}]@title", m.group(3), False))
        return m.group(1)

    line = _MD_IMAGE.sub(image, line)
    line = _MD_LINK.sub(link, line)
    line = _MD_AUTOLINK.sub(lambda m: m.group(1), line)
    line = _MD_FOOTREF.sub(lambda m: f"[{m.group(1)}]{m.group(2)}", line)
    line = re.sub(r"\*\*|__|~~|[*`]", "", line)
    line = html.unescape(line)
    return re.sub("(\\d+)", lambda m: escapes[int(m.group(1))], line)


def _md(text: str) -> list[Segment]:
    rendered, hidden = render_markdown(text)
    return [Segment("", "", rendered, True, line_mapped=True), *hidden]


# ----------------------------------------------------------------------- raw sweep

_BLOCK_TAGS = re.compile(r"</?(?:[\w.-]+:)?(?:p|tr|tc|si|c|v|pt|row|comment|author|t|title|lpstr|br|tab)\b[^>]*>")
_ANY_TAG = re.compile(r"<[^>]*>")
_ATTR = re.compile(r"""[\s][\w:.-]+\s*=\s*(?:"([^"]*)"|'([^']*)')""")


def _strip_markup(xml: str) -> str:
    # Inline run boundaries vanish (so split runs re-join); block boundaries
    # become newlines. w:t and a:t are inline: only their tags are removed.
    xml = re.sub(r"</?(?:[\w.-]+:)?(?:t|r|rPr)\b[^>]*>", "", xml)
    xml = _BLOCK_TAGS.sub("\n", xml)
    return html.unescape(_ANY_TAG.sub("", xml))


def _attr_values(xml: str) -> str:
    vals = []
    for m in _ATTR.finditer(xml):
        v = html.unescape(m.group(1) if m.group(1) is not None else m.group(2))
        vals.append(v)
        decoded = unquote(v)
        if decoded != v:
            vals.append(decoded)
    return "\n".join(vals)


def _binary_views(blob: bytes) -> list[str]:
    latin = blob.decode("latin-1")
    return [latin, latin.replace("\x00", "")]


def raw_texts(path: str | Path) -> list[tuple[str, str]]:
    """Every text-like view of every byte in the file, as (where, text)."""
    blob = Path(path).read_bytes()
    if blob.startswith(b"PK\x03\x04"):
        return _raw_zip(blob, "")
    if blob.startswith(b"%PDF"):
        return _raw_pdf(path, blob)
    text = blob.decode("utf-8-sig", errors="replace")
    out = [("file", text)]
    if Path(path).suffix.lower() == ".md":
        rendered, hidden = render_markdown(text)
        out.append(("file:rendered", rendered))
        out.extend(("file:" + s.path, s.text) for s in hidden)
    return out


def _raw_zip(blob: bytes, prefix: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        for info in zf.infolist():
            name = prefix + info.filename
            out.append((name + "#zipname", info.filename))
            if info.comment:
                out.append((name + "#zipcomment", info.comment.decode("utf-8", "replace")))
            data = zf.read(info)
            if data.startswith(b"PK\x03\x04"):
                out.extend(_raw_zip(data, name + "!/"))
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                out.extend((name, v) for v in _binary_views(data))
                continue
            if text.lstrip("﻿").lstrip().startswith("<"):
                out.append((name, _strip_markup(text)))
                out.append((name + "#attributes", _attr_values(text)))
            else:
                out.append((name, text))
        if zf.comment:
            out.append((prefix + "#zipcomment", zf.comment.decode("utf-8", "replace")))
    return out


def _raw_pdf(path: str | Path, blob: bytes) -> list[tuple[str, str]]:
    import pypdf

    out: list[tuple[str, str]] = [("bytes", v) for v in _binary_views(blob)]
    # Every stream in the file, reachable or not (incremental updates leave
    # old objects behind), decompressed where possible.
    for k, m in enumerate(re.finditer(rb"stream\r?\n(.*?)endstream", blob, re.S), 1):
        data = m.group(1)
        try:
            data = zlib.decompress(data)
        except zlib.error:
            try:
                data = zlib.decompressobj().decompress(data)
            except zlib.error:
                pass
        views = _binary_views(data)
        literals = "".join(re.findall(r"\(((?:\\.|[^\\)])*)\)", views[0]))
        out.extend((f"stream[{k}]", v) for v in (*views, literals))
    try:
        reader = pypdf.PdfReader(str(path))
    except Exception:
        return out
    seen: set[tuple[int, int]] = set()
    strings: list[str] = []

    def visit(obj, depth: int = 0) -> None:
        if depth > 200:
            return
        if isinstance(obj, pypdf.generic.IndirectObject):
            key = (obj.idnum, obj.generation)
            if key in seen:
                return
            seen.add(key)
            try:
                obj = obj.get_object()
            except Exception:
                return
        if isinstance(obj, (pypdf.generic.TextStringObject, pypdf.generic.ByteStringObject)):
            strings.append(str(obj) if isinstance(obj, pypdf.generic.TextStringObject)
                           else obj.original_bytes.decode("latin-1"))
        elif isinstance(obj, pypdf.generic.DictionaryObject):
            for value in obj.values():
                visit(value, depth + 1)
        elif isinstance(obj, pypdf.generic.ArrayObject):
            for value in obj:
                visit(value, depth + 1)

    visit(reader.trailer)
    out.append(("objects", "\n".join(strings)))
    for n, page in enumerate(reader.pages, 1):
        try:
            out.append((f"page[{n}]:pypdf", page.extract_text() or ""))
        except Exception:
            pass
    return out
