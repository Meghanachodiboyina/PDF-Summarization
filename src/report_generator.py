"""Build a formatted Microsoft Word report in memory."""
from io import BytesIO
import re
from zipfile import BadZipFile

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from .config import COLUMNS, MISSING
from .schemas import Summary

HEADINGS = ("Project Overview", "Scope of Work", "Project Objectives",
            "Proposed Methodology", "Implementation Timeline", "Additional Information")
MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def detail_label(text: str) -> str:
    rules = (
        ("Acceptance", r"acceptance|conformance|criteria|resubmit"),
        ("Support and warranty", r"warranty|support|incident|escalat"),
        ("Handover", r"handover|training|documentation|repository|runbook"),
        ("Security and data", r"security|retention|privacy|consent|credentials|encrypt"),
        ("Responsibilities and dependencies", r"responsib|provides?|coordination|operator|access"),
        ("Technical constraints", r"technical|standard|on-premis|infrastructure|specification"),
    )
    return next((label for label, pattern in rules if re.search(pattern, text, re.I)), "Delivery requirements")


def method_label(text: str) -> str:
    labels = (
        ("Plan and design", r"survey|design|baseline|architect|work plan"),
        ("Deploy and validate", r"deploy|pilot|commission"),
        ("Integrate and test", r"integrat|test|calibrat|validat"),
        ("Transfer operations", r"handover|training|documentation"),
        ("Apply technical controls", r"buffer|security|publish|require|must"),
    )
    return next((label for label, pattern in labels if re.search(pattern, text, re.I)), "Implement")


def _new_list(doc, style: str) -> int:
    numbering = doc.part.numbering_part.element
    abstracts = numbering.xpath(f'./w:abstractNum[w:lvl/w:pStyle[@w:val="{style}"]]')
    abstract = abstracts[0].get(qn("w:abstractNumId"))
    instance = numbering.add_num(int(abstract))
    if style == "ListNumber":
        instance.add_lvlOverride(0).add_startOverride(1)
    return int(instance.numId)


def _list_item(doc, text: str, num: int, label: str = "") -> None:
    paragraph = doc.add_paragraph()
    numbering = paragraph._p.get_or_add_pPr().get_or_add_numPr()
    numbering.get_or_add_ilvl().val = 0
    numbering.get_or_add_numId().val = num
    paragraph.paragraph_format.space_after = Pt(4)
    if label:
        paragraph.add_run(label + " — ").bold = True
    paragraph.add_run(text)


def _heading_numbering(doc) -> int:
    numbering = doc.part.numbering_part.element
    ids = [int(value) for value in numbering.xpath("./w:abstractNum/@w:abstractNumId")]
    abstract_id = max(ids, default=-1) + 1
    abstract = OxmlElement("w:abstractNum")
    abstract.set(qn("w:abstractNumId"), str(abstract_id))
    multi_level = OxmlElement("w:multiLevelType")
    multi_level.set(qn("w:val"), "multilevel")
    abstract.append(multi_level)
    for index, pattern in enumerate(("%1.", "%1.%2")):
        level = OxmlElement("w:lvl")
        level.set(qn("w:ilvl"), str(index))
        for tag, value in (("start", "1"), ("numFmt", "decimal"),
                           ("pStyle", f"Heading{index + 1}"), ("suff", "space"),
                           ("lvlText", pattern), ("lvlJc", "left")):
            element = OxmlElement(f"w:{tag}")
            element.set(qn("w:val"), value)
            level.append(element)
        abstract.append(level)
    numbering.insert_element_before(abstract, "w:num")
    return int(numbering.add_num(abstract_id).numId)


def _add_numbered_heading(doc, label: str, level: int, num_id: int) -> None:
    paragraph = doc.add_heading(label, level=level)
    properties = paragraph._p.get_or_add_pPr().get_or_add_numPr()
    properties.get_or_add_ilvl().val = level - 1
    properties.get_or_add_numId().val = num_id


def generate_report(summary: Summary) -> BytesIO:
    """Render the six required sections and a timeline table when rows exist."""
    try:
        doc = Document()
        doc.core_properties.title = summary.title
        layout = doc.sections[0]
        layout.top_margin = layout.bottom_margin = Inches(.75)
        layout.left_margin = layout.right_margin = Inches(.8)
        normal = doc.styles["Normal"]
        normal.font.name = "Calibri"
        normal.font.size = Pt(11)
        normal.paragraph_format.space_after = Pt(6)
        normal.paragraph_format.line_spacing = 1.12
        heading = doc.styles["Heading 1"]
        heading.font.name = "Calibri"
        heading.font.size = Pt(14)
        heading.font.bold = True
        heading.font.color.rgb = RGBColor.from_string("17354B")
        heading.paragraph_format.space_before = Pt(13)
        heading.paragraph_format.space_after = Pt(5)
        subheading = doc.styles["Heading 2"]
        subheading.font.name = "Calibri"
        subheading.font.size = Pt(11)
        subheading.font.bold = True
        subheading.font.color.rgb = RGBColor.from_string("17354B")
        title = doc.add_paragraph()
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        title.paragraph_format.keep_with_next = True
        title.paragraph_format.space_after = Pt(12)
        run = title.add_run(summary.title or MISSING)
        run.font.name = "Calibri"
        run.font.size = Pt(16)
        run.bold = True
        content = iter(summary.sections)
        heading_num = _heading_numbering(doc)
        for index, label in enumerate(HEADINGS, 1):
            _add_numbered_heading(doc, label, 1, heading_num)
            if index != 5:
                lines = next(content) or [MISSING]
                if index == 1 or lines == [MISSING]:
                    for line in lines:
                        p = doc.add_paragraph()
                        label, separator, value = line.partition(" — ")
                        if separator and len(label.split()) <= 5:
                            p.add_run(label + separator).bold = True
                            p.add_run(value)
                        else:
                            p.add_run(line)
                    continue
                list_id = _new_list(doc, "ListNumber" if index in {3, 4} else "ListBullet")
                if index == 6:
                    groups = {}
                    for line in lines:
                        groups.setdefault(detail_label(line), []).append(line)
                    for group, group_lines in groups.items():
                        _add_numbered_heading(doc, group, 2, heading_num)
                        for line in group_lines:
                            label, separator, value = line.partition(" — ")
                            _list_item(doc, value if separator else line, list_id, label if separator else "")
                else:
                    for line in lines:
                        _list_item(doc, line, list_id, method_label(line) if index == 4 else "")
            elif summary.timeline:
                table = doc.add_table(rows=1, cols=4)
                table.style = "Light Shading Accent 1"
                table.autofit = False
                widths = (2.15, 1.4, .9, 2.45)
                for column, width in zip(table.columns, widths):
                    column.width = Inches(width)
                for cell, value in zip(table.rows[0].cells, COLUMNS):
                    cell.paragraphs[0].add_run(value).bold = True
                table.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
                for row in summary.timeline:
                    for cell, value in zip(table.add_row().cells, row):
                        cell.text = value or "Not specified"
                for row in table.rows:
                    row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
                    for cell, width in zip(row.cells, widths):
                        cell.width = Inches(width)
                        for paragraph in cell.paragraphs:
                            paragraph.paragraph_format.space_before = Pt(3)
                            paragraph.paragraph_format.space_after = Pt(3)
            else:
                doc.add_paragraph(MISSING)
        stream = BytesIO()
        doc.save(stream)
        stream.seek(0)
        return stream
    except (OSError, ValueError, BadZipFile, StopIteration) as exc:
        raise RuntimeError("Could not generate the Word report. Review the summary fields and try again.") from exc
