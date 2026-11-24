"""Read PDF pages, recover layout and route raster content to OCR."""
import re
from statistics import median
from typing import Callable

import pymupdf

from . import ocr_engine
from .config import MAX_BYTES, MAX_PAGES
from .schemas import Block, OCRMode, Page
from .section_classifier import heading_category


def _table_text(page: pymupdf.Page, table) -> tuple[tuple[str, ...], ...]:
    """Read each detected cell using text-word coordinates to preserve word spacing."""
    words = page.get_text("words", clip=pymupdf.Rect(table.bbox), sort=True)
    rows = []
    # A styled header can be split into many phantom cells by the PDF detector.
    # Use a repeated body grid only when it is substantially simpler.
    grids = [tuple((c[0], c[2]) for c in row.cells if c) for row in table.rows]
    grid = min((g for g in grids if len(g) >= 2), key=len, default=())
    repair = bool(grid and len(grids[0]) >= len(grid) * 2 and grids.count(grid) >= 2)
    for row in table.rows:
        cells = []
        boxes = [(left, row.bbox[1], right, row.bbox[3]) for left, right in grid] if repair else row.cells
        for cell in boxes:
            region = pymupdf.Rect(cell) if cell else None
            cells.append(" ".join(word[4] for word in words if region and
                                  region.contains(pymupdf.Point((word[0] + word[2]) / 2,
                                                               (word[1] + word[3]) / 2))))
        if any(cells):
            rows.append(tuple(cells))
    return tuple(rows)


def open_pdf(data: bytes) -> pymupdf.Document:
    if not data:
        raise ValueError("The PDF upload is empty.")
    if len(data) > MAX_BYTES:
        raise ValueError("The PDF exceeds the 50 MiB limit.")
    if b"%PDF-" not in data[:1024]:
        raise ValueError("The file does not have a valid PDF header.")
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except (RuntimeError, ValueError) as exc:
        raise ValueError("The PDF is corrupted or unsupported.") from exc
    if doc.needs_pass or not 0 < doc.page_count <= MAX_PAGES:
        message = ("The PDF is password protected." if doc.needs_pass else
                   "The PDF contains no pages." if not doc.page_count else
                   f"The PDF exceeds the {MAX_PAGES}-page limit.")
        doc.close()
        raise ValueError(message)
    return doc


def _layout(page: pymupdf.Page, result: Page) -> list[pymupdf.Rect]:
    flags = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
    blocks = page.get_text("dict", flags=flags)["blocks"]
    spans = [s for b in blocks for line in b.get("lines", []) for s in line["spans"] if s["text"].strip()]
    typical = median([s["size"] for s in spans]) if spans else 11
    items = []
    tables = []
    if len(spans) >= 4:
        try:
            for table in page.find_tables().tables:
                rows = _table_text(page, table)
                if rows:
                    rect = pymupdf.Rect(table.bbox)
                    tables.append(rect)
                    text = "\n".join(" | ".join(row) for row in rows)
                    items.append((rect.y0, rect.x0, Block(result.number, text, table=True, cells=rows)))
        except (RuntimeError, ValueError, AttributeError) as exc:
            result.warnings.append(f"Table detection failed ({type(exc).__name__}). Embedded text retained.")
    for block in blocks:
        previous = None
        for line in block.get("lines", []):
            rect = pymupdf.Rect(line["bbox"])
            if any(rect.get_area() and (rect & table).get_area() / rect.get_area() > .8 for table in tables):
                previous = None
                continue
            text = "".join(s["text"] for s in line["spans"]).strip()
            if text:
                heading = len(text) <= 100 and any(s["size"] >= typical * 1.16 or
                    (s["size"] >= typical and s["flags"] & 16) for s in line["spans"])
                edge = rect.y0 < page.rect.height * .09 or rect.y1 > page.rect.height * .91
                heading = heading or bool(heading_category(text))
                if previous and not heading and not edge and not re.match(r"^(?:[-•–]|\d+[.)])\s", text):
                    prior_rect, (y, x, prior) = previous, items[-1]
                    if (not prior.heading and not prior.edge and abs(rect.x0 - prior_rect.x0) < 4
                            and -3 <= rect.y0 - prior_rect.y1 <= 5
                            and not prior.text.endswith((".", ":", "!", "?"))):
                        items[-1] = (y, x, Block(result.number, prior.text + " " + text))
                        previous = rect
                        continue
                items.append((rect.y0, rect.x0, Block(result.number, text, heading, edge=edge)))
                previous = rect
    result.blocks = [item[2] for item in sorted(items, key=lambda x: (round(x[0], 1), x[1]))]
    return [pymupdf.Rect(item["bbox"]) for item in page.get_image_info()
            if pymupdf.Rect(item["bbox"]).get_area() >= page.rect.get_area() * .15]


def extract_pdf(data: bytes, mode: OCRMode = "Auto", language: str = "eng",
                progress: Callable[[int, int], None] | None = None) -> list[Page]:
    """Return one result for every original page, including blank pages."""
    if mode not in {"Auto", "Force", "Off"}:
        raise ValueError("Unknown OCR mode.")
    pages = []
    with open_pdf(data) as doc:
        for page in doc:
            result = Page(page.number + 1)
            try:
                images = _layout(page, result)
            except (RuntimeError, ValueError) as exc:
                result.warnings.append(f"Embedded extraction failed ({type(exc).__name__}).")
                images = []
            if mode == "Force" or (mode == "Auto" and (images or len(re.findall(r"\w", result.text)) < 24)):
                result.ocr_attempted = True
                clips = images if images and mode == "Auto" and len(result.text) >= 24 and not page.rotation else [None]
                seen = {" ".join(line.casefold().split()) for line in result.text.splitlines()}
                for clip in clips:
                    try:
                        recovered = ocr_engine.recognize(page, language, clip)
                    except (RuntimeError, OSError, ValueError, pymupdf.FileDataError) as exc:
                        result.warnings.append(f"OCR failed: {str(exc)[:350]}")
                        break
                    if recovered:
                        result.ocr_used = True
                    for block in recovered:
                        key = " ".join(block.text.casefold().split())
                        if key and key not in seen:
                            result.blocks.append(block)
                            seen.add(key)
                if not result.ocr_used and not result.warnings:
                    result.warnings.append("OCR returned no text.")
            if not result.text.strip():
                result.warnings.append("No readable content on this page.")
            pages.append(result)
            if progress:
                progress(len(pages), doc.page_count)
    return pages
