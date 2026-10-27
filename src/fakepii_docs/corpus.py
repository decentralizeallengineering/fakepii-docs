"""Generate documents and their ground-truth files."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from .people import make_plan
from .truth import write_truth

# format -> (module, function, file suffix)
GENERATORS = {
    "docx": ("docx_gen", "generate_docx", ".docx"),
    "xlsx": ("xlsx_gen", "generate_xlsx", ".xlsx"),
    "pptx": ("pptx_gen", "generate_pptx", ".pptx"),
    "pdf": ("pdf_gen", "generate_pdf", ".pdf"),
    "pdf-image": ("pdf_gen", "generate_pdf_image", ".pdf"),
    "csv": ("text_gen", "generate_csv", ".csv"),
    "txt": ("text_gen", "generate_txt", ".txt"),
    "md": ("text_gen", "generate_md", ".md"),
}


def document_name(fmt: str, seed: int) -> str:
    stem = f"fakepii-{seed}-{fmt.replace('-', '_')}"
    return stem + GENERATORS[fmt][2]


def truth_name(document: str) -> str:
    return document + ".truth.json"


def generate_document(fmt: str, out_dir: str | Path, seed: int = 0, n_people: int = 3) -> tuple[Path, Path]:
    """Write one document and its truth file; return both paths."""
    if fmt not in GENERATORS:
        raise ValueError(f"unknown format {fmt!r}; choose from {', '.join(GENERATORS)}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    module, func, _ = GENERATORS[fmt]
    generator = getattr(importlib.import_module(f".generators.{module}", __package__), func)
    doc_path = out_dir / document_name(fmt, seed)
    builder = generator(make_plan(seed, fmt, n_people), doc_path)
    truth_path = out_dir / truth_name(doc_path.name)
    write_truth(builder.build(doc_path), truth_path)
    return doc_path, truth_path


def generate_corpus(out_dir: str | Path, seed: int = 0, formats: tuple[str, ...] | list[str] | None = None,
                    n_people: int = 3) -> dict[str, Any]:
    """Generate every requested format; writes and returns a manifest."""
    out_dir = Path(out_dir)
    entries = []
    for fmt in formats or GENERATORS:
        doc, truth = generate_document(fmt, out_dir, seed, n_people)
        entries.append({"format": fmt, "document": doc.name, "truth": truth.name})
    manifest = {"seed": seed, "documents": entries}
    (out_dir / "manifest.json").write_bytes((json.dumps(manifest, indent=2) + "\n").encode("utf-8"))
    return manifest
