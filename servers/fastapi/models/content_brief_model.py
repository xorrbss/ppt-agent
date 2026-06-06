from typing import List, Optional

from pydantic import BaseModel, Field


class DataPoint(BaseModel):
    label: str = Field(
        description="Metric / category / row name, e.g. 'Q3 2025 revenue'"
    )
    value: str = Field(
        description="Figure as text, keeping units/%/$/dates, e.g. '$4.2M', '37%', '2019'"
    )
    context: Optional[str] = Field(
        default=None,
        description="Year, baseline/comparison, or source qualifier",
    )


class BriefSection(BaseModel):
    heading: str = Field(description="Section heading, plain text")
    key_points: List[str] = Field(
        default_factory=list,
        description="Substantive claims/insights, each a full sentence",
    )
    facts_figures: List[str] = Field(
        default_factory=list,
        description="Concrete facts: stats, dates, named examples",
    )
    data_points: List[DataPoint] = Field(
        default_factory=list,
        description="Structured numeric/tabular data; empty if none",
    )


class ContentBrief(BaseModel):
    """Stage A output: a rich, uncapped knowledge brief produced BEFORE slides exist.

    It is used to ground the downstream outline stage so generated decks are
    substantive rather than fragmentary.
    """

    title: str = Field(description="Concise presentation title, plain text")
    overview: Optional[str] = Field(
        default=None, description="1-3 sentence framing of the topic"
    )
    sections: List[BriefSection] = Field(
        default_factory=list,
        description="Logically ordered coverage of the topic",
    )

    def to_prompt_context(self) -> str:
        """Render the brief as readable markdown to ground the outline stage."""
        lines: List[str] = [f"# Knowledge Brief: {self.title}"]
        if self.overview:
            lines.append(self.overview)
        for section in self.sections:
            lines.append(f"\n## {section.heading}")
            for point in section.key_points:
                lines.append(f"- {point}")
            for fact in section.facts_figures:
                lines.append(f"- (fact) {fact}")
            for dp in section.data_points:
                suffix = f" ({dp.context})" if dp.context else ""
                lines.append(f"- (data) {dp.label}: {dp.value}{suffix}")
        return "\n".join(lines)
