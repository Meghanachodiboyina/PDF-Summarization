"""Deterministic section labels, propagated across page boundaries."""
import re
from dataclasses import replace

from .schemas import Block, Category

EXCLUDED = {"administrative": r"(?:(?:bidder|supplier) qualifications?(?: requirements| and experience)?|"
            r"key personnel.*|(?:technical )?evaluation criteria|content of submission|payment terms|"
            r"lead bidder and delivery partners|methodology and evaluation criteria|procurement requirements|"
            r"(?:bid|proposal|quotation) (?:instructions|submission deadlines?|deadlines?))",
            "contents": r"(?:table of )?contents",
            "glossary": r"glossary|abbreviations(?: and acronyms)?|acronyms|definitions"}
TOC_LINE = re.compile(r"(?:\.{2,}|…|\s{3,})\s*\d+\s*$")
ADMIN = re.compile(r"\b(?:financial offer|supplier registration|eligibility declaration|quotation submission|"
                   r"bidder qualifications|evaluation criteria|payments? (?:will|shall) be made)\b", re.I)
GENERIC_TITLE = re.compile(r"^(?:annex\b|request for\b|terms of reference$|rfq[-\s]|table of contents$)", re.I)

HEADINGS: dict[Category, str] = {
    "title": r"project (title|name)",
    "overview": r"(project )?(overview|description|background|introduction|executive summary)|objectives? and background",
    "purpose": r"(project )?purpose",
    "scope": r"scope( of work| of services(?: and expected results)?)?|statement of work|contract structure and delivery model",
    "objective": r"(project )?(objectives?|goals|intended outcomes)",
    "methodology": r"(proposed )?(methodology|implementation approach|methods)( and work plan)?",
    "technical": r"technical (approach|details|requirements|specifications)|specific constraints and limitations|"
                 r"scope exclusions|out of scope",
    "activity": r"(major |project |key )?activities|work items",
    "deliverable": r"(project |key )?deliverables|summary of deliverables",
    "timeline": r"(implementation |project )?(timeline|schedule|work plan)|schedule and milestones|contract duration",
    "milestone": r"(project |key )?milestones",
    "deadline": r"(submission )?deadlines?|due dates",
    "additional": r"additional( technical)? information|constraints|assumptions|roles and responsibilities|"
                  r"roles, governance and coordination|change management, maintenance, warranty and support|"
                  r"documentation and delivery format|training and knowledge transfer|review and acceptance procedure",
}


def _label(text: str) -> str:
    return re.sub(r"^(?:\d+(?:\.\d+)*[.)]?\s+|[IVX]+[.)]\s+)", "", text.strip()).rstrip(":").strip()


def heading_category(text: str) -> Category | None:
    label = _label(text)
    return next((category for category, pattern in HEADINGS.items()
                 if re.fullmatch(pattern, label, re.I)), None)


def classify(blocks: list[Block]) -> list[Block]:
    category: Category = "additional"
    kind = "project"
    output = []
    seen_body = False
    exclusion = False
    title_candidates = []
    for block in blocks:
        if block.kind != "project" or TOC_LINE.search(block.text):
            output.append(replace(block, kind="contents" if block.kind == "project" else block.kind))
            continue
        label = None if block.table else heading_category(block.text)
        excluded = next((key for key, pattern in EXCLUDED.items()
                         if not block.table and re.fullmatch(pattern, _label(block.text), re.I)), None)
        if excluded:
            kind = excluded
            output.append(replace(block, heading=True, kind=kind))
            continue
        if kind == "contents" and not (block.heading and re.match(r"^\d+\.\s+[A-Z]", block.text)
                                       and label and block.page != output[-1].page):
            output.append(replace(block, kind=kind))
            continue
        if kind == "glossary" and not (label and block.heading):
            output.append(replace(block, kind=kind))
            continue
        if label:
            category = label
            kind = "project"
            exclusion = bool(re.search(r"scope exclusions|out of scope", block.text, re.I))
        elif block.heading and re.match(r"^\d+\.\d+\b", block.text):
            exclusion = False
        elif not seen_body and block.page == 1 and block.heading and not block.edge and not block.table and len(block.text) <= 130:
            if not GENERIC_TITLE.search(block.text) and not re.match(r"^\d+[.)]", block.text):
                title_candidates.append(len(output))
                output.append(replace(block, kind="irrelevant", heading=False))
            else:
                output.append(replace(block, kind="administrative"))
            continue
        block_kind = "administrative" if ADMIN.search(block.text) and not block.table and not re.search(
            r"\b(?:shall|must)\b.*\b(?:deploy|integrate|test|maintain|correct|resubmit)\b", block.text, re.I) else kind
        if re.search(r"(?:outside|out of) the scope of this (?:project|contract)", block.text, re.I):
            exclusion = True
        output.append(replace(block, category=category, kind=block_kind,
                              heading=bool(label) or block.heading, exclusion=exclusion))
        seen_body = seen_body or not block.edge
    if title_candidates:
        # A multi-line cover can state the project after generic document labels.
        for index in title_candidates:
            output[index] = replace(output[index], kind="project", category="title")
    return output
