# fakepii-docs

A synthetic test corpus for personal-data masking. It generates office
documents full of **fake** personal data, puts that data in the places
maskers usually miss, and writes exact ground truth for every value. A scorer
then reports what a masker leaked, what it over-masked, and whether an
unmasker restored the original text byte for byte.

- Formats: `docx`, `xlsx`, `pptx`, `pdf` (text layer), `pdf-image` (rendered
  text, no text layer), `csv`, `txt`, `md`.
- Seeded and reproducible: the same seed and library versions give the same
  bytes.
- Python 3.12+, Linux and Windows. No network access at runtime.
- Apache-2.0.

## Fake data only

| Kind | Rule |
|---|---|
| Names | Built-in list. First names are whimsical; surnames are obviously invented (`Example`, `Samplewick`, `Notrealis`, ...). |
| E-mail | Ends in `example.invalid` or `example.com` (RFC 2606 / 6761). |
| Phone | NANP fictional block `555-0100`–`555-0199`, e.g. `(310) 555-0147`, `+1 310 555 0147`. |
| SSN-shaped ID | Starts with `9xx` or `000`. The SSA never issues these. |
| Address | Invented streets (`Make Believe Lane`, `Nowhere Street`, ...), invented cities, state `ZZ`, ZIP `000xx`. |

The tests check every generated value against these rules. They also check
that no pool name appears in the blank docx/pptx/xlsx templates. (`Placeholder`
was dropped as a surname because Office templates use that word.)

## Install

```bash
pip install -e ".[test]"        # or: pip install .
```

Runtime dependencies are `openpyxl`, `python-docx`, `python-pptx`, `pypdf`,
`pdfplumber`, `fpdf2`, `numpy` and `jsonschema`. All of them are on the
allowed list. `docxcompose` and `pandas` are allowed too, but nothing here
needs them, so they are not installed. Those libraries pull in their own
dependencies (Pillow, lxml, pdfminer.six, pypdfium2, XlsxWriter). This code
does not import any of those directly.

## Command line

```bash
fakepii-docs generate OUT_DIR [--seed 42] [--formats docx,xlsx,...] [--people 3]
fakepii-docs verify   DOC TRUTH.json                 # scan back: is the truth exact and complete?
fakepii-docs score    ORIGINAL MASKED TRUTH.json [--unmasked FILE] [--json]
fakepii-docs schema                                  # print the truth JSON Schema
```

`generate` writes `fakepii-<seed>-<format>.<ext>`, one
`<document>.truth.json` per document and a `manifest.json`.

`score` exits `0` on PASS and `1` on FAIL. Usage errors exit `2`.

The report below is real output. It scores a masking that has three faults:
it put the author back into the document properties, it masked the place
name `Inventedale Township`, and its unmasker restored one phone digit
wrongly.

```
fakepii-docs score: fakepii-42-docx.docx (docx)
LEAKS: 2 of 22 PII values still present (2 structured occurrences)
  pii-003 first_name 'Ann'
      docProps/core.xml#core/creator
      raw sweep: docProps/core.xml
  pii-005 person_name 'Ann Inventedale'
      docProps/core.xml#core/creator
      raw sweep: docProps/core.xml
OVER-MASKING: 1 of 3 near misses altered
  nm-001 place_named_like_surname 'Inventedale Township': 1 -> 0
      word/document.xml#body/p[14]
ROUND-TRIP: 28/29 visible segments restored byte-for-byte; 0 hidden segment(s) differ
  word/document.xml#body/p[4]: first difference at char 86: expected '9.' got '0.'
RESULT: FAIL
```

## Python API

```python
from fakepii_docs import generate_corpus, generate_document, score, load_truth
from fakepii_docs.scan import verify_truth
from fakepii_docs.oracle import oracle_mask

manifest = generate_corpus("out", seed=42)                 # all formats
doc, truth = generate_document("xlsx", "out", seed=7)
assert verify_truth(doc, load_truth(truth)) == []
report = score(doc, "masked.xlsx", truth, unmasked="restored.xlsx")
report["passed"], report["leaks"], report["over_masking"], report["round_trip"]
```

## Where the PII is hidden

Every location records its techniques in the truth file.

