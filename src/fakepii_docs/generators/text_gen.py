"""CSV, TXT and Markdown generators.

CSV: quoted fields with commas, doubled quotes, an embedded newline inside a
name, glued tokens. TXT: a name wrapped across a line break, zero-width space
inside a name, tab-separated rows, glued and parenthesised tokens. Markdown:
YAML front matter, a name split by emphasis markers, link text vs mailto
target, image alt text and file name, an HTML comment, ``&nbsp;`` entity,
inline code, a table, a soft line break inside a name, a footnote.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from ..people import Plan, split_points
from ..truth import TruthBuilder

NEAR_KINDS = ("place_named_like_surname", "product_code_phone_shape", "reference_number_ssn_shape")


class _Lines:
    """Accumulates text lines; ``add`` returns the 1-based number of the first."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def add(self, text: str = "") -> int:
        start = len(self.lines) + 1
        self.lines.extend(text.split("\n"))
        return start

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def generate_csv(plan: Plan, out_path: str | Path) -> TruthBuilder:
    a, b, c = plan.people[:3]
    town, sku, order = (plan.near(k) for k in NEAR_KINDS)
    tb = TruthBuilder("csv", plan.seed, plan.people)
    header = ["id", "full_name", "email", "phone", "ssn", "address", "username", "notes", "site", "reference"]
    notes = {
        a.entity: (f'Prefers "{a.first}"; alt contact ({b.email_alt}),',
                   [(a.first, "first_name", a, ["quoted", "doubled_quotes"]),
                    (b.email_alt, "email", b, ["parentheses", "trailing_punctuation"])]),
        b.entity: (f"Emergency: {c.first}\n{c.last}",
                   [(c.full, "person_name", c, ["embedded_newline"])]),
        c.entity: (f"Badge {c.username}/{c.phone_alt}",
                   [(c.username, "username", c, ["glued"]), (c.phone_alt, "phone", c, ["glued"])]),
    }
    site = {a.entity: (town.context, town), b.entity: ("North depot", None), c.entity: ("North depot", None)}
    ref = {a.entity: (sku.context, sku), b.entity: (order.context, order), c.entity: ("", None)}
    rows = [header]
    for r, p in enumerate((a, b, c), 2):
        note_text, note_plants = notes[p.entity]
        rows.append([f"E{r - 1:03d}", p.full, p.email, p.phone, p.ssn, p.address, p.username,
                     note_text, site[p.entity][0], ref[p.entity][0]])
        for col, (value, type_) in enumerate(((p.full, "person_name"), (p.email, "email"), (p.phone, "phone"),
                                              (p.ssn, "national_id"), (p.address, "postal_address"),
                                              (p.username, "username")), 2):
            tb.pii(value, type_, p, "", f"row[{r}]/col[{col}]",
                   techniques=["quoted_comma"] if type_ == "postal_address" else [])
        for value, type_, person, techniques in note_plants:
            tb.pii(value, type_, person, "", f"row[{r}]/col[8]", techniques=techniques)
        for col, (_, nm) in ((9, site[p.entity]), (10, ref[p.entity])):
            if nm is not None:
                tb.near(nm, "", f"row[{r}]/col[{col}]", techniques=["near_miss"])
    buf = io.StringIO(newline="")
    csv.writer(buf, lineterminator="\r\n").writerows(rows)
    Path(out_path).write_bytes(buf.getvalue().encode("utf-8"))
    return tb


def generate_txt(plan: Plan, out_path: str | Path) -> TruthBuilder:
    a, b, c = plan.people[:3]
    town, sku, order = (plan.near(k) for k in NEAR_KINDS)
    rng = plan.rng
    tb = TruthBuilder("txt", plan.seed, plan.people)
    doc = _Lines()

    def pii(line: int, value: str, type_: str, person, *techniques: str) -> None:
        tb.pii(value, type_, person, "", f"line[{line}]", techniques=techniques)

    doc.add("MEMO - synthetic test data")
    n = doc.add(f"From: {a.full} <{a.email}>")
    pii(n, a.full, "person_name", a)
    pii(n, a.email, "email", a, "angle_brackets")
    n = doc.add(f"To: {b.first} {b.last}; cc {c.email_alt}")
    pii(n, b.full, "person_name", b)
    pii(n, c.email_alt, "email", c)
    doc.add("Date: 2024-01-02")
    doc.add()
    doc.add("Shift swaps must be logged in the shared tracker by the end of day.")
    doc.add()
    n = doc.add(f"Please update the badge record for {c.first}\n{c.last} before Friday. Login: {c.username}.")
    pii(n, c.full, "person_name", c, "line_wrap")
    pii(n + 1, c.username, "username", c, "glued", "trailing_punctuation")
    n = doc.add(f"Reach me at {a.phone} or ({a.phone_alt}),")
    pii(n, a.phone, "phone", a)
    pii(n, a.phone_alt, "phone", a, "parentheses", "trailing_punctuation")
    n = doc.add(f"SSN on the form: {b.ssn}; DOB {b.dob}.")
    pii(n, b.ssn, "national_id", b)
    pii(n, b.dob, "date_of_birth", b)
    k = rng.randrange(1, len(b.first))
    n = doc.add(f"Countersigned: {b.first[:k]}​{b.first[k:]} {b.last}")
    pii(n, b.full, "person_name", b, "zero_width_space")
    doc.add()
    doc.add("Name\tPhone\tAddress")
    for p in (a, c):
        n = doc.add(f"{p.full}\t{p.phone}\t{p.address}")
        pii(n, p.full, "person_name", p, "tab_separated")
        pii(n, p.phone, "phone", p, "tab_separated")
        pii(n, p.address, "postal_address", p, "tab_separated")
    doc.add()
    n = doc.add(f"Dear {c.first}, the depot moved to {town.context}.")
    pii(n, c.first, "first_name", c)
    tb.near(town, "", f"line[{n}]", techniques=["near_miss"])
    n = doc.add(f"Reorder {sku.context}; chase {order.context}.")
    tb.near(sku, "", f"line[{n}]", techniques=["near_miss"])
    tb.near(order, "", f"line[{n}]", techniques=["near_miss"])
    doc.add("This document is synthetic test data generated for masking evaluation.")
    Path(out_path).write_bytes(doc.text().encode("utf-8"))
    return tb


