"""Bounded local Tesseract OCR for raster PDF pages."""
import os
import shutil
from collections import defaultdict
from pathlib import Path
from statistics import median

from PIL import Image
import pymupdf
import pytesseract

from .schemas import Block
from .section_classifier import heading_category


def find_tesseract() -> str | None:
    override = os.getenv("TESSERACT_CMD")
    if override:
        return shutil.which(override) or (override if Path(override).is_file() else None)
    windows_path = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    return shutil.which("tesseract") or (str(windows_path) if windows_path.is_file() else None)


def recognize(page: pymupdf.Page, language: str = "eng",
              clip: pymupdf.Rect | None = None) -> list[Block]:
    command = find_tesseract()
    if not command:
        raise RuntimeError("Tesseract is unavailable. Check TESSERACT_CMD or disable OCR.")
    pytesseract.pytesseract.tesseract_cmd = command
    if language not in pytesseract.get_languages():
        raise RuntimeError(f"Tesseract language pack '{language}' is unavailable.")
    region = (clip & page.rect) if clip else page.rect
    if region.is_empty:
        return []
    if region.get_area() * (270 / 72) ** 2 > 30_000_000:
        raise RuntimeError("Page is too large to rasterize at 270 DPI.")
    pix = page.get_pixmap(dpi=270, clip=region, colorspace=pymupdf.csRGB, alpha=False)
    with Image.frombytes("RGB", (pix.width, pix.height), pix.samples) as image:
        data = pytesseract.image_to_data(image, lang=language, timeout=60,
                                        output_type=pytesseract.Output.DICT)
    return _recover_layout(data, page.number + 1, pix.height, clip is not None)


def _recover_layout(data: dict, page: int, height: int, clipped: bool) -> list[Block]:
    """Retain OCR line geometry and recover schedules with aligned column headers."""
    grouped = defaultdict(list)
    for i, value in enumerate(data["text"]):
        if value.strip():
            group = tuple(data[key][i] for key in ("block_num", "par_num", "line_num"))
            x, y, width, h = (data[key][i] for key in ("left", "top", "width", "height"))
            grouped[group].append((x, y, width, h, value.strip()))
    if not grouped:
        return []
    word_height = median(word[3] for words in grouped.values() for word in words)
    lines = sorted(grouped.values(), key=lambda words: median(w[1] + w[3] for w in words))
    bands = []
    for words in lines:
        bottom = median(word[1] + word[3] for word in words)
        if bands and bottom - bands[-1][0] <= word_height * .45:
            bands[-1][1].extend(words)
        else:
            bands.append((bottom, list(words)))
    blocks = []
    anchors: list[int] = []
    rows: list[str] = []
    last_bottom = 0
    for bottom, words in bands:
        words.sort()
        text = " ".join(word[4] for word in words)
        groups = [[]]
        right = 0
        for word in words:
            if groups[-1] and word[0] - right > word_height * 1.55:
                groups.append([])
            groups[-1].append(word)
            right = word[0] + word[2]
        labels = [" ".join(w[4] for w in group).casefold() for group in groups]
        is_header = (len(labels) >= 2 and any(k in labels[0] for k in ("activity", "work item", "task", "phase"))
                     and any(label in {"deadline", "duration", "milestone", "submission date"} for label in labels[1:]))
        if is_header and not rows:
            anchors = [group[0][0] for group in groups]
            rows = [" | ".join(" ".join(w[4] for w in group) for group in groups)]
            last_bottom = bottom
            continue
        if rows:
            cells = [[] for _ in anchors]
            for word in words:
                column = sum(word[0] >= start - word_height * .35 for start in anchors[1:])
                cells[column].append(word[4])
            if cells[0] and any(cells[1:]) and bottom - last_bottom <= word_height * 3.6:
                rows.append(" | ".join(" ".join(cell) for cell in cells))
                last_bottom = bottom
                continue
            blocks.append(Block(page, "\n".join(rows), table=True))
            rows = []
        edge = not clipped and (bottom < .09 * height or bottom > .91 * height)
        heading = bool(heading_category(text)) or (len(text) <= 100 and
                   median(word[3] for word in words) >= word_height * 1.4)
        blocks.append(Block(page, text, heading=heading, edge=edge))
    if rows:
        blocks.append(Block(page, "\n".join(rows), table=True))
    return blocks
