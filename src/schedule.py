"""Recover schedule records from aligned cells and explicit dated clauses."""
from dataclasses import replace
from datetime import datetime
import re

from dateutil.parser import parse

from .evidence_validator import DURATION, DUE, INJECTION
from .schemas import Block, Fact, TimelineItem

MONTH = (r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
         r"Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?")
DATE = re.compile(rf"\b(?:\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}[/.-]\d{{1,2}}[/.-]\d{{2,4}}|\d{{1,2}}/\d{{1,2}}|"
                  rf"\d{{1,2}}[ -]+(?:{MONTH})(?:[ -]+\d{{4}})?|"
                  rf"(?:{MONTH})\s+\d{{1,2}}(?:,?\s+\d{{4}})?|"
                  rf"\d{{1,2}}:\d{{2}}(?:\s*[ap]m)?)\b", re.I)
RELATIVE = re.compile(r"\b(?:within\s+(?!the dates?\b)[^.;]{1,100}|(?:upon|after|before)\s+(?:award|acceptance|approval|completion)[^.;]{0,60}|"
                      r"(?:by\s+)?(?:the\s+)?\d+(?:st|nd|rd|th)\s+business day of [^.;]{1,70})", re.I)
IDENTIFIER = re.compile(r"^(?:deliverable\s+|D\s*[- ]?)(\d+)\b", re.I)
MISSING_CELL = {"", "n/a", "none", "—", "-", "not specified", "tbd", "to be confirmed"}
SCHEDULE_WORD = re.compile(r"\b(?:kick[- ]off|handover|deployment|installation|training|survey|inspection|"
                           r"test(?:ing)?|report|review|design|commissioning|workshop|delivery|implementation|"
                           r"build|migration|pilot|release|audit|assessment|validation|certification)\b", re.I)
WARRANTY = re.compile(r"\bwarranty\b", re.I)


def date_value(text: str) -> str:
    for match in DATE.finditer(text):
        try:
            parse(match[0], default=datetime(2000, 1, 1))
        except (ValueError, OverflowError):
            continue
        return match[0]
    return ""


def _columns(cells: tuple[str, ...]) -> dict[str, int]:
    columns = {}
    for index, cell in enumerate(cells):
        label = re.sub(r"\s+", " ", cell.casefold()).strip()
        if re.fullmatch(r"#|no\.?|id|identifier|deliverable (?:id|no\.?|number)", label):
            columns.setdefault("identifier", index)
        elif re.search(r"deadline|due date|submission (?:date|time)|completion date|target date|^due$|^date$", label):
            columns.setdefault("deadline", index)
        elif re.fullmatch(r"(?:planned |estimated )?duration|elapsed time", label):
            columns.setdefault("duration", index)
        elif re.fullmatch(r"milestones?|expected output|milestone\s*/\s*expected output", label):
            columns.setdefault("milestone", index)
        elif re.fullmatch(r"acceptance(?: (?:criteria|requirements))?", label):
            columns.setdefault("acceptance", index)
        elif re.fullmatch(r"content|outputs?|expected results?", label):
            columns.setdefault("output", index)
        elif re.fullmatch(r"activity(?:\s*/\s*(?:work item|deliverable))?|activities|work items?|tasks?|phases?|deliverables?|description", label):
            columns.setdefault("activity", index)
    if "activity" not in columns and "milestone" in columns and "deadline" in columns:
        columns["activity"] = columns.pop("milestone")
    if "activity" not in columns or not {"deadline", "duration", "milestone"}.intersection(columns):
        return {}
    return columns


def table_cells(block: Block) -> tuple[tuple[str, ...], ...]:
    return block.cells or tuple(tuple(cell.strip() for cell in line.split("|"))
                                for line in block.text.splitlines())


def _deadline_cell(text: str) -> str:
    if text.casefold() in MISSING_CELL or DURATION.fullmatch(text):
        return ""
    return text if date_value(text) or RELATIVE.search(text) or re.fullmatch(r"week\s+\d{1,3}", text, re.I) else ""


