from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH

from src.config import COLUMNS, MISSING
from src.report_generator import HEADINGS, generate_report
from src.schemas import Summary


def test_word_sections_formatting_and_table():
    summary = Summary("River Project", [["Purpose"], ["Scope"], ["Goal"], ["Methods"], ["Details"]],
                      [["Survey", "Not specified", "2 weeks", "Approved"]])
    doc = Document(generate_report(summary))
    title = doc.paragraphs[0]
    assert title.text == "River Project"
    assert title.alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert title.runs[0].font.size.pt == 16 and title.runs[0].bold
    assert doc.styles["Normal"].font.name == "Calibri"
    assert doc.styles["Normal"].font.size.pt == 11
    heading = doc.styles["Heading 1"]
    assert heading.font.size.pt == 14 and heading.font.bold
    titles = [p for p in doc.paragraphs if p.style.name == "Heading 1"]
    assert [p.text for p in titles] == list(HEADINGS)
    subheadings = [p for p in doc.paragraphs if p.style.name == "Heading 2"]
    number_id = titles[0]._p.pPr.numPr.numId.val
    assert all(p._p.pPr.numPr.numId.val == number_id for p in titles + subheadings)
    assert all(p._p.pPr.numPr.ilvl.val == 0 for p in titles)
    assert all(p._p.pPr.numPr.ilvl.val == 1 for p in subheadings)
    numbering = doc.part.numbering_part.element
    abstract_id = numbering.xpath(f'./w:num[@w:numId="{number_id}"]/w:abstractNumId/@w:val')[0]
    assert numbering.xpath(f'./w:abstractNum[@w:abstractNumId="{abstract_id}"]/w:lvl/w:lvlText/@w:val') == [
        "%1.", "%1.%2"]
    table, = doc.tables
    assert [c.text for c in table.rows[0].cells] == list(COLUMNS)
    assert table.cell(1, 2).text == "2 weeks"
    assert table.rows[0]._tr.xpath("./w:trPr/w:tblHeader")


def test_empty_timeline_does_not_emit_empty_table():
    doc = Document(generate_report(Summary(MISSING, [[], [], [], [], []], [])))
    assert not doc.tables
    assert sum(p.text == MISSING for p in doc.paragraphs) == 7
