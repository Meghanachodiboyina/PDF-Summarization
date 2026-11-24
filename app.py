"""Streamlit entry point for DocuMind AI."""
from dataclasses import replace
import hashlib
from pathlib import Path

import streamlit as st

from src.config import COLUMNS, MAX_BYTES, MAX_PAGES, MISSING, ollama_settings
from src.document_analyzer import analyze
from src.llm_client import OllamaClient, OllamaError
from src.ocr_engine import find_tesseract
from src.pdf_extractor import open_pdf
from src.report_generator import HEADINGS, MIME, detail_label, generate_report, method_label


@st.cache_data(ttl=15, show_spinner=False)
def model_status(url: str) -> tuple[list[str], str | None]:
    try:
        return OllamaClient().models(), None
    except OllamaError as exc:
        return [], str(exc)


def clear_document_state() -> None:
    saved = st.session_state.pop("analysis", None)
    st.session_state.pop("report", None)
    if saved:
        st.session_state.pop(f"reviewed_{saved[2]}", None)


def summary_preview(summary) -> None:
    """Render the report structure from the current reviewed values."""
    st.subheader(summary.title or MISSING)
    content = iter(summary.sections)
    for index, heading in enumerate(HEADINGS, 1):
        st.markdown(f"#### {index}. {heading}")
        if index == 5:
            if summary.timeline:
                st.dataframe([dict(zip(COLUMNS, row)) for row in summary.timeline], hide_index=True)
            else:
                st.caption(MISSING)
            continue
        lines = next(content) or [MISSING]
        for number, line in enumerate(lines, 1):
            safe = "".join("\\" + c if c in r"\[]*_`" else c for c in line)
            if line == MISSING or index == 1:
                st.text(line)
            elif index == 2:
                st.markdown(f"- {safe}")
            elif index == 3:
                st.markdown(f"{number}. {safe}")
            elif index == 4:
                st.markdown(f"{number}. **{method_label(line)}** — {safe}")
            else:
                st.markdown(f"- **{detail_label(line)}** — {safe}")


