from copy import deepcopy
from typing import Optional
import uuid
from pydantic import field_validator, model_validator
from sqlalchemy import CheckConstraint, ForeignKey, Uuid
from sqlalchemy.types import JSON, TypeDecorator
from sqlmodel import Field, Column, SQLModel

from templates.v2.persistence import canonicalize_slide_ui
from utils.owner_context import get_current_owner_id


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
    owner_id: Optional[uuid.UUID] = Field(
        default_factory=get_current_owner_id,
        exclude=True,
        # The production FK is managed by Alembic; omit it from ORM metadata so
        # partial-schema fixtures can create slides independently.
        sa_column=Column(Uuid, nullable=True, index=True),
    )
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

    def is_authored(self) -> bool:
        """Whether this slide's body is model-authored HTML rather than a layout.

        An authored slide has no layout schema behind it, so the schema-driven
        chat editing tools can delete it but can never rebuild an equivalent
        one.  Callers use this to refuse the destructive half of that pair.
        """

        return (
            self.layout_group == "authored"
            or (self.layout or "").startswith("authored:")
            or (
                isinstance(self.content, dict)
                and self.content.get("__authored__") is True
            )
        )

    def get_new_slide(self, presentation: uuid.UUID, content: Optional[dict] = None):
        return SlideModel(
            id=uuid.uuid4(),
            owner_id=self.owner_id,
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
