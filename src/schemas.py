"""Records shared by extraction, analysis and reporting."""
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

Category = Literal["title", "overview", "purpose", "scope", "objective", "methodology",
                   "technical", "activity", "deliverable", "timeline", "milestone",
                   "deadline", "date", "duration", "additional"]
OCRMode = Literal["Auto", "Force", "Off"]
ContentKind = Literal["project", "administrative", "glossary", "contents", "boilerplate", "irrelevant"]


@dataclass(frozen=True)
class Block:
    page: int
    text: str
    heading: bool = False
    table: bool = False
    edge: bool = False
    category: Category = "additional"
    kind: ContentKind = "project"
    cells: tuple[tuple[str, ...], ...] = ()
    exclusion: bool = False


@dataclass
class Page:
    number: int
    blocks: list[Block] = field(default_factory=list)
    ocr_attempted: bool = False
    ocr_used: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(block.text for block in self.blocks)


class Fact(BaseModel):
    category: Category
    page: int = Field(ge=1)
    value: str = Field(min_length=1, max_length=2500)
    quote: str = Field(min_length=1, max_length=3500)


class FactBatch(BaseModel):
    facts: list[Fact]


@dataclass(frozen=True)
class TimelineItem:
    activity: str
    page: int
    evidence: str
    identifier: str = ""
    deadline: str = ""
    duration: str = ""
    milestone: str = ""
    kind: Literal["deliverable", "activity", "contract"] = "activity"
    acceptance: str = ""
    expected_output: str = ""

    def row(self) -> list[str]:
        name = f"{self.identifier} — {self.activity}" if self.identifier else self.activity
        return [name, self.deadline or "Not specified", self.duration or "Not specified",
                self.milestone or self.expected_output or "Not specified"]


@dataclass
class Summary:
    title: str
    sections: list[list[str]]
    timeline: list[list[str]]
    facts: list[Fact] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schedule: list[TimelineItem] = field(default_factory=list)
