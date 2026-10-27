"""Helpers shared by the generators."""

from __future__ import annotations

import io
import re
import struct
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

FIXED_TIME = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
FIXED_ISO = FIXED_TIME.strftime("%Y-%m-%dT%H:%M:%SZ")
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def png_bytes(width: int = 48, height: int = 48) -> bytes:
    """A small grey 'portrait silhouette' PNG, written with zlib only."""
    rows = []
    cx, cy = width / 2, height * 0.38
    for y in range(height):
        row = bytearray([0])  # filter type 0
        for x in range(width):
            head = (x - cx) ** 2 + (y - cy) ** 2 < (width * 0.2) ** 2
            body = y > height * 0.62 and abs(x - cx) < width * 0.35
            row.append(90 if head or body else 225)
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


def _normalized(blob: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        members = [(info, zf.read(info)) for info in zf.infolist()]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for info, data in members:
            if data.startswith(b"PK\x03\x04"):
                # e.g. the workbook python-pptx embeds behind every chart
                data = _normalized(data)
            elif info.filename == "docProps/core.xml":
                text = data.decode("utf-8")
                text = re.sub(r"(<dcterms:(?:created|modified)[^>]*>)[^<]*(<)", rf"\g<1>{FIXED_ISO}\g<2>", text)
                data = text.encode("utf-8")
            new = zipfile.ZipInfo(info.filename, date_time=_ZIP_TIME)
            new.compress_type = zipfile.ZIP_DEFLATED
            new.external_attr = 0o644 << 16
            zf.writestr(new, data)
    return buf.getvalue()


def normalize_zip(path: str | Path) -> None:
    """Rewrite an OOXML package so that the same content gives the same bytes:
    fixed member timestamps and core-properties dates, recursively for
    embedded packages."""
    path = Path(path)
    path.write_bytes(_normalized(path.read_bytes()))
