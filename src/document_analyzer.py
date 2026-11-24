"""Run extraction, evidence checks, consolidation and report preparation."""
from collections import Counter
from dataclasses import replace
import re
from typing import Callable

from .config import MISSING
from .evidence_validator import DURATION, DUE, INJECTION, contains, verify
from .llm_client import OllamaClient, OllamaError
from .pdf_extractor import extract_pdf
from .schemas import Block, Fact, OCRMode, Page, Summary
from .schedule import build_schedule, date_value, table_cells
from .section_classifier import classify
from .text_preprocessor import normalize, preprocess

Progress = Callable[[float, str], None]
LIMIT = 1700
UNSPECIFIED = "Not specified"
BULLET = re.compile(r"^(?:[•●▪–-]\s+|\d+[.)]\s+|[a-z]\)\s+)", re.I)


def _pieces(block: Block) -> list[Block]:
    prefix = BULLET.match(block.text) if not block.table else None
    if block.table:
        units = block.text.splitlines()
    else:
        body = block.text[prefix.end():] if prefix else block.text
        units = re.split(r"(?<=[.!?])\s+(?=[A-Z])", body)
        if prefix:
            units[0] = prefix[0] + units[0]
    output = []
    for unit in units:
        remaining = unit.strip()
        while remaining:
            end = len(remaining)
            if end > LIMIT:
                end = remaining.rfind(" ", 0, LIMIT + 1)
                end = end if end > 0 else LIMIT
            output.append(replace(block, text=remaining[:end], cells=()))
            remaining = remaining[end:].lstrip()
    return output


def chunk_blocks(blocks: list[Block]) -> list[list[Block]]:
    chunks = []
    chunk = []
    size = 0
    for block in blocks:
        if block.kind != "project" or block.heading or INJECTION.search(block.text):
            continue
        for piece in _pieces(block):
            cost = len(piece.text) + 90
            if chunk and size + cost > LIMIT + 100:
                chunks.append(chunk)
                last = chunk[-1]
                overlap = len(last.text) + 90
                chunk = [last] if overlap <= 300 and overlap + cost <= LIMIT + 100 else []
                size = overlap if chunk else 0
            chunk.append(piece)
            size += cost
    if chunk:
        chunks.append(chunk)
    return chunks


def _fact(block: Block, category=None, value=None) -> Fact:
    return Fact(category=category or block.category, page=block.page,
                value=value or block.text, quote=block.text)


MATERIAL = re.compile(r"\b(?:shall|must|required|responsib\w*|acceptance|retention|handover|"
                      r"security|warranty|standard|dependenc\w*|technical|on-premises|subject to|retain|resubmit)\b", re.I)
BACKGROUND = re.compile(r"\b(?:oldest agency|member states|headquartered|specialized agency|"
                        r"membership of|year of establishment|history of the organization)\b", re.I)
FILLER = re.compile(r"^(?:refer to|the (?:specific )?(?:exclusions|deadline and indicative|warranty period|"
                    r"review and acceptance procedure|required information).{0,140}(?:set out|listed|below)|"
                    r"annex .{0,80}form an integral part)|\b(?:suspends the corresponding payment|"
                    r"paid upon|authorization of payment|shall not be quoted)\b", re.I)
TIMED = re.compile(r"\b(?:until|ends? on|end on|completed by|in any case by|within|"
                   r"submitted by|submit .*? by|scheduled for|takes place on)\b", re.I)
PROJECT_REFERENCE = re.compile(r"\b(?:pilot|project|platform|work|service|implement|develop|deploy|integrat|moderniz|provide|aim)\w*\b", re.I)


def _deterministic(blocks: list[Block]) -> list[Fact]:
    facts = []
    for block in blocks:
        if block.kind != "project" or block.heading or INJECTION.search(block.text):
            continue
        if block.table:
            rows = table_cells(block)
            if rows and any("responsibilities" == c.casefold() for c in rows[0]):
                for row in rows[1:]:
                    for cell in row:
                        if len(cell.split()) >= 8:
                            part = replace(block, text=cell, table=False, cells=())
                            facts.extend(_fact(piece, "additional") for piece in _pieces(part)
                                         if MATERIAL.search(piece.text))
                            facts.extend(_fact(piece, "timeline") for piece in _pieces(part)
                                         if (TIMED.search(piece.text) or DUE.search(piece.text)) and re.search(
                                             r"\b(?:kick[- ]off meeting|monthly progress reports?)\b", piece.text, re.I))
            continue
        title = re.match(r"project (?:name|title)\s*:\s*(.+)", block.text, re.I)
        if title:
            facts.append(_fact(block, "title", title[1]))
            continue
        for piece in _pieces(block):
            text = piece.text
            if BACKGROUND.search(text) or FILLER.search(text) or re.fullmatch(r"[a-z0-9]+[.)]?", text, re.I):
                continue
            if block.category in {"timeline", "deadline", "milestone"}:
                if DUE.search(text) or TIMED.search(text) or DURATION.search(text) or block.category == "milestone":
                    facts.append(_fact(piece))
            elif block.category != "additional" or MATERIAL.search(text):
                facts.append(_fact(piece))
            if block.category not in {"deadline", "timeline", "milestone"}:
                if DUE.search(text) and (date_value(text) or TIMED.search(text)):
                    facts.append(_fact(piece, "deadline"))
                elif TIMED.search(text) and (date_value(text) or DURATION.search(text)):
                    if re.search(r"\b(?:kick[- ]off|warranty|deliverable|contract|report|workshop)\b", text, re.I):
                        facts.append(_fact(piece, "timeline"))
    return facts


