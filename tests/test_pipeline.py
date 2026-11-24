from pathlib import Path

from src.document_analyzer import analyze
from src.llm_client import OllamaError
from src.schemas import Fact


def test_pipeline_exports_current_pdf_after_failed_chunk_and_rejects_unsupported_facts():
    class Client:
        calls = 0

        def check(self):
            pass

        def extract(self, blocks):
            self.calls += 1
            if self.calls == 1:
                raise OllamaError("invalid JSON")
            return [Fact(category="title", page=1, value="Wrong project", quote="Wrong project")], 0

    source = Path(__file__).resolve().parents[1] / "samples" / "sample_project.pdf"
    client = Client()
    summary, pages, _ = analyze(source.read_bytes(), client, "Off")
    assert client.calls > 1 and len(pages) == 3
    assert summary.title == "Riverbend Flood Monitoring Modernization"
    assert len(summary.sections[2]) == 2
    assert len(summary.sections[3]) == 5
    assert any(row[0] == "Deployment" and row[1] == "Not specified" for row in summary.timeline)
    assert ["Field survey", "15 January 2027", "2 weeks", "Survey sign-off"] in summary.timeline
    assert any("7 May 2027" in row[1] for row in summary.timeline)
    assert all(f.value != "Wrong project" for f in summary.facts)
    assert any("invalid JSON" in warning for warning in summary.warnings)
