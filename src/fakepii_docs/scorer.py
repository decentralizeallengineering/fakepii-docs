"""Score a masked (and optionally unmasked) document against its truth.

* **Leaks** - every true PII value still present anywhere in the masked file:
  the structured view (body, cells, notes, metadata, alt text, link targets,
  computed formulas ...) plus a raw sweep of every zip member / PDF object
  and stream. Near-miss contexts are blanked first, so the city
  ``Example Township`` does not count as a leak of the surname ``Example``.
  For image-only PDFs, each planted value's bounding box is compared with the
  original pixels: an unchanged box means the value is still visible.
* **Over-masking** - near misses (labeled non-PII) that were altered or
  removed: fewer occurrences of the near-miss context than in the original,
  or a changed bounding box in an image-only PDF.
* **Round-trip fidelity** - when an unmasked file is given, every visible text
  segment of the original must be restored byte-for-byte (hidden segments
  such as metadata are compared and reported separately). For image-only
  PDFs the rendered pages are compared pixel by pixel.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .extract import Segment, extract_segments, raw_texts
from .scan import Needle
from .textmatch import CASE_INSENSITIVE_TYPES, blank_spans, find_spans, line_of, normalize_with_map
from .truth import load_truth, sha256_file

RASTER_DPI = 100
PIXEL_DELTA = 48  # grey levels; smaller differences count as noise (re-encoding)
CHANGED_FRACTION = 0.10  # a bbox is "changed" when this share of pixels differs
PAGE_TOLERANCE = 0.0005  # round trip: share of differing pixels allowed per page


def _hits(text: str, values: list[Needle], contexts: list[Needle], line_mapped: bool,
          path: str) -> list[tuple[Needle, str]]:
    norm, index = normalize_with_map(text)
    blanked = norm
    for ctx in contexts:
        blanked = blank_spans(blanked, find_spans(norm, ctx.text))
    out = []
    for v in values:
        for s, _ in find_spans(blanked, v.text, ignore_case=v.ignore_case):
            out.append((v, f"line[{line_of(text, index[s])}]" if line_mapped else path))
    return out


def _context_counts(segments: list[Segment], contexts: list[Needle]) -> dict[str, Counter]:
    counts: dict[str, Counter] = defaultdict(Counter)
    for seg in segments:
        norm, index = normalize_with_map(seg.text)
        for ctx in contexts:
            for s, _ in find_spans(norm, ctx.text):
                where = f"line[{line_of(seg.text, index[s])}]" if seg.line_mapped else seg.path
                counts[ctx.key][f"{seg.part}#{where}" if seg.part else where] += 1
    return counts


def _render(path: str | Path) -> list[Any]:
    import numpy as np
    import pdfplumber

    with pdfplumber.open(str(path)) as pdf:
        return [np.asarray(page.to_image(resolution=RASTER_DPI).original.convert("L"), dtype=np.int16)
                for page in pdf.pages]


def _box_changed(a, b, bbox: list[float]) -> float:
    import numpy as np

    s = RASTER_DPI / 72.0
    x0, y0 = max(int(bbox[0] * s) - 1, 0), max(int(bbox[1] * s) - 1, 0)
    x1, y1 = int(np.ceil(bbox[2] * s)) + 1, int(np.ceil(bbox[3] * s)) + 1
    ca, cb = a[y0:y1, x0:x1], b[y0:y1, x0:x1]
    if ca.size == 0:
        return 0.0
    return float(np.mean(np.abs(ca - cb) > PIXEL_DELTA))


def _page_index(part: str) -> int:
    return int(part[len("page["):-1]) - 1


def score(original: str | Path, masked: str | Path, truth: str | Path | dict[str, Any],
          unmasked: str | Path | None = None) -> dict[str, Any]:
    """Return a JSON-serialisable report; ``report["passed"]`` is the verdict."""
    truth = load_truth(truth) if not isinstance(truth, dict) else truth
    fmt = truth["document"]["format"]
    warnings: list[str] = []
    if sha256_file(original) != truth["document"]["sha256"]:
        warnings.append("original file does not match the sha256 recorded in the truth file")

    values = [Needle(e["id"], e["value"], e["type"] in CASE_INSENSITIVE_TYPES) for e in truth["pii"]]
    contexts = [Needle(e["id"], e["context"], False) for e in truth["near_misses"]]

    def segments_of(path: str | Path, label: str) -> list[Segment] | None:
        try:
            return extract_segments(path)
        except Exception as exc:  # a broken output is a finding, not a crash
            warnings.append(f"{label} document could not be parsed: {type(exc).__name__}: {exc}")
            return None

    orig_segs = segments_of(original, "original") or []
    masked_segs = segments_of(masked, "masked")

    # ---------------------------------------------------------------- leaks
    found: dict[str, list[str]] = defaultdict(list)
    raw_found: dict[str, list[str]] = defaultdict(list)
    for seg in masked_segs or []:
        for v, where in _hits(seg.text, values, contexts, seg.line_mapped, seg.path):
            found[v.key].append(f"{seg.part}#{where}" if seg.part else where)
    for where, text in raw_texts(masked):
        for v, _ in _hits(text, values, contexts, False, ""):
            if where not in raw_found[v.key]:
                raw_found[v.key].append(where)

    over: dict[str, dict[str, Any]] = {}
    raster: dict[str, Any] | None = None
    if fmt == "pdf-image":
        orig_pages, masked_pages = _render(original), None
        try:
            masked_pages = _render(masked)
        except Exception as exc:
            warnings.append(f"masked PDF could not be rendered: {exc}")
        comparable = masked_pages is not None and len(masked_pages) == len(orig_pages) and all(
            a.shape == b.shape for a, b in zip(orig_pages, masked_pages))
        if masked_pages is not None and not comparable:
            warnings.append("masked PDF pages differ in count or size from the original; "
                            "raster locations could not be checked and are reported as unverified")
        raster = {"checked": comparable, "unverified": []}
        for entry in truth["pii"] + truth["near_misses"]:
            for loc in entry["locations"]:
                for bbox in loc.get("bboxes", []):
                    where = f"{loc['part']}#{loc['path']}"
                    if not comparable:
                        raster["unverified"].append(f"{entry['id']} {where}")
                        continue
                    i = _page_index(loc["part"])
                    changed = _box_changed(orig_pages[i], masked_pages[i], bbox) > CHANGED_FRACTION
                    if entry["id"].startswith("pii-") and not changed:
                        found[entry["id"]].append(f"{where} (pixels unchanged)")
                    elif entry["id"].startswith("nm-") and changed:
                        over.setdefault(entry["id"], {"changed_regions": []})["changed_regions"].append(where)

    leaks = []
    for e in truth["pii"]:
        if found.get(e["id"]) or raw_found.get(e["id"]):
            leaks.append({"id": e["id"], "type": e["type"], "value": e["value"], "entity": e["entity"],
                          "locations": found.get(e["id"], []), "raw": raw_found.get(e["id"], [])})

    # ---------------------------------------------------------- over-masking
    orig_counts = _context_counts(orig_segs, contexts)
    masked_counts = _context_counts(masked_segs or [], contexts)
    over_masking = []
    for nm in truth["near_misses"]:
        before, after = orig_counts.get(nm["id"], Counter()), masked_counts.get(nm["id"], Counter())
        missing = sorted(loc for loc in before if after[loc] < before[loc])
        extra = over.get(nm["id"], {}).get("changed_regions", [])
        if sum(after.values()) < sum(before.values()) or missing or extra:
            over_masking.append({"id": nm["id"], "kind": nm["kind"], "context": nm["context"], "label": nm["label"],
                                 "original_count": sum(before.values()), "masked_count": sum(after.values()),
                                 "missing_at": missing, "changed_regions": extra})

    # ------------------------------------------------------------ round trip
    round_trip = None
    if unmasked is not None:
        round_trip = _round_trip(original, unmasked, orig_segs, segments_of(unmasked, "unmasked"), fmt, truth)

    value_occurrences = sum(len(found.get(e["id"], [])) for e in truth["pii"])
    passed = (not leaks and not over_masking and masked_segs is not None
              and (raster is None or raster["checked"])
              and (round_trip is None or round_trip["passed"]))
    return {
        "document": truth["document"]["file"],
        "format": fmt,
        "masked": str(masked),
        "unmasked": str(unmasked) if unmasked is not None else None,
        "leaks": leaks,
        "leak_summary": {"values_total": len(truth["pii"]), "values_leaked": len(leaks),
                         "occurrences": value_occurrences},
        "over_masking": over_masking,
        "over_masking_summary": {"near_misses_total": len(truth["near_misses"]),
                                 "near_misses_damaged": len(over_masking)},
        "raster": raster,
        "round_trip": round_trip,
        "warnings": warnings,
        "passed": passed,
    }


def _first_diff(a: str, b: str) -> int:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b))


def _round_trip(original, unmasked, orig_segs: list[Segment], un_segs: list[Segment] | None,
                fmt: str, truth: dict[str, Any]) -> dict[str, Any]:
    if un_segs is None:
        return {"passed": False, "error": "unmasked document could not be parsed"}

    def keyed(segs: list[Segment]) -> dict[tuple[str, str], Segment]:
        out: dict[tuple[str, str], Segment] = {}
        for s in segs:
            key = (s.part, s.path)
            n = 2
            while key in out:  # duplicate paths (should not happen) stay distinct
                key = (s.part, f"{s.path}#{n}")
                n += 1
            out[key] = s
        return out

    before, after = keyed(orig_segs), keyed(un_segs)
    visible_bad, hidden_bad = [], []
    visible_total = 0
    for key, seg in before.items():
        visible_total += seg.visible
        got = after.get(key)
        if got is not None and got.text == seg.text:
            continue
        item = {"where": f"{key[0]}#{key[1]}" if key[0] else key[1] or "(whole file)",
                "expected": seg.text[:200], "got": None if got is None else got.text[:200],
                "first_diff": None if got is None else _first_diff(seg.text, got.text)}
        (visible_bad if seg.visible else hidden_bad).append(item)
    extra = [f"{k[0]}#{k[1]}" for k in after if k not in before and after[k].text.strip()]

    result: dict[str, Any] = {
        "visible_segments": visible_total,
        "visible_restored": visible_total - len(visible_bad),
        "mismatches": visible_bad,
        "hidden_mismatches": hidden_bad,
        "unexpected_segments": extra,
        "byte_identical_file": sha256_file(original) == sha256_file(unmasked),
    }
    passed = not visible_bad and not extra
    if fmt == "pdf-image":
        import numpy as np

        a_pages, b_pages = _render(original), _render(unmasked)
        pages = []
        if len(a_pages) != len(b_pages) or any(x.shape != y.shape for x, y in zip(a_pages, b_pages)):
            pages.append({"page": None, "error": "page count or size differs"})
        else:
            for i, (x, y) in enumerate(zip(a_pages, b_pages), 1):
                share = float(np.mean(np.abs(x - y) > PIXEL_DELTA))
                if share > PAGE_TOLERANCE:
                    pages.append({"page": i, "changed_pixel_share": round(share, 6)})
            for entry in truth["pii"] + truth["near_misses"]:
                for loc in entry["locations"]:
                    for bbox in loc.get("bboxes", []):
                        i = _page_index(loc["part"])
                        if _box_changed(a_pages[i], b_pages[i], bbox) > 0:
                            pages.append({"page": i + 1, "region": f"{entry['id']} {loc['path']}",
                                          "changed_pixel_share": round(_box_changed(a_pages[i], b_pages[i], bbox), 4)})
        result["raster_mismatches"] = pages
        passed = passed and not pages
    result["passed"] = passed
    return result


def format_report(report: dict[str, Any]) -> str:
    lines = [f"fakepii-docs score: {report['document']} ({report['format']})"]
    for w in report["warnings"]:
        lines.append(f"WARNING: {w}")
    ls = report["leak_summary"]
    lines.append(f"LEAKS: {ls['values_leaked']} of {ls['values_total']} PII values still present "
                 f"({ls['occurrences']} structured occurrences)")
    for leak in report["leaks"]:
        lines.append(f"  {leak['id']} {leak['type']} {leak['value']!r}")
        for loc in leak["locations"]:
            lines.append(f"      {loc}")
        if leak["raw"]:
            lines.append(f"      raw sweep: {', '.join(leak['raw'][:6])}" + (" ..." if len(leak["raw"]) > 6 else ""))
    if report["raster"] is not None and report["raster"]["unverified"]:
        lines.append(f"UNVERIFIED raster locations: {len(report['raster']['unverified'])}")
    os_ = report["over_masking_summary"]
    lines.append(f"OVER-MASKING: {os_['near_misses_damaged']} of {os_['near_misses_total']} near misses altered")
    for item in report["over_masking"]:
        lines.append(f"  {item['id']} {item['kind']} {item['context']!r}: "
                     f"{item['original_count']} -> {item['masked_count']}")
        for loc in item["missing_at"] + item["changed_regions"]:
            lines.append(f"      {loc}")
    rt = report["round_trip"]
    if rt is not None:
        if "error" in rt:
            lines.append(f"ROUND-TRIP: {rt['error']}")
        else:
            lines.append(f"ROUND-TRIP: {rt['visible_restored']}/{rt['visible_segments']} visible segments "
                         f"restored byte-for-byte; {len(rt['hidden_mismatches'])} hidden segment(s) differ"
                         + ("; file is byte-identical" if rt["byte_identical_file"] else ""))
            for m in rt["mismatches"]:
                if m["got"] is None:
                    lines.append(f"  {m['where']}: missing")
                else:
                    i = m["first_diff"]
                    lines.append(f"  {m['where']}: first difference at char {i}: "
                                 f"expected {m['expected'][i:i + 30]!r} got {m['got'][i:i + 30]!r}")
            for m in rt["hidden_mismatches"]:
                lines.append(f"  (hidden) {m['where']}")
            for w in rt["unexpected_segments"]:
                lines.append(f"  unexpected segment: {w}")
            for p in rt.get("raster_mismatches", []):
                lines.append(f"  raster: {p}")
    lines.append("RESULT: " + ("PASS" if report["passed"] else "FAIL"))
    return "\n".join(lines)