| Format | Hard places |
|---|---|
| docx | name split over runs with bold/italic/underline/colour; SSN split over runs; glued `AnnExample2024`; `(ann@example.invalid),`; header; footer; comment text **and comment author**; footnote; table with horizontally and vertically merged cells; mailto hyperlink (text and `.rels` target); picture alt text (`descr`, `title`); legacy FORMTEXT field (value and `default`); content control; tracked deletion (`w:delText`) and revision authors; zero-width space inside a name; NBSP between names; core metadata (author, title, subject, keywords, description, last modified by) |
| xlsx | merged cell; `_xlfn.CONCAT(A4," ",B4)`; `CONCATENATE("Ve","sper Exa","mple")` (the name exists only when computed); `="First"&" "&"Last"`; cross-sheet `=Roster!A5&" "&Roster!B5`; rich-text runs in one cell; a **sheet named after a person**; cell comment and its author; mailto hyperlinks; print header/footer; chart title; workbook metadata |
| pptx | subtitle split over runs; speaker notes; table with merged cells; chart title; picture alt text; run hyperlink (text and target); text in a group shape; glued tokens; NBSP; metadata |
| pdf | running header and footer on every page; name split over font styles; glued tokens; table with spanning cells; link annotation URI; sticky-note comment (contents and author); AcroForm field value; image alt text (structure tree); bookmark; Info and XMP metadata |
| pdf-image | the same visible layout, rasterised at 150 dpi, with no text layer; Info metadata; one bounding box per value |
| csv | quoted fields with commas, doubled quotes, newline inside a quoted name, glued tokens |
| txt | name wrapped across a line break, zero-width space, tab-separated rows, `<email>`, `(phone),` |
| md | YAML front matter; `**A**nn *Example*`; link text vs `mailto:` target; image alt and file name; HTML comment; `&nbsp;`; inline code; table; soft line break inside a name; footnote |

**Near misses** must *not* be masked. Every document has all three, and each
carries a `label` in the truth file:

- `place_named_like_surname`: `"<Surname> Township"`, where the surname is a
  real PII surname in the same document.
- `product_code_phone_shape`: `SKU 310-555-0155`.
- `reference_number_ssn_shape`: `Order #000-55-5319`.

## Ground truth

There is one JSON file per document, validated against
`src/fakepii_docs/schema/truth.schema.json`. Below is an abridged excerpt
from the seed-42 docx truth. The real entry for this name has five
locations: header, split runs, comment, tracked deletion and metadata.

```json
{
  "document": {"file": "fakepii-42-docx.docx", "format": "docx", "sha256": "..."},
  "entities": [{"id": "person-1", "kind": "person", "display": "Ann Inventedale"}],
  "pii": [
    {"id": "pii-005", "type": "person_name", "value": "Ann Inventedale", "entity": "person-1",
     "locations": [
       {"part": "word/document.xml", "path": "body/p[3]", "count": 1, "visible": true,
        "techniques": ["split_runs", "formatting"]},
       {"part": "docProps/core.xml", "path": "core/creator", "count": 1, "visible": false,
        "techniques": ["metadata"]}
     ]}
  ],
  "near_misses": [
    {"id": "nm-001", "kind": "place_named_like_surname", "value": "Inventedale",
     "context": "Inventedale Township", "label": "...", "locations": [...]}
  ]
}
```

- **Types:** `person_name`, `first_name`, `last_name`, `email`, `phone`,
  `national_id`, `street_address`, `postal_address`, `date_of_birth`,
  `username`.
- **`part` / `path`:** follow the location grammar documented at the top of
  `extract.py`. Some examples: `xl/workbook.xml` `sheet[2]@name`;
  `xl/worksheets/sheet1.xml` `sheet[1]!C8` (a computed formula);
  `ppt/notesSlides/notesSlide1.xml` `shape[2]/p[1]`; `page[1]` `annot[2]/T`;
  `""` `line[12]` for txt/md (the line where the match starts).
- **`count`:** the number of standalone occurrences at that location. An
  occurrence inside a longer recorded value belongs to the longer value:
  `Inventedale` inside `Ann Inventedale`, or inside `Inventedale Township`.
- **`bboxes`** (pdf-image only): `[x0, top, x1, bottom]` in PDF points, with
  the origin at the top left.
