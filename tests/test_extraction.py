import pymupdf
import pytest

from src import ocr_engine
from src.pdf_extractor import extract_pdf, open_pdf
from src.schemas import Block


@pytest.fixture
def document():
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((60, 100), "Scope of Work: install and validate the sensor network.")
        scanned = pdf.new_page()
        scanned.draw_rect(pymupdf.Rect(50, 50, 300, 400), fill=(.5, .5, .5))
        pix = scanned.get_pixmap(matrix=pymupdf.Matrix(1, 1))
        scanned.insert_image(scanned.rect, stream=pix.tobytes("png"))
        return pdf.tobytes()


def test_mixed_ocr_and_page_numbers(document, monkeypatch):
    calls = []

    def fake(page, language, clip):
        calls.append(page.number)
        return [Block(page.number + 1, "Scanned field notes")]

    monkeypatch.setattr(ocr_engine, "recognize", fake)
    pages = extract_pdf(document)
    assert [page.number for page in pages] == [1, 2]
    assert "sensor network" in pages[0].text
    assert pages[1].ocr_used and pages[1].text == "Scanned field notes"
    assert calls == [1]
    off = extract_pdf(document, "Off")
    assert not off[1].ocr_attempted and off[1].warnings


def test_force_and_failure(document, monkeypatch):
    def fail(*args):
        raise RuntimeError("Tesseract unavailable")

    monkeypatch.setattr(ocr_engine, "recognize", fail)
    pages = extract_pdf(document, "Force")
    assert all(p.ocr_attempted and not p.ocr_used for p in pages)
    assert all("OCR failed" in p.warnings[0] for p in pages)
    assert pages[0].text


@pytest.mark.parametrize("data", [b"", b"not PDF", b"%PDF-1.7\ncorrupt"])
def test_invalid_pdf(data):
    with pytest.raises(ValueError):
        open_pdf(data)


def test_password_protected_pdf():
    with pymupdf.open() as pdf:
        pdf.new_page()
        data = pdf.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret")
    with pytest.raises(ValueError, match="password"):
        open_pdf(data)