def main() -> None:
    st.set_page_config(page_title="DocuMind AI", page_icon="📄", layout="wide")
    st.title("DocuMind AI — PDF Analysis & Summarization Agent")
    st.write("Extract project facts from a PDF, review their source evidence, and export a Word report.")
    try:
        url, default, timeout = ollama_settings()
    except ValueError as exc:
        st.error(str(exc))
        return
    with st.sidebar:
        st.header("Local processing")
        models, error = model_status(url)
        if error:
            st.warning(error)
        else:
            st.success("Ollama connected")
        options = list(dict.fromkeys([default, *models]))
        model = st.selectbox("Ollama model", options)
        if not error and model not in models and model + ":latest" not in models:
            st.warning(f"Install the selected model with ollama pull {model}.")
        st.caption(f"Tesseract {'available' if find_tesseract() else 'unavailable'}")
        mode = st.radio("OCR mode", ["Auto", "Force", "Off"], horizontal=True,
                        help="Auto uses OCR on sparse or large-image pages. Force attempts OCR on every page.")
        language = st.text_input("Tesseract language code", "eng", disabled=mode == "Off").strip() or "eng"
        st.caption(f"PDF limit {MAX_BYTES // 1024**2} MiB / {MAX_PAGES} pages · Model timeout {timeout:g} s")
        st.caption("Text extraction → OCR → local model → evidence review → Word")

    upload = st.file_uploader("Project PDF", type=["pdf"], max_upload_size=MAX_BYTES // 1024**2)
    data = upload.getvalue() if upload is not None else None
    identity = (hashlib.sha256(data).hexdigest(), model, mode, language, url) if data is not None else None
    if st.session_state.get("active_document") != identity:
        clear_document_state()
        st.session_state.active_document = identity
    if not upload:
        return
    try:
        with open_pdf(data) as document:
            total = document.page_count
    except ValueError as exc:
        st.error(str(exc))
        return
    st.caption(f"{upload.name} · {len(data) / 1024:,.1f} KiB · {total} page(s)")
    if st.button("Start Analysis", type="primary", disabled=bool(error) or
                 (model not in models and model + ":latest" not in models)):
        clear_document_state()
        indicator = st.progress(0, text="Reading document")
        try:
            with st.spinner("Processing the document locally…"):
                result = analyze(data, OllamaClient(model), mode, language,
                                 lambda fraction, message: indicator.progress(fraction, text=message))
        except (ValueError, OllamaError, RuntimeError) as exc:
            indicator.empty()
            st.error(str(exc))
        else:
            st.session_state.analysis = identity, result, st.session_state.get("revision", 0) + 1
            st.session_state.revision = st.session_state.analysis[2]
    saved = st.session_state.get("analysis")
    if not saved or saved[0] != identity:
        return
    summary, pages, blocks = saved[1]
    revision = saved[2]
    extracted, ocr, fact_count = st.columns(3)
    extracted.metric("Readable pages", f"{sum(bool(p.text.strip()) for p in pages)}/{total}")
    ocr.metric("Pages with OCR text", sum(p.ocr_used for p in pages))
    fact_count.metric("Verified facts", len(summary.facts))
    for warning in summary.warnings:
        st.warning(warning)
    review, evidence = st.tabs(["Summary & report", "Sections & evidence"])
    with evidence:
        labels = [{"Page": b.page, "Section": b.category, "Content": b.kind, "Heading": b.text}
                  for b in blocks if b.heading]
        st.subheader("Detected sections")
        if labels:
            st.dataframe(labels, hide_index=True)
        else:
            st.info("No explicit section headings were detected.")
        with st.expander("Schedule evidence"):
            if summary.schedule:
                st.dataframe([{"Activity": item.row()[0], "Type": item.kind, "Page": item.page,
                               "Deadline": item.deadline or "Not specified", "Quote": item.evidence}
                              for item in summary.schedule], hide_index=True)
            else:
                st.caption(MISSING)
        with st.expander("Verified source evidence"):
            st.dataframe([{"Page": f.page, "Category": f.category, "Value": f.value, "Quote": f.quote}
                          for f in summary.facts], hide_index=True)
        with st.expander("Extracted page text"):
            number = st.selectbox("Source page", range(1, total + 1))
            page = pages[number - 1]
            if page.ocr_attempted:
                st.caption("OCR recovered text" if page.ocr_used else "OCR attempted without recovered text")
            st.text(page.text or "No readable content")
    with review:
        st.caption("Review and correct the summary. Manual edits are included in the export.")
        edited_key = f"reviewed_{revision}"
        with st.form(f"review_{revision}"):
            title = st.text_input("Project name", summary.title, key=f"project_name_{revision}")
            sections = []
            content = iter(summary.sections)
            timeline = []
            for index, heading in enumerate(HEADINGS, 1):
                if index == 5:
                    st.subheader(heading)
                    import pandas as pd
                    frame = pd.DataFrame(summary.timeline, columns=COLUMNS, dtype=str)
                    edited = st.data_editor(frame, hide_index=True, num_rows="dynamic", key=f"timeline_{revision}",
                                            column_config={c: st.column_config.TextColumn(c) for c in COLUMNS},
                                            width="stretch")
                    timeline = [[str(cell).strip() for cell in row]
                                for row in edited.fillna("").itertuples(index=False, name=None)
                                if any(str(cell).strip() for cell in row)]
                else:
                    value = st.text_area(heading, "\n".join(next(content)), key=f"section_{revision}_{index}")
                    sections.append([line.strip() for line in value.splitlines() if line.strip()])
            submitted = st.form_submit_button("Generate Word report", type="primary")
        if submitted:
            st.session_state.pop("report", None)
            try:
                reviewed = replace(summary, title=title.strip(), sections=sections, timeline=timeline)
                stream = generate_report(reviewed)
                st.session_state[edited_key] = reviewed
                st.session_state.report = revision, stream.getvalue()
            except RuntimeError as exc:
                st.error(str(exc))
        report = st.session_state.get("report")
        if report and report[0] == revision:
            st.download_button("Download Word Report", report[1], f"{Path(upload.name).stem}_Report.docx",
                               MIME, on_click="ignore")
        with st.expander("Formatted report preview", expanded=True):
            summary_preview(st.session_state.get(edited_key, summary))


if __name__ == "__main__":
    main()
