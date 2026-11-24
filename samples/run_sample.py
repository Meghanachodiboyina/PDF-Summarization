"""Analyze a sample with live local services and write its Word report."""
import argparse
from pathlib import Path

from src.document_analyzer import analyze
from src.llm_client import OllamaClient, OllamaError
from src.report_generator import generate_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", nargs="?", type=Path, default=Path("samples/sample_project.pdf"))
    args = parser.parse_args()
    client = OllamaClient()
    try:
        summary, pages, _ = analyze(args.pdf.read_bytes(), client,
                                    progress=lambda _, message: print(message, flush=True))
        path = Path("output") / f"{args.pdf.stem}_report.docx"
        path.write_bytes(generate_report(summary).getvalue())
    except (OSError, ValueError, OllamaError, RuntimeError) as exc:
        parser.exit(1, f"Analysis failed: {exc}\n")
    for warning in summary.warnings:
        print(warning)
    print(f"{path}: {len(pages)} pages, {sum(p.ocr_used for p in pages)} with OCR, "
          f"{len(summary.facts)} verified facts, {len(summary.timeline)} timeline rows.")
    if any("OCR failed" in warning or warning.startswith("Chunk ") for warning in summary.warnings):
        parser.exit(2, "Report saved with partial processing; review warnings above.\n")


if __name__ == "__main__":
    main()
