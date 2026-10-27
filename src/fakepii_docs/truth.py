"""Ground-truth recording, serialisation and validation."""

from __future__ import annotations

import hashlib
import json
from importlib import resources
from pathlib import Path
from typing import Any, Iterable

import jsonschema

from . import __version__
from .people import NearMiss, Person
from .textmatch import CASE_INSENSITIVE_TYPES, ZERO_WIDTH

SCHEMA_VERSION = "1.0"


def load_schema() -> dict[str, Any]:
    text = resources.files("fakepii_docs").joinpath("schema/truth.schema.json").read_text("utf-8")
    return json.loads(text)


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


class TruthBuilder:
    """Collects planted values while a generator writes a document.

    Generators call :meth:`pii` / :meth:`near` at the moment they place a
    value, naming the part and path where they put it. Repeated calls for the
    same (value, part, path) increase that location's ``count``.
    """

    def __init__(self, fmt: str, seed: int, people: Iterable[Person]):
        self.fmt = fmt
        self.seed = seed
        self.people = list(people)
        self._pii: dict[tuple[str, str], dict[str, Any]] = {}
        self._near: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _add_location(entry: dict[str, Any], part: str, path: str, visible: bool,
                      techniques: Iterable[str], bboxes: list[list[float]] | None) -> None:
        for loc in entry["locations"]:
            if loc["part"] == part and loc["path"] == path:
                loc["count"] += 1
                for t in techniques:
                    if t not in loc["techniques"]:
                        loc["techniques"].append(t)
                if bboxes:
                    loc.setdefault("bboxes", []).extend(bboxes)
                return
        loc = {"part": part, "path": path, "count": 1, "visible": visible,
               "techniques": list(dict.fromkeys(techniques))}
        if bboxes:
            loc["bboxes"] = list(bboxes)
        entry["locations"].append(loc)

    def pii(self, value: str, type_: str, person: Person, part: str, path: str, *,
            visible: bool = True, techniques: Iterable[str] = (),
            bboxes: list[list[float]] | None = None) -> None:
        key = (value, type_)
        entry = self._pii.setdefault(key, {"type": type_, "value": value,
                                           "entity": person.entity, "locations": []})
        if entry["entity"] != person.entity:
            raise ValueError(f"value {value!r} planted for two entities")
        self._add_location(entry, part, path, visible, techniques, bboxes)

    def near(self, nm: NearMiss, part: str, path: str, *, visible: bool = True,
             techniques: Iterable[str] = (), bboxes: list[list[float]] | None = None) -> None:
        entry = self._near.setdefault(nm.context, {"kind": nm.kind, "value": nm.value,
                                                   "context": nm.context, "label": nm.label,
                                                   "locations": []})
        self._add_location(entry, part, path, visible, techniques, bboxes)

    def build(self, document_path: str | Path) -> dict[str, Any]:
        document_path = Path(document_path)
        pii = []
        for i, entry in enumerate(sorted(self._pii.values(),
                                         key=lambda e: (e["entity"], e["type"], e["value"])), 1):
            pii.append({"id": f"pii-{i:03d}", **entry})
        near = [{"id": f"nm-{i:03d}", **e} for i, e in enumerate(self._near.values(), 1)]
        truth = {
            "schema_version": SCHEMA_VERSION,
            "generator": {"name": "fakepii-docs", "version": __version__},
            "seed": self.seed,
            "document": {"file": document_path.name, "format": self.fmt,
                         "sha256": sha256_file(document_path)},
            "matching": {
                "whitespace": "runs of whitespace (incl. NBSP, tab, newline) compare as one space",
                "zero_width": "removed before matching: " + " ".join(f"U+{ord(c):04X}" for c in sorted(ZERO_WIDTH)),
                "case_insensitive_types": sorted(CASE_INSENSITIVE_TYPES),
            },
            "entities": [{"id": p.entity, "kind": "person", "display": p.full} for p in self.people],
            "pii": pii,
            "near_misses": near,
        }
        validate(truth)
        return truth


def validate(truth: dict[str, Any]) -> None:
    jsonschema.validate(truth, load_schema())


def write_truth(truth: dict[str, Any], path: str | Path) -> None:
    Path(path).write_bytes((json.dumps(truth, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def load_truth(path: str | Path) -> dict[str, Any]:
    truth = json.loads(Path(path).read_text("utf-8"))
    validate(truth)
    return truth
