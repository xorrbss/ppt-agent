"""Admit the ``svg-native`` output mode in the public export identity authority.

SVG decks join the same digest-bound single-flight row as authored exports
(``services/public_export_identity.py``); ``svg-native`` names what those bytes
are. Without it an svg identity could only be recorded by pretending to be
``fidelity-raster``, which would lie about the export and key the hybrid
evidence branch wrongly. Slide checkpoints need no change: ``slide-svg`` and
the ``checkpoint`` retention class are already admitted by ``public_artifacts``.
"""
from alembic import op
import sqlalchemy as sa

revision = "a7c9e1b3d5f8"
down_revision = "f432fc854225"
branch_labels = None
depends_on = None

_TABLE = "public_export_identities"
_CONSTRAINT = "ck_public_export_identity_output_mode"
_OLD = "'fidelity-raster', 'editable-hybrid'"
_NEW = "'svg-native'"


def _replace(modes: str) -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(_CONSTRAINT, f"output_mode IN ({modes})")


def upgrade() -> None:
    _replace(_OLD + ", " + _NEW)


def downgrade() -> None:
    # An svg-native identity row holds a reference on its source checkpoint and
    # result artifact; dropping or relabelling it would leak those references or
    # misdescribe the bytes, so the downgrade refuses instead.
    if op.get_bind().scalar(
        sa.text(f"SELECT count(*) FROM {_TABLE} WHERE output_mode IN ({_NEW})")
    ):
        raise RuntimeError("svg-native export identities must be retained; downgrade refused")
    _replace(_OLD)
