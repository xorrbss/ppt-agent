"""Run the public-v2 RC Alembic graph under a dedicated migration DSN."""

from migrations import _run_migrations


if __name__ == "__main__":
    _run_migrations()