def generate_md(plan: Plan, out_path: str | Path) -> TruthBuilder:
    a, b, c = plan.people[:3]
    town, sku, order = (plan.near(k) for k in NEAR_KINDS)
    rng = plan.rng
    tb = TruthBuilder("md", plan.seed, plan.people)
    doc = _Lines()

    def pii(path: str, value: str, type_: str, person, *techniques: str, visible: bool = True) -> None:
        tb.pii(value, type_, person, "", path, visible=visible, techniques=techniques)

    doc.add(f'---\ntitle: "Onboarding notes for {b.full}"\nauthor: {a.full}\n---')
    pii("front_matter/title", b.full, "person_name", b, "front_matter", visible=False)
    pii("front_matter/author", a.full, "person_name", a, "front_matter", visible=False)
    doc.add("# Onboarding notes")
    doc.add()
    p1, p2, p3 = split_points(a.full, rng, 3)
    n = doc.add(f"Prepared by **{p1}**{p2}*{p3}*.")
    pii(f"line[{n}]", a.full, "person_name", a, "split_emphasis")
    doc.add()
    n = doc.add(f"Contact [{c.email}](mailto:{c.email}) or call {c.phone}.")
    pii(f"line[{n}]", c.email, "email", c, "hyperlink_text")
    pii(f"line[{n}]/link[1]@target", c.email, "email", c, "hyperlink_target", visible=False)
    pii(f"line[{n}]", c.phone, "phone", c)
    doc.add()
    n = doc.add(f"Backup: {b.first}&nbsp;{b.last} (`{b.username}`).")
    pii(f"line[{n}]", b.full, "person_name", b, "html_entity_nbsp")
    pii(f"line[{n}]", b.username, "username", b, "inline_code", "parentheses")
    doc.add()
    n = doc.add(f"Badge photo: ![Headshot of {a.full}]({a.username}.png)")
    pii(f"line[{n}]/image[1]@alt", a.full, "person_name", a, "alt_text", visible=False)
    pii(f"line[{n}]/image[1]@src", a.username, "username", a, "image_filename", visible=False)
    doc.add()
    n = doc.add(f"<!-- reviewer: {c.full} -->")
    pii(f"line[{n}]/comment", c.full, "person_name", c, "html_comment", visible=False)
    doc.add()
    doc.add("| Name | Email | SSN |\n|---|---|---|")
    n = doc.add(f"| {a.full} | {a.email_alt} | {a.ssn} |")
    pii(f"line[{n}]", a.full, "person_name", a, "table")
    pii(f"line[{n}]", a.email_alt, "email", a, "table")
    pii(f"line[{n}]", a.ssn, "national_id", a, "table")
    doc.add()
    n = doc.add(f"Mailing address for {c.first}\n{c.last}: {c.address}.")
    pii(f"line[{n}]", c.full, "person_name", c, "soft_line_break")
    pii(f"line[{n + 1}]", c.address, "postal_address", c)
    doc.add()
    doc.add("Home address on file[^1].")
    doc.add()
    n = doc.add(f"[^1]: {a.address}; DOB {a.dob}.")
    pii(f"line[{n}]", a.address, "postal_address", a, "footnote")
    pii(f"line[{n}]", a.dob, "date_of_birth", a, "footnote")
    doc.add()
    n = doc.add(f"The depot moved to {town.context}; reorder {sku.context} and chase {order.context}.")
    for nm in (town, sku, order):
        tb.near(nm, "", f"line[{n}]", techniques=["near_miss"])
    doc.add()
    doc.add("This document is synthetic test data generated for masking evaluation.")
    Path(out_path).write_bytes(doc.text().encode("utf-8"))
    return tb