def table_schedule(blocks: list[Block]) -> list[TimelineItem]:
    output = []
    prior: tuple[int, int, dict[str, int], bool] | None = None
    for block in blocks:
        if block.kind != "project" or INJECTION.search(block.text):
            prior = None
            continue
        if not block.table:
            if block.heading and block.category not in {"timeline", "deliverable", "milestone", "deadline", "activity"}:
                prior = None
            continue
        cells = table_cells(block)
        if not cells:
            continue
        first = next(((i, _columns(row)) for i, row in enumerate(cells[:3]) if _columns(row)), None)
        if first:
            header, columns = first
            widths = len(cells[header])
            deliverable = "deliverable" in cells[header][columns["activity"]].casefold()
            deliverable = deliverable or (block.category == "deliverable" and "identifier" in columns)
            rows = cells[header + 1:]
        elif prior and (block.page - prior[0] in {0, 1}) and len(cells[0]) == prior[1]:
            _, widths, columns, deliverable = prior
            rows = cells
            # Require a schedule-shaped row before reusing adjacent-page headers.
            if not any(len(row) == widths and (_deadline_cell(row[columns["deadline"]])
                       if "deadline" in columns else DURATION.search(row[columns["duration"]])
                       if "duration" in columns else False) for row in rows):
                prior = None
                continue
        else:
            prior = None
            continue
        prior = block.page, widths, columns, deliverable
        for row in rows:
            if len(row) != widths or _columns(row):
                continue
            def cell(key: str) -> str:
                found = row[columns[key]].strip() if key in columns else ""
                return "" if found.casefold() in MISSING_CELL else found

            activity, identifier = cell("activity"), cell("identifier")
            inline = IDENTIFIER.match(activity)
            if inline:
                identifier = identifier or f"D{inline[1]}"
                activity = activity[inline.end():].strip(" .:-—") or activity
            if not activity:
                continue
            if deliverable and identifier.isdigit():
                identifier = "D" + identifier
            duration = cell("duration")
            content = cell("output")
            short_output = re.split(r";|\.(?=\s+[A-Z])|,\s+(?:with|including|under)\b", content, maxsplit=1)[0].strip()
            short_output = re.sub(r"^\([a-z]\)\s+", "", short_output, flags=re.I)
            if len(short_output.split()) > 26:
                short_output = ""
            output.append(TimelineItem(activity, block.page, " | ".join(row), identifier,
                                       _deadline_cell(cell("deadline")),
                                       duration if DURATION.search(duration) else "", cell("milestone"),
                                       "deliverable" if deliverable or inline else "activity",
                                       cell("acceptance"), short_output))
    return output


