from copy import deepcopy
from typing import Optional
import uuid
from pydantic import field_validator, model_validator
from sqlalchemy import CheckConstraint, ForeignKey
from sqlalchemy.types import JSON, TypeDecorator
from sqlmodel import Field, Column, SQLModel

from templates.v2.persistence import canonicalize_slide_ui


class NativeSlideUIJSON(TypeDecorator):
    """Validate native slide UI on both database writes and database reads."""

    impl = JSON
    cache_ok = True

    def __init__(self) -> None:
        super().__init__(none_as_null=True)

    def process_bind_param(self, value, _dialect):
        return canonicalize_slide_ui(value)

    def process_result_value(self, value, _dialect):
        return canonicalize_slide_ui(value)


class SlideModel(SQLModel, table=True):
    __tablename__ = "slides"
    __table_args__ = (
        CheckConstraint(
            "ui IS NULL OR html_content IS NULL OR html_content = ''",
            name="ck_slides_native_ui_or_authored_html",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    presentation: uuid.UUID = Field(
        sa_column=Column(ForeignKey("presentations.id", ondelete="CASCADE"), index=True)
    )
    layout_group: str
    layout: str
    index: int
    content: dict = Field(sa_column=Column(JSON))
    html_content: Optional[str] = None
    # Native Template V2 editor payload. It is never projected into adaptive
    # content and must not coexist with authored HTML.
    ui: Optional[dict] = Field(
        sa_column=Column(NativeSlideUIJSON(), nullable=True),
        default=None,
    )
    speaker_note: Optional[str] = None
    properties: Optional[dict] = Field(sa_column=Column(JSON))

    @field_validator("ui", mode="before")
    @classmethod
    def canonicalize_native_ui(cls, value):
        return canonicalize_slide_ui(value)

    @model_validator(mode="after")
    def reject_mixed_editor_payload(self) -> "SlideModel":
        if self.ui is not None and self.html_content:
            raise ValueError("slide_ui_and_authored_html_cannot_coexist")
        return self

    def get_new_slide(self, presentation: uuid.UUID, content: Optional[dict] = None):
        return SlideModel(
            id=uuid.uuid4(),
            presentation=presentation,
            layout_group=self.layout_group,
            layout=self.layout,
            index=self.index,
            speaker_note=self.speaker_note,
            content=deepcopy(self.content if content is None else content),
            html_content=self.html_content,
            ui=canonicalize_slide_ui(self.ui),
            properties=deepcopy(self.properties),
        )