def _merge(facts: list[Fact]) -> list[Fact]:
    merged = []
    seen = set()
    for fact in facts:
        key = (fact.category, BULLET.sub("", normalize(fact.value)).rstrip(".").casefold())
        if key in seen or any(other.category == fact.category and other.page == fact.page
                              and other.quote == fact.quote and contains(other.value, fact.value)
                              for other in merged):
            continue
        seen.add(key)
        merged.append(fact)
    return merged


GROUPS = [
    {"overview", "purpose"}, {"scope", "activity", "deliverable"}, {"objective"},
    {"methodology", "technical"}, {"additional", "date"},
]
STOPWORDS = set("the a an and or in on to of for with is be from by shall must will that this as at under".split())


def _terms(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", value.casefold())) - STOPWORDS


def _concise(value: str) -> list[str]:
    value = BULLET.sub("", value).strip()
    value = re.sub(r"^The objective of (?:this |the )?(?:procurement|project) is to contract a firm \(.*?\) to ",
                   "", value, flags=re.I)
    value = re.sub(r"^(?:This work is carried out|The services comprise|The expected result is)\s+", "",
                   value, flags=re.I)
    value = re.sub(r"\b(?:as described|as set out) in section [\d.]+(?: and Annex [A-Z-]+)?[.]?$", "", value, flags=re.I)
    value = re.sub(r"\s*\(hereinafter, [^)]+\)", "", value, flags=re.I)
    value = re.sub(r"\s*\((?:see )?sections? [\d., and]+\)", "", value, flags=re.I)
    value = re.sub(r"^(?:Its expected output is|Within this framework,|As a minimum requirement,)\s+", "", value, flags=re.I)
    value = re.sub(r"^The Contractor shall\s+", "", value, flags=re.I)
    value = value.strip(" ,;:")
    if len(value.split()) <= 42:
        return [value[:1].upper() + value[1:]] if value else []
    # Break long source clauses at grammatical boundaries without losing
    # the qualifying requirements that follow them.
    pieces = re.split(r";\s+|(?<=,)\s+(?=(?:including|subject to|with|and that|and the)\b)", value, flags=re.I)
    if len(pieces) == 1:
        pieces = re.split(r",\s+(?=(?:which|where|covering|including|with|through|and in)\b)", value, flags=re.I)
    return [part[:1].upper() + part[1:] for piece in pieces
            if len((part := piece.strip(" ,;")).split()) >= 3]


def _relevant(fact: Fact, source: Block | None) -> bool:
    text = fact.value
    if BACKGROUND.search(text) or FILLER.search(text) or "|" in text or len(_terms(text)) < 2:
        return False
    if fact.category in {"overview", "purpose"}:
        return bool(PROJECT_REFERENCE.search(text))
    if fact.category in {"additional", "date"}:
        return bool(MATERIAL.search(text)) or bool(source and source.category == "additional" and
            re.search(r"\b(?:retain|operate|provide|within|working days|business hours|monthly|Spanish)\b", text, re.I))
    return True


def summarize(facts: list[Fact], blocks: list[Block], warnings: list[str]) -> Summary:
    titles = [f.value for f in facts if f.category == "title"]
    title = " ".join(dict.fromkeys(titles)) if titles else MISSING
    schedule = build_schedule(blocks, facts)
    sections = [[] for _ in GROUPS]
    displayed: list[tuple[str, set[str]]] = []
    page_blocks = {}
    for block in blocks:
        page_blocks.setdefault(block.page, []).append(block)
    deliverables = [item for item in schedule if item.kind == "deliverable" and
                    (item.deadline or item.acceptance or item.expected_output)]
    for index, categories in enumerate(GROUPS):
        pool = []
        for fact in facts:
            if fact.category not in categories:
                continue
            source = next((b for b in page_blocks.get(fact.page, [])
                           if contains(normalize(b.text), fact.quote)), None)
            if not _relevant(fact, source):
                continue
            if index == 3 and (fact.category == "technical" and deliverables):
                continue
            if index == 3 and deliverables and re.search(r"\bbidders? shall propose\b", fact.value, re.I):
                continue
            score = 2 * bool(MATERIAL.search(fact.value)) + bool(source and source.category == fact.category)
            if index == 0:
                score += 3 * bool(re.search(r"\b(?:aims? to|expected output|purpose|will modernize)\b", fact.value, re.I))
            pool.append((score, fact))
        if index == 3 and deliverables:
            sections[index].extend(f"{item.identifier + ' - ' if item.identifier else ''}{item.activity}"
                                   for item in deliverables)
        if index == 4 and deliverables:
            for fact in facts:
                if fact.category == "technical" and _relevant(fact, None):
                    source = next((b for b in page_blocks.get(fact.page, [])
                                   if b.exclusion and contains(normalize(b.text), fact.quote)), None)
                    for value in _concise(fact.value):
                        if source:
                            value = "Scope exclusion — " + value
                        if value not in sections[index]:
                            sections[index].append(value)
            for item in deliverables:
                for value in _concise(item.acceptance):
                    label = f"{item.identifier} acceptance" if item.identifier else "Acceptance"
                    if value:
                        sections[index].append(f"{label} — {value}")
        # Keep implementation steps in source order. Rank descriptive material by relevance.
        if index in {0, 4}:
            pool.sort(key=lambda item: -item[0])
        for score, fact in pool:
            if index == 0 and sum(len(line.split()) for line in sections[index]) >= 100:
                continue
            if index == 4 and len(sections[index]) >= 8 and score < 2:
                continue
            for value in _concise(fact.value):
                terms = _terms(value)
                if not terms or any(value.casefold().rstrip(".") == key or
                        (len(terms) >= 5 and len(terms & prior) / len(terms | prior) >= .82)
                        for key, prior in displayed):
                    continue
                displayed.append((value.casefold().rstrip("."), terms))
                if index == 4 and source and source.table:
                    rows = table_cells(source)
                    if rows and any(c.casefold() == "responsibilities" for c in rows[0]):
                        actor = next((row[0] for row in rows[1:] if row[0] and
                                      any(contains(normalize(c), fact.quote) for c in row[1:])), "")
                        if actor:
                            value = f"{actor} — {value}"
                sections[index].append(value)
    for section in sections:
        if not section:
            section.append(MISSING)
    return Summary(title, sections, [item.row() for item in schedule], facts,
                   list(dict.fromkeys(warnings)), schedule)


def analyze(data: bytes, client: OllamaClient, mode: OCRMode = "Auto", language: str = "eng",
            progress: Progress | None = None) -> tuple[Summary, list[Page], list[Block]]:
    """Analyze every chunk, recording failures and retaining deterministic evidence."""
    def notify(fraction: float, message: str) -> None:
        if progress:
            progress(fraction, message)

    pages = extract_pdf(data, mode, language, lambda i, n: notify(.3 * i / n, f"Extracted page {i} of {n}"))
    blocks = classify(preprocess(pages))
    chunks = chunk_blocks(blocks)
    if len(chunks) > 6:
        chunks = [[block for chunk in chunks[i:i + 3] for block in chunk]
                  for i in range(0, len(chunks), 3)]
    if not chunks:
        raise ValueError("No readable project text was extracted. Check the PDF and OCR settings.")
    warnings = [f"Page {p.number}: {w}" for p in pages for w in p.warnings]
    if language != "eng" and mode != "Off":
        warnings.append("OCR uses the selected language pack. Section and schedule rules are tuned for English.")
    verified = []
    reasons: Counter = Counter()

    def collect(facts: list[Fact], evidence: list[Block]) -> None:
        for fact in facts:
            valid, reason = verify(fact, evidence)
            if valid:
                verified.append(valid)
            else:
                reasons[(fact.page, reason)] += 1

    # Use full classified blocks for layout-based evidence and chunk-local evidence for Ollama.
    lookup = {}
    for block in blocks:
        lookup.setdefault(block.page, []).append(block)
    for fact in _deterministic(blocks):
        collect([fact], lookup[fact.page])
    client.check()
    for index, chunk in enumerate(chunks, 1):
        notify(.3 + .6 * (index - 1) / len(chunks), f"Analyzing chunk {index} of {len(chunks)}")
        records = [{"page": b.page, "section": b.category, "text": b.text,
                    **({"cells": list(table_cells(b)[0])} if b.table else {})} for b in chunk]
        try:
            candidates, invalid = client.extract(records)
        except OllamaError as exc:
            warnings.append(f"Chunk {index} (pages {chunk[0].page}–{chunk[-1].page}): {exc} "
                            "The report includes only facts verified against this PDF.")
            continue
        if invalid:
            warnings.append(f"Chunk {index}: {invalid} malformed fact(s) ignored.")
        collect(candidates, chunk)
    for (page, reason), count in reasons.items():
        warnings.append(f"Page {page}: {count} fact(s) excluded because {reason}.")
    merged = _merge(verified)
    summary = summarize(merged, blocks, warnings)
    if not merged and not summary.schedule:
        raise ValueError("No project facts could be verified in this PDF.")
    sections = ("overview and purpose", "scope, activities and deliverables", "objectives",
                "methodology and technical approach", "additional information")
    missing = [name for name, lines in zip(sections, summary.sections) if lines == [MISSING]]
    if missing:
        summary.warnings.append("No verified information for " + ", ".join(missing) + ".")
    if not summary.timeline:
        summary.warnings.append("No supported timeline entries were found.")
    if summary.title == MISSING:
        summary.warnings.append("Project name was not established by source evidence.")
    notify(1, "Analysis complete")
    return summary, pages, blocks