def _prose_items(facts: list[Fact], blocks: list[Block]) -> list[TimelineItem]:
    output = []
    for fact in facts:
        if fact.category not in {"activity", "deliverable", "timeline", "milestone", "deadline", "duration"}:
            continue
        text = fact.value.strip()
        if "|" in text:
            continue
        text = re.sub(r"^(?:[-•]\s+|\d+[.)]\s+)", "", text)
        date = date_value(text)
        duration = DURATION.search(text)
        due = DUE.search(text)
        ending = re.search(r"\b(?:until|ends? on|ending on|end on|completed by|in any case by|"
                           r"submitted by|submit .*? by|scheduled for|takes place on)\b", text, re.I)
        explicit_duration = bool(duration and re.search(r"\b(?:lasts?|takes?|duration|will run for)\b", text, re.I))
        relative = RELATIVE.search(text)
        if WARRANTY.search(text) and date and ending:
            end_date = date_value(text[ending.end():]) or date
            output.append(TimelineItem("Warranty and support", fact.page, fact.quote, deadline=end_date,
                                       milestone="Warranty ends", kind="contract"))
            continue
        if re.search(r"\b(?:deliverables\s+\d+\s+(?:to|through|[-–])|coordination will take place|payment terms)\b", text, re.I):
            continue
        if re.search(r"\b(?:payment|invoice|quotation|bid submission|proposal submission|"
                     r"submit (?:the |a )?(?:bid|proposal))\b", text, re.I):
            continue
        if relative:
            named = re.search(r"\b(?:kick[- ]off meeting|monthly progress reports?)\b", text[:relative.start()], re.I)
            if named and (due or re.search(r"\b(?:hold|holds|provide|deliver|submit)\b", text[:named.start()], re.I)):
                output.append(TimelineItem(named[0][:1].upper() + named[0][1:], fact.page, fact.quote,
                                           deadline=relative[0].rstrip(" ,")))
                continue
        if re.search(r"\b(?:deliverables\s+\d+\s+(?:to|through|[-?])|coordination will take place|payment terms)\b", text, re.I):
            continue
        inline = IDENTIFIER.match(text)
        is_deliverable = bool(inline) or fact.category == "deliverable"
        is_timed = bool((date or relative) and (inline or due or ending or (relative and SCHEDULE_WORD.search(text))))
        is_concrete = bool(SCHEDULE_WORD.search(text)) and not re.search(
            r"\b(?:background|beneficiary institution|business hours|as needed)\b", text, re.I)
        if not (is_timed or explicit_duration or (is_concrete and fact.category in {"activity", "milestone"})
                or (is_deliverable and is_concrete and len(text.split()) <= 16)):
            continue
        marker = due or ending or relative or duration
        cutoff = marker.start() if marker else text.index(date) if inline and date else len(text)
        activity = text[:cutoff].rstrip(" .:-—")
        activity = re.sub(r"\s+(?:is|are|shall be|must be|will be|will last|lasts|takes)$", "", activity, flags=re.I)
        identifier = f"D{inline[1]}" if inline else ""
        if inline and len(activity) > inline.end():
            activity = activity[inline.end():].strip(" :-—")
        if not activity or len(activity.split()) > 18 or (not is_concrete and not inline and not is_timed):
            continue
        # Preserve the complete source wording in a dedicated deadline statement for review.
        marker_date = date_value(text[(due or ending).end():]) if due or ending else ""
        deadline = text if due and fact.category == "deadline" and not inline else (
            marker_date or date or (relative[0] if relative else ""))
        output.append(TimelineItem(activity, fact.page, fact.quote, identifier, deadline if is_timed else "",
                                   duration[0] if explicit_duration else "",
                                   activity if fact.category == "milestone" else "",
                                   "deliverable" if is_deliverable else "activity"))
    return output


def build_schedule(blocks: list[Block], facts: list[Fact]) -> list[TimelineItem]:
    rows = table_schedule(blocks)
    first_rows = {}
    for item in rows:
        key = (item.identifier.casefold(), item.deadline.casefold()) if item.identifier else tuple(item.row())
        existing = first_rows.get(key)
        if existing is None or (not existing.acceptance and item.acceptance):
            first_rows[key] = item
    items = list(first_rows.values())
    for candidate in _prose_items(facts, blocks):
        key = candidate.activity.casefold().rstrip(".")
        matches = [i for i, item in enumerate(items)
                   if (candidate.identifier and candidate.identifier == item.identifier)
                   or key == item.activity.casefold().rstrip(".")]
        if matches:
            index = matches[0]
            item = items[index]
            if not item.deadline and candidate.deadline:
                items[index] = replace(candidate, activity=item.activity, identifier=item.identifier or candidate.identifier,
                                       duration=item.duration or candidate.duration, milestone=item.milestone or candidate.milestone,
                                       kind=item.kind, acceptance=item.acceptance, expected_output=item.expected_output)
            continue
        if any(candidate.evidence == item.evidence and candidate.deadline == item.deadline for item in items):
            continue
        items.append(candidate)
    return items