- **Matching rules:** whitespace runs (including NBSP, tab and newline)
  compare as one space. Zero-width characters and soft hyphens are ignored.
  Matching is case-sensitive, except for e-mail addresses.

### How truth completeness is checked

The generators record each location *at the moment they place the value*.
`verify_truth` then re-reads the file with an independent extractor (raw XML
through `xml.etree`, `pypdf` and `pdfplumber`; none of the writer libraries).
Per value, the set of locations and counts it finds must equal the truth. For
zip packages, a raw byte sweep of every member must also find no value in a
part the truth does not name. The test suite runs this for every format and
several seeds, and shows that it catches a dropped location, a wrong count
and an untracked value. Two real mistakes surfaced this way during
development: `Placeholder` occurring inside Office templates, and openpyxl
writing inline strings rather than shared strings.

## Scorer semantics

- **Leaks.** Every PII value still present in the masked file is a leak. The
  scorer searches two views:
  - the structured view: everything above, including metadata, alt text,
    link targets, comment authors, tracked deletions and computed formula
    results;
  - a raw sweep of every zip member, embedded packages included, with tags
    stripped so split runs join up again. For PDFs it also covers every
    object string and every stream in the file, reachable or not, plus
    UTF-16 views.

  Near-miss contexts are blanked out before searching. That way, keeping
  `Inventedale Township` is not a leak of the surname `Inventedale`.
- **Over-masking.** A near miss is over-masked when its context occurs fewer
  times in the masked file than in the original. The report lists the
  locations where it disappeared.
- **Round trip** (`--unmasked`). Every visible segment of the original must
  be present at the same location in the unmasked file, byte for byte. Hidden
  segments (metadata, alt text, link targets, ...) are compared too, but
  listed separately. They do not fail the round trip on their own.
- **pdf-image.** The scorer has no OCR. Instead it renders both files at 100
  dpi and compares pixels inside each truth bounding box:
  - A PII box counts as *changed* when more than 10% of its pixels differ by
    more than 48 grey levels. An unchanged PII box is a leak.
  - A changed near-miss box is over-masking.
  - For the round trip, each page may have at most 0.05% differing pixels,
    and no box may change at all.
  - This requires the masked file to keep page count and page size. If it
    does not, the boxes are reported as *unverified* and the score fails.
  - If the masked PDF has a text layer (an OCR-ing masker), that text is
    searched as well.

## Oracle masker

`fakepii_docs.oracle.oracle_mask(doc, truth, out, leave=(), mask_near_misses=False)`
masks a generated document **using its truth**. Every masked character
becomes `X`. It is a test fixture, not a masker. The test suite uses it for
three things:

- To show the scorer has no false positives: a full oracle mask scores PASS
  in every format.
- To build hand-made leaks: leave one value, and exactly that value is
  reported. This runs for every value in every format.
- To build over-masking: mask the near misses, and all three are reported.

Other hand-made leaks in the tests:

- an upper-cased e-mail appended to a masked txt;
- an author name written back into `docProps/core.xml`;
- a name re-inserted across three formatted runs.

## Limitations

- The formula evaluator covers string literals, cell and cross-sheet
  references, `&`, `CONCAT` and `CONCATENATE`. Other formulas are not
  evaluated; their source text is still scanned.
- The Markdown renderer handles the inline subset that matters for matching:
  emphasis, links, images, autolinks, entities, escapes, front matter and
  HTML comments. It is not a full CommonMark renderer.
- Names are matched case-sensitively, so an upper-cased `ANN EXAMPLE` is not
  counted as a leak. E-mail addresses are matched case-insensitively.
- Reproducibility is per environment. Different zlib or pdfium builds can
  change bytes. The truth content (values, locations, bounding boxes) does
  not depend on them.

## Tests

```bash
python -m pytest -q
```

The suite does the following:

- generates every format with a fixed seed and checks byte reproducibility
  and the fake-only rules;
- scans every document back to check its truth is exact and complete;
- exercises the scorer on perfect masks, hand-made leaks (body, metadata and
  split runs), over-masking, and round trips that are exact, off by one byte,
  or changed only in metadata;
- exercises the CLI.

CI runs it on Ubuntu and Windows with Python 3.12
(`.github/workflows/tests.yml`).

## License

Apache-2.0. See `LICENSE`.
