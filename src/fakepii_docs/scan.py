"""Scan a document back and check its ground truth is exact and complete."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .extract import Segment, extract_segments, raw_texts
from .textmatch import CASE_INSENSITIVE_TYPES, find_spans, line_of, normalize, normalize_with_map


class Needle:
    """One string to look for: a PII value or a near-miss context."""

    def __init__(self, key: str, text: str, ignore_case: bool):
        self.key = key
        self.text = normalize(text)
        self.ignore_case = ignore_case

    def contains(self, other: "Needle") -> bool:
        if len(self.text) <= len(other.text):
            return False
        a, b = (self.text.lower(), other.text.lower()) if other.ignore_case else (self.text, other.text)
        return b in a


def truth_needles(truth: dict[str, Any]) -> list[Needle]:
    needles = [Needle(e["id"], e["value"], e["type"] in CASE_INSENSITIVE_TYPES) for e in truth["pii"]]
    needles += [Needle(e["id"], e["context"], False) for e in truth["near_misses"]]
    return needles


def segment_matches(seg: Segment, needles: list[Needle]) -> list[tuple[Needle, str, int, int]]:
    """All (needle, path, start, end) matches in *seg*, standalone only: a
    match lying inside a longer needle's match (``Samplewick`` inside ``Bram
    Samplewick``) is attributed to the longer needle."""
    norm, index = normalize_with_map(seg.text)
    found = {n.key: find_spans(norm, n.text, ignore_case=n.ignore_case) for n in needles}
    by_key = {n.key: n for n in needles}
    out = []
    for n in needles:
        for s, e in found[n.key]:
            covered = any(
                by_key[k].contains(n) and any(os <= s and e <= oe for os, oe in spans)
                for k, spans in found.items() if k != n.key and spans
            )
            if covered:
                continue
            path = f"line[{line_of(seg.text, index[s])}]" if seg.line_mapped else seg.path
            out.append((n, path, s, e))
    return out


def observed_locations(path: str | Path, truth: dict[str, Any]) -> dict[str, Counter]:
    """needle id -> Counter[(part, path)] of standalone occurrences."""
    needles = truth_needles(truth)
    observed: dict[str, Counter] = defaultdict(Counter)
    for seg in extract_segments(path):
        for n, loc_path, _, _ in segment_matches(seg, needles):
            observed[n.key][(seg.part, loc_path)] += 1
    return observed


def verify_truth(path: str | Path, truth: dict[str, Any]) -> list[str]:
    """Problems with *truth* for the document at *path* (empty list = exact).

    Checks, for every PII value and near miss, that the set of locations and
    per-location counts found by scanning the file equals the truth, and (for
    zip packages) that a raw byte-level sweep finds no value in a part the
    truth does not mention.
    """
    problems: list[str] = []
    is_raster = truth["document"]["format"] == "pdf-image"
    observed = observed_locations(path, truth)
    entries = {e["id"]: e for e in truth["pii"] + truth["near_misses"]}
    for key, entry in entries.items():
        expected = Counter({(loc["part"], loc["path"]): loc["count"] for loc in entry["locations"]
                            if not (is_raster and "bboxes" in loc)})
        got = observed.get(key, Counter())
        label = f"{key} {entry.get('type', entry.get('kind'))} {entry.get('value')!r}"
        for loc in sorted(set(expected) | set(got)):
            if expected[loc] != got[loc]:
                problems.append(f"{label}: {loc[0]}#{loc[1]} truth={expected[loc]} found={got[loc]}")

    if Path(path).suffix.lower() in (".docx", ".xlsx", ".pptx"):
        needles = truth_needles(truth)
        allowed: dict[str, set[str]] = {}
        for n in needles:
            parts = set()
            for other in needles:
                if other.key == n.key or other.contains(n):
                    parts |= {loc["part"] for loc in entries[other.key]["locations"]}
            allowed[n.key] = parts
        for where, text in raw_texts(path):
            part = where.split("#", 1)[0]
            norm = normalize(text)
            for n in needles:
                if find_spans(norm, n.text, ignore_case=n.ignore_case) and part not in allowed[n.key]:
                    problems.append(f"{n.key} {entries[n.key].get('value')!r}: raw bytes in {where} "
                                    "not covered by any truth location")
    return problems
