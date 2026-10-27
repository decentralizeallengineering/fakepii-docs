# Build brief: fakepii-docs

Build a Python library and CLI "fakepii-docs": generates synthetic office documents full of FAKE personal data, with exact ground truth, for testing a masking and unmasking system. Common rules: Python 3.12, works on Windows and Linux. Allowed dependencies only: openpyxl, python-docx, docxcompose, python-pptx, pypdf, pdfplumber, fpdf2, pandas, numpy, jsonschema. No network. Pytest tests. Apache-2.0.

Fake data only: emails end in example.invalid or example.com, phone numbers use 555-01xx, ID numbers use invalid ranges (for example a US SSN shape starting with 9xx or 000), addresses use reserved or obviously fictional streets, and names come from a built-in list of clearly fictional names. Seeded and reproducible.

Generate docx, xlsx, pptx, pdf (text layer), pdf (image only, rendered text), csv, txt and md. Put PII in hard places:
- split across runs and formatting;
- glued to other tokens ("AnnExample2024", "ann@example.invalid," inside parentheses);
- headers and footers, comments, footnotes, tables, merged cells, formulas that build a name with CONCAT, sheet names, chart titles, speaker notes, form field values, metadata (author, title), hyperlinks, alt text;
- near misses that must NOT be masked (a city named like a surname, a product code shaped like a phone number), each labeled.

Ground truth: one JSON file per document listing every PII value, its type and every location.
A scorer: `fakepii-docs score <original> <masked> <truth.json>` reports leaks (true values still present anywhere, including metadata), over-masking of near misses, and round-trip fidelity when given an unmasked file (every byte of visible text restored).

Tests: generate every format with a fixed seed, check the truth files are complete by scanning the documents back, and check the scorer on a hand-made leak.
