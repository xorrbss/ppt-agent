from datetime import datetime, timezone


def get_current_utc_datetime():
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """Return ``value`` as an aware UTC datetime for portable comparisons.

    SQLite hands ``DateTime(timezone=True)`` columns back naive while
    PostgreSQL hands them back aware. Comparing or subtracting one against an
    aware ``now`` raises ``TypeError`` on SQLite, so every lease, heartbeat and
    deadline boundary must normalise through this helper before comparing.
    Naive values are trusted to be UTC because the service only ever writes UTC.
    """

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
