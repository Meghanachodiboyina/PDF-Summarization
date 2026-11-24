"""Create reproducible embedded-text, scanned and mixed sample PDFs."""
import argparse
from pathlib import Path

import pymupdf

FOLDER = Path(__file__).resolve().parent


def make_text() -> Path:
    path = FOLDER / "sample_project.pdf"
    with pymupdf.open() as pdf:
        def new_page():
            page = pdf.new_page()
            page.insert_text((52, 35), "Riverbend City | Capital Projects Office", fontsize=9)
            page.insert_text((52, 805), f"Page {page.number + 1} of 3", fontsize=9)
            return page

        def line(page, y, text, size=11, bold=False):
            page.insert_text((52, y), text, fontsize=size, fontname="hebo" if bold else "helv")

        first = new_page()
        line(first, 105, "Riverbend Flood Monitoring Modernization", 23, True)
        line(first, 148, "Project Overview", 15, True)
        line(first, 176, "Riverbend City will modernize flood monitoring across the Lower Cedar watershed.")
        line(first, 195, "The project integrates river gauges and rainfall telemetry into a shared dashboard.")
        line(first, 230, "Project Purpose", 15, True)
        line(first, 258, "Provide timely flood alerts to municipal emergency coordinators.")
        line(first, 300, "Project Objectives", 15, True)
        line(first, 329, "- Reduce alert delivery latency to under two minutes.")
        line(first, 348, "- Provide continuous telemetry from 12 monitoring sites.")
        line(first, 385, "Scope of Work", 15, True)
        line(first, 414, "- Survey existing gauge sites and confirm cellular coverage.")
        line(first, 433, "- Install 12 water-level sensors and commission the telemetry network.")

        second = new_page()
        line(second, 105, "Proposed Methodology", 15, True)
        line(second, 134, "1. Survey sites and establish water-level calibration baselines.")
        line(second, 154, "2. Pilot telemetry at three sites and validate readings against manual measurements.")
        line(second, 174, "3. Deploy the validated configuration to the remaining nine sites.")
        line(second, 217, "Deliverables", 15, True)
        line(second, 246, "- Calibrated monitoring stations and the operator dashboard.")
        line(second, 265, "- Operator handbook and acceptance test results.")
        line(second, 309, "Technical Requirements", 15, True)
        line(second, 338, "Each station must buffer at least 72 hours of readings during a network outage.")
        line(second, 357, "The telemetry interface must publish records through MQTT over TLS 1.3.")

        third = new_page()
        line(third, 105, "Implementation Timeline", 15, True)
        cols = [52, 229, 352, 438, 548]
        rows = [
            ("Activity / Work Item", "Deadline", "Duration", "Milestone"),
            ("Field survey", "15 January 2027", "2 weeks", "Survey sign-off"),
            ("Pilot installation", "19 February 2027", "4 weeks", "Pilot accepted"),
            ("Deployment", "", "6 weeks", "12 stations live"),
            ("Operator handover", "30 April 2027", "", "Final acceptance"),
        ]
        for i, row in enumerate(rows):
            y = 132 + i * 24
            for j, text in enumerate(row):
                third.draw_rect(pymupdf.Rect(cols[j], y, cols[j + 1], y + 24), width=.6)
                if text:
                    third.insert_text((cols[j] + 5, y + 16), text, fontsize=10,
                                      fontname="hebo" if i == 0 else "helv")
        line(third, 287, "Submission Deadlines", 15, True)
        line(third, 317, "The acceptance test report is due on 7 May 2027.")
        line(third, 362, "Additional Information", 15, True)
        line(third, 391, "The city will retain operational telemetry for 18 months.")
        pdf.save(path)
    return path


def raster_variant(source: Path, name: str, mixed: bool) -> None:
    with pymupdf.open(source) as embedded, pymupdf.open() as result:
        for page in embedded:
            if mixed and page.number != 1:
                result.insert_pdf(embedded, from_page=page.number, to_page=page.number)
                continue
            pix = page.get_pixmap(dpi=225, alpha=False)
            output = result.new_page(width=page.rect.width, height=page.rect.height)
            output.insert_image(output.rect, stream=pix.tobytes("png"))
        result.save(FOLDER / name, deflate=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scanned", action="store_true", help="Also generate scanned and mixed samples")
    args = parser.parse_args()
    source = make_text()
    if args.scanned:
        raster_variant(source, "sample_project_scanned.pdf", mixed=False)
        raster_variant(source, "sample_project_mixed.pdf", mixed=True)
