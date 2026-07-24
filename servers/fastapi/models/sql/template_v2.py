from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Uuid,
    false,
    func,
    text,
)
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime


TEMPLATE_V2_CANONICAL_COLUMNS = frozenset(
    {
        "id",
        "name",
        "description",
        "raw_layouts",
        "components",
        "merged_components",
        "layouts",
        "assets",
        "is_default",
        "created_at",
        "updated_at",
    }
)
TEMPLATE_V2_TRANSITIONAL_LOCAL_COLUMNS = frozenset(
    {"presentation_id", "revision"}
)


def _new_template_v2_id() -> str:
    return str(uuid.uuid4())


class TemplateV2(SQLModel, table=True):
    """Persisted native structured-template definition.

    The canonical fields intentionally match presenton/presenton at the pinned
    Template V2 baseline. ``presentation_id`` and ``revision`` remain mapped
    temporarily so the existing API can be moved to ``TemplateV2LocalState``
    without a flag-day schema change; new local integrations must use the
    sidecar instead of adding more columns here.
    """

    __tablename__ = "template_v2"

    id: str = Field(primary_key=True, default_factory=_new_template_v2_id)
    # Transitional compatibility columns. Their values are backfilled into
    # template_v2_local_state by revision f9a0b1c2d3e4. The presentation FK
    # is also the current presentation-owned delete path for the canonical
    # row; a later migration must install a replacement before removing it.
    presentation_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey(
                "presentations.id",
                name="fk_template_v2_presentation_id_presentations",
                ondelete="CASCADE",
            ),
            nullable=False,
            index=True,
        )
    )
    name: str = Field(nullable=False)
    description: Optional[str] = Field(default=None, nullable=True)
    raw_layouts: Optional[dict] = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    components: Optional[dict] = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    merged_components: Optional[dict] = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    layouts: Optional[dict] = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    assets: Optional[dict] = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    is_default: bool = Field(
        sa_column=Column(
            Boolean,
            nullable=False,
            default=False,
            server_default=false(),
        ),
        default=False,
    )
    revision: int = Field(
        sa_column=Column(
            Integer,
            nullable=False,
            default=1,
            server_default=text("1"),
        ),
        default=1,
    )
    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            default=get_current_utc_datetime,
            server_default=func.now(),
        ),
        default_factory=get_current_utc_datetime,
    )
    updated_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            default=get_current_utc_datetime,
            onupdate=get_current_utc_datetime,
            server_default=func.now(),
        ),
        default_factory=get_current_utc_datetime,
    )
