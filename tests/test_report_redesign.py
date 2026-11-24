"""Regression coverage for content filtering, scheduling and report review."""
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pymupdf
import pytest
import streamlit
from docx import Document
from streamlit.testing.v1 import AppTest

from src.document_analyzer import analyze, summarize
from src.pdf_extractor import extract_pdf
from src.report_generator import generate_report
from src.schedule import build_schedule
from src.schemas import Block, Fact, Page, Summary
from src.section_classifier import classify


class EmptyModel:
    def check(self):
        pass

    def extract(self, records):
        return [], 0


def test_filter_toc_glossary_and_procurement():
    blocks = classify([
        Block(1, "Table of Contents", heading=True),
        Block(1, "2. Scope of Work"),
        Block(1, "Project Objectives ........ 5"),
        Block(2, "Glossary", heading=True),
        Block(2, "Term | Definition\nSLA | Service level agreement", table=True),
        Block(3, "1. Project Objectives", heading=True),
        Block(3, "Reduce flood response time."),
        Block(3, "Bidder Qualification Requirements", heading=True),
        Block(3, "Submit the financial offer on 1 May 2027."),
        Block(4, "2. Scope of Work", heading=True),
        Block(4, "Install monitoring stations."),
    ])
    assert [b.kind for b in blocks] == ["contents"] * 3 + ["glossary"] * 2 + [
        "project", "project", "administrative", "administrative", "project", "project"]


def test_schedule_association_and_blank_date_across_real_pdf_pages():
    with pymupdf.open() as pdf:
        for heading, rows in [
            ("Deliverables", [("ID", "Deliverable", "Deadline", "Duration"),
                              ("1", "Site design", "15 May 2027", "2 weeks")]),
            ("", [("2", "Sensor commissioning", "15 June 2027", ""),
                  ("3", "Training", "", "1 week")]),
        ]:
            p = pdf.new_page()
            if heading:
                p.insert_text((50, 80), heading, fontsize=17)
            for n, row in enumerate(rows):
                y = 105 + n * 28
                for i, text in enumerate(row):
                    x = 50 + i * 117
                    p.draw_rect(pymupdf.Rect(x, y, x + 117, y + 28))
                    p.insert_text((x + 5, y + 17), text, fontsize=9)
        pages = extract_pdf(pdf.tobytes(), "Off")
    items = build_schedule(classify([b for p in pages for b in p.blocks]), [])
    assert [(i.identifier, i.deadline, i.page) for i in items] == [
        ("D1", "15 May 2027", 1), ("D2", "15 June 2027", 2), ("D3", "", 2)]
    assert items[-1].row()[1:3] == ["Not specified", "1 week"]
    assert "Sensor commissioning" in items[1].evidence


def test_background_false_dates_duplicates_and_long_paragraphs():
    values = [
        ("overview", "The project will deploy six monitoring sites."),
        ("scope", "The project will deploy six monitoring sites."),
        ("timeline", "Coordination will take place during business hours."),
        ("timeline", "The payment process ends on completion of section 5.2."),
        ("deadline", "The acceptance report is due on 12 May 2027."),
        ("methodology", "Install the monitoring equipment and confirm the communications channel at each site "
                        "before accepting the field work and handing over control to the operating teams, including "
                        "a documented commissioning procedure, with the test results filed in the repository "
                        "for the project manager and field supervisors to inspect."),
    ]
    facts = [Fact(category=k, page=1, value=v, quote=v) for k, v in values]
    summary = summarize(facts, [], [])
    assert len(summary.timeline) == 1 and "12 May 2027" in summary.timeline[0][1]
    assert sum(values[0][1] in section for section in summary.sections) == 1
    assert max(len(line.split()) for line in summary.sections[3]) <= 42


