"""Add a separately fenced SVG export source lineage artifact kind."""
from alembic import op
import sqlalchemy as sa

revision = "f2d4e6a8c0b3"
down_revision = "c9e3a5f7b1d4"
branch_labels = None
depends_on = None

_OLD = "kind IN ('checkpoint', 'slide-html', 'slide-svg', 'slide-png', 'export-bundle', 'presentation-pptx', 'presentation-pdf', 'image-derivative', 'source-manifest', 'placement-manifest')"
_NEW = _OLD[:-1] + ", 'svg-export-source-lineage')"


def _replace(condition: str) -> None:
    with op.batch_alter_table("public_artifacts") as batch:
        batch.drop_constraint("ck_public_artifact_kind", type_="check")
        batch.create_check_constraint("ck_public_artifact_kind", condition)


def upgrade() -> None:
    _replace(_NEW)


def downgrade() -> None:
    if op.get_bind().scalar(sa.text(
        "SELECT count(*) FROM public_artifacts WHERE kind = 'svg-export-source-lineage'"
    )):
        raise RuntimeError("SVG export source lineage artifacts must be retained before downgrade")
    _replace(_OLD)
