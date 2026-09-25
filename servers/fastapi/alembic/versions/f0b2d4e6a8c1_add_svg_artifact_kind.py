"""Admit immutable SVG source artifacts at the existing artifact boundary."""
from alembic import op
import sqlalchemy as sa

revision = "f0b2d4e6a8c1"
down_revision = "e9a1c3f5b7d2"
branch_labels = None
depends_on = None


def _replace(include_svg):
    kinds = "'checkpoint', 'slide-html', 'slide-png', 'export-bundle', 'presentation-pptx', 'presentation-pdf'"
    if include_svg:
        kinds += ", 'slide-svg'"
    with op.batch_alter_table("public_artifacts") as batch:
        batch.drop_constraint("ck_public_artifact_kind", type_="check")
        batch.create_check_constraint("ck_public_artifact_kind", f"kind IN ({kinds})")


def upgrade():
    _replace(True)


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM public_artifacts WHERE kind='slide-svg'")):
        raise RuntimeError("SVG artifacts must be retained; downgrade refused")
    _replace(False)
