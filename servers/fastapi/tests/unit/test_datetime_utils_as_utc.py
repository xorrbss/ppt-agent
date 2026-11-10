"""Gate for ``utils.datetime_utils.as_utc`` as the single UTC normaliser.

Some former copies only stamped ``tzinfo=UTC`` onto an aware value instead
of converting it, which silently shifts a ``+09:00`` timestamp by nine
hours.  The shared helper converts with ``astimezone``; this pins that and
proves the migrated modules resolve to the shared helper.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from utils.datetime_utils import as_utc

_KST = timezone(timedelta(hours=9))


def test_naive_is_trusted_as_utc() -> None:
    naive = datetime(2026, 9, 29, 12, 0, 0)
    result = as_utc(naive)
    assert result.tzinfo is timezone.utc
    assert result == datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc)


def test_non_utc_aware_is_converted_not_relabelled() -> None:
    kst = datetime(2026, 9, 29, 12, 0, 0, tzinfo=_KST)
    result = as_utc(kst)
    assert result.tzinfo is timezone.utc
    assert result == kst
    assert result.hour == 3
    # The relabelling fork would have produced 12:00Z, nine hours later.
    assert result != datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc)


def test_utc_aware_is_unchanged() -> None:
    value = datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc)
    assert as_utc(value) == value


@pytest.mark.parametrize(
    "module_name",
    [
        "services.provider_durable_runtime",
        "services.public_operation_deadline_scope",
        "services.workspace_api_contract",
        "services.workspace_service",
        # editable_mvp_completion_codec's copy was dead code and is simply gone,
        # and the generation SQL repository lost its lease code with the
        # authored_vnext_generation_operations table.
    ],
)
def test_migrated_modules_use_the_shared_helper(module_name: str) -> None:
    import importlib

    module = importlib.import_module(module_name)
    for name in ("_as_utc", "_utc"):
        assert not hasattr(module, name) or getattr(module, name) is as_utc, (
            f"{module_name}.{name} is a private copy"
        )
    assert getattr(module, "as_utc", None) is as_utc


def test_workspace_api_contract_parses_then_normalises() -> None:
    from services.workspace_api_contract import _parse_utc

    assert _parse_utc(None) is None
    assert _parse_utc("garbage") is None
    assert _parse_utc("2026-09-29T12:00:00Z") == datetime(
        2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc
    )
    assert _parse_utc("2026-09-29T12:00:00+09:00") == datetime(
        2026, 9, 29, 3, 0, 0, tzinfo=timezone.utc
    )
    assert _parse_utc(datetime(2026, 9, 29, 12, 0, 0, tzinfo=_KST)).hour == 3
