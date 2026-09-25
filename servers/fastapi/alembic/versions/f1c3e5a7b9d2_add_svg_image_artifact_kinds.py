"""Add immutable SVG image and manifest artifact kinds without changing old kinds."""
from alembic import op
import sqlalchemy as sa

revision = "f1c3e5a7b9d2"
down_revision = "f0b2d4e6a8c1"
branch_labels = None
depends_on = None

_OLD = "'checkpoint', 'slide-html', 'slide-svg', 'slide-png', 'export-bundle', 'presentation-pptx', 'presentation-pdf'"
_NEW = "'image-derivative', 'source-manifest', 'placement-manifest'"


def _replace(kinds):
    with op.batch_alter_table("public_artifacts") as batch:
        batch.drop_constraint("ck_public_artifact_kind", type_="check")
        batch.create_check_constraint("ck_public_artifact_kind", f"kind IN ({kinds})")


def upgrade():
    _replace(_OLD + ", " + _NEW)


def downgrade():
    if op.get_bind().scalar(sa.text(f"SELECT count(*) FROM public_artifacts WHERE kind IN ({_NEW})")):
        raise RuntimeError("SVG image artifacts must be retained; downgrade refused")
    _replace(_OLD)
