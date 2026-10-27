"""String matching rules shared by the truth checker and the scorer.

A value "occurs" in a piece of text when it is found after normalisation:

* zero-width characters (ZWSP, ZWNJ, ZWJ, WORD JOINER, BOM, soft hyphen) are
  removed, because they are invisible to a reader;
* every run of whitespace (space, tab, newline, NBSP, ...) becomes one space,
  so a name wrapped across a line break or joined with NBSP still matches;
* matching is case-sensitive, except for e-mail addresses.
"""

from __future__ import annotations

ZERO_WIDTH = frozenset("​‌‍⁠﻿­")

CASE_INSENSITIVE_TYPES = frozenset({"email"})


def normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Return the normalised text and, per output char, its index in *text*."""
    out: list[str] = []
    index: list[int] = []
    prev_space = False
    for i, ch in enumerate(text):
        if ch in ZERO_WIDTH:
            continue
        if ch.isspace():
            if prev_space:
                continue
            out.append(" ")
            prev_space = True
        else:
            out.append(ch)
            prev_space = False
        index.append(i)
    return "".join(out), index


def normalize(text: str) -> str:
    return normalize_with_map(text)[0]


def find_spans(haystack: str, needle: str, *, ignore_case: bool = False) -> list[tuple[int, int]]:
    """Non-overlapping spans of *needle* in *haystack* (both already normalised)."""
    if not needle:
        return []
    if ignore_case:
        haystack, needle = haystack.lower(), needle.lower()
    spans = []
    start = haystack.find(needle)
    while start != -1:
        spans.append((start, start + len(needle)))
        start = haystack.find(needle, start + len(needle))
    return spans


def blank_spans(text: str, spans: list[tuple[int, int]]) -> str:
    """Replace each span with NULs of the same length (offsets stay valid)."""
    chars = list(text)
    for s, e in spans:
        chars[s:e] = "\0" * (e - s)
    return "".join(chars)


def line_of(text: str, offset: int) -> int:
    """1-based line number of *offset* in *text* (``\\n`` separated)."""
    return text.count("\n", 0, offset) + 1
