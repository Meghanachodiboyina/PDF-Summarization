from src.document_analyzer import chunk_blocks
from src.schemas import Block, Page
from src.section_classifier import classify, heading_category
from src.text_preprocessor import preprocess


def test_normalize_and_remove_duplicates_preserving_sections():
    pages = [
        Page(1, [Block(1, "Page 1 of 2", edge=True), Block(1, "2. Project Objectives"),
                 Block(1, "Deliver  A-012   by  15 May 2027"), Block(1, "Deliver A-012 by 15 May 2027")]),
        Page(2, [Block(2, "Page 2 of 2", edge=True), Block(2, "Preserve version 2.1"),
                 Block(2, "Work | Duration\nBuild | 3 weeks", table=True)]),
    ]
    blocks = classify(preprocess(pages))
    assert [block.page for block in blocks] == [1, 1, 2, 2]
    assert blocks[1].text == "Deliver A-012 by 15 May 2027"
    assert blocks[2].category == "objective"
    assert blocks[3].text == "Work | Duration\nBuild | 3 weeks"


def test_heading_categories():
    assert heading_category("III. Proposed Methodology") == "methodology"
    assert heading_category("Submission Deadlines") == "deadline"
    assert heading_category("Submit proposals by 15 May") is None


def test_chunk_coverage_and_provenance():
    blocks = [Block(i, f"id-{i} " + "word " * 130) for i in range(1, 20)]
    chunks = chunk_blocks(blocks)
    assert len(chunks) > 1
    assert {block.page for chunk in chunks for block in chunk} == set(range(1, 20))
    assert all(sum(len(b.text) + 90 for b in chunk) <= 1800 for chunk in chunks)
