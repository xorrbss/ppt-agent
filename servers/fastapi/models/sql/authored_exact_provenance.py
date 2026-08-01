"""Private provenance for exact-reconstruction authored presentations."""

import uuid

from sqlalchemy import JSON, Column, ForeignKey, Uuid
from sqlmodel import Field, SQLModel


class AuthoredExactProvenance(SQLModel, table=True):
    """One immutable generation manifest per exact-reconstruction deck.

    This data is intentionally kept out of ``presentations.theme`` because that
    object is returned by public presentation APIs.  The JSON contains hashes but
    never source paths or source bytes.
    """

    __tablename__ = "authored_exact_provenance"

    presentation_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("presentations.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        )
    )
    provenance: dict = Field(sa_column=Column(JSON, nullable=False))