def test_real_word_list_ids_and_action_labels():
    summary = Summary("Sensors", [["Project summary"], ["Install sensors"], ["Reduce response time"],
                      ["Design monitoring stations", "Deploy and validate"], ["Retain logs"]], [])
    doc = Document(generate_report(summary))
    lookup = {p.text: p for p in doc.paragraphs}
    bullet = lookup["Install sensors"]._p.pPr.numPr.numId.val
    objectives = lookup["Reduce response time"]._p.pPr.numPr.numId.val
    methods = [p for p in doc.paragraphs if p.text.endswith(("Design monitoring stations", "Deploy and validate"))]
    method_ids = {p._p.pPr.numPr.numId.val for p in methods}
    assert len(method_ids | {bullet, objectives}) == 3
    assert all(p.runs[0].bold for p in methods)
    numbering = doc.part.numbering_part.element
    for num, expected in [(bullet, "bullet"), (objectives, "decimal"), (method_ids.pop(), "decimal")]:
        abstract = numbering.xpath(f'./w:num[@w:numId="{num}"]/w:abstractNumId/@w:val')[0]
        assert numbering.xpath(f'./w:abstractNum[@w:abstractNumId="{abstract}"]/w:lvl/w:numFmt/@w:val') == [expected]


def test_streamlit_edit_exports_without_second_analysis():
    sample = Path(__file__).resolve().parents[1] / "samples" / "sample_project.pdf"

    class Upload:
        name = sample.name

        def getvalue(self):
            return sample.read_bytes()

    summary = Summary("Initial", [["Overview"], ["Scope"], ["Objective"], ["Methods"], ["Details"]],
                      [["Survey", "15 May 2027", "2 weeks", "Approved"]])
    with patch.object(streamlit, "file_uploader", return_value=Upload()), patch(
        "src.llm_client.OllamaClient.models", return_value=["qwen3:1.7b"]
    ), patch("src.document_analyzer.analyze", return_value=(summary, [Page(i) for i in range(1, 4)], [])) as run:
        app = AppTest.from_file(sample.parents[1] / "app.py", default_timeout=25).run()
        app.button[0].click().run()
        assert not app.exception
        app.text_input(key="project_name_1").set_value("Reviewed title")
        app.text_area(key="section_1_2").set_value("Updated scope")
        app.button[1].click().run()
        assert not app.exception
        doc = Document(BytesIO(app.session_state["report"][1]))
        assert doc.paragraphs[0].text == "Reviewed title"
        assert any(p.text == "Updated scope" for p in doc.paragraphs)
        assert run.call_count == 1
        # Replace the PDF with different bytes under the same filename.
        new_data = sample.with_name("sample_project_mixed.pdf").read_bytes()
        with patch.object(Upload, "getvalue", return_value=new_data):
            app.run()
            assert "analysis" not in app.session_state
            assert "report" not in app.session_state
            assert "reviewed_1" not in app.session_state
            new_summary = Summary("Second project", [["Second overview"], [], [], [], []], [])
            run.return_value = (new_summary, [Page(i) for i in range(1, 4)], [])
            app.button[0].click().run()
            app.button[1].click().run()
            assert not app.exception
            exported = Document(BytesIO(app.session_state["report"][1]))
            assert exported.paragraphs[0].text == "Second project"
            assert all(p.text not in {"Reviewed title", "Updated scope"} for p in exported.paragraphs)
            assert not exported.tables
            assert run.call_count == 2


def test_local_itu_annex_deliverable_regression():
    source = Path.home() / "Downloads" / "Annex I - ToRs.pdf"
    if not source.is_file():
        pytest.skip("Original ITU source PDF is not in Downloads.")
    report, _, _ = analyze(source.read_bytes(), EmptyModel(), "Off")
    assert {item.identifier: item.deadline for item in report.schedule if item.identifier} == {
        "D1": "31 January 2027", "D2": "30 April 2027", "D3": "31 May 2027", "D4": "30 June 2027"}
    warranty, = [item for item in report.schedule if item.kind == "contract"]
    assert warranty.deadline == "30 September 2027" and warranty.page == 9
    assert all(item.page == 8 for item in report.schedule if item.identifier)
    text = "\n".join(line for group in report.sections for line in group)
    for excluded in ("Application Programming Interface;", "Financial Offer", "Member States", "Technical Evaluation Criteria"):
        assert excluded.casefold() not in text.casefold()
    assert len(Document(generate_report(report)).paragraphs) < 90

