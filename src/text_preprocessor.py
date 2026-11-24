"""Normalize layout text without losing source provenance."""
from collections import defaultdict
from dataclasses import replace
import re
import unicodedata

from .schemas import Block, Page
from .section_classifier import heading_category


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).replace("\u00ad", "").split())


def preprocess(pages: list[Page]) -> list[Block]:
    edges: dict[str, set[int]] = defaultdict(set)
    for page in pages:
        for block in page.blocks:
            if block.edge and not block.table and not heading_category(block.text):
                key = normalize(block.text)
                if re.fullmatch(r"page\s+\d+(?:\s+of\s+\d+)?", key, re.I):
                    key = "page #"
                edges[key].add(page.number)

    output = []
    for page in pages:
        seen = set()
        for block in page.blocks:
            value = "\n".join(normalize(line) for line in block.text.splitlines()) if block.table else normalize(block.text)
            edge_key = "page #" if re.fullmatch(r"page\s+\d+(?:\s+of\s+\d+)?", value, re.I) else value
            repeated_edge = block.edge and len(edges[edge_key]) >= max(2, (len(pages) + 1) // 2)
            if not value or edge_key == "page #":
                continue
            if repeated_edge and not block.heading and not block.table:
                output.append(replace(block, text=value, kind="boilerplate"))
                continue
            if (value, block.table) not in seen:
                output.append(replace(block, text=value))
                seen.add((value, block.table))
            if block.heading or heading_category(value):
                seen.clear()
    return output
