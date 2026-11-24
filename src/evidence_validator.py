"""Validate copied facts and reject unsupported or ambiguous claims."""
from difflib import SequenceMatcher
import re

from .schemas import Block, Fact
from .text_preprocessor import normalize

DUE = re.compile(r"\b(?:deadlines?|due(?!\s+to\b)(?:\s+(?:on|by|date))?|submit(?:ted)?\s+(?:by|on)|submission\s+(?:date|time|deadline)|no later than)\b", re.I)
DURATION = re.compile(r"\b(?:\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|twelve)\s*[- ]?\s*(?:business\s+|working\s+|calendar\s+)?(?:hours?|days?|weeks?|months?|years?)\b", re.I)
INJECTION = re.compile(r"\b(?:ignore (?:all |any )?(?:previous |prior |above )?(?:instructions|system)|system prompt|you are chatgpt|override (?:the )?instructions|follow these instructions instead)\b", re.I)


def contains(haystack: str, needle: str) -> bool:
    if not needle:
        return False
    return bool(re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", haystack))


def verify(fact: Fact, source: list[Block]) -> tuple[Fact | None, str | None]:
    quote, value = normalize(fact.quote), normalize(fact.value)
    if not value or not quote or INJECTION.search(quote) or not contains(quote, value):
        return None, "value is unsupported by its quoted evidence"
    candidates = [block for block in source if block.page == fact.page and not block.heading
                  and block.kind == "project"
                  and not INJECTION.search(block.text)]
    if any(contains(normalize(block.text), quote) for block in source
           if block.page == fact.page and block.heading) and not any(
               contains(normalize(block.text), quote) for block in candidates):
        return None, "the matching text is a section heading"
    matches = [block for block in candidates if contains(normalize(block.text), quote)]
    if not matches:
        near = any(SequenceMatcher(None, quote, normalize(block.text), autojunk=False).ratio() >= .92
                   for block in candidates if len(block.text) <= 4000 and len(quote) >= 25)
        return None, "near match requires review" if near else "quote was not found on the stated page"
    if fact.category != "title" and all(b.category == "title" for b in matches):
        return None, "cover title is not an implementation fact"
    if fact.category == "deadline":
        if not DUE.search(quote) and not any(b.category == "deadline" for b in matches):
            return None, "deadline wording is missing"
        if DURATION.fullmatch(value):
            return None, "a duration alone does not establish a deadline"
    elif fact.category == "duration" and not DURATION.fullmatch(value):
        return None, "duration units are missing"
    elif fact.category == "title" and not any(b.category == "title" or
            re.match(r"project (?:name|title)\s*:", b.text, re.I) for b in matches):
        return None, "project title lacks explicit or layout evidence"
    return fact.model_copy(update={"value": value, "quote": quote}), None
