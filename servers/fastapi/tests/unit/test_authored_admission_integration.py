from __future__ import annotations

from functools import wraps
import asyncio

import importlib.util
from pathlib import Path




def sync_async(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


def shadow_report():
    root = Path(__file__).resolve().parents[4]
    spec = importlib.util.spec_from_file_location("shadow_report", root / "scripts/authored_admission_shadow_report.py")
    report = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(report)
    return report


def test_shadow_report_aggregates_content_free_lines():
    report = shadow_report()
    rows = report.aggregate(
        [
            'prefix authored_admission_shadow {"producer":"author",'
            '"existingVerdict":"admitted","gateVerdict":"rejected"}\n'
        ]
        * 2
    )
    # ``series_metrics.py`` unpacks this key as a three-tuple; it must not grow.
    assert list(rows) == [("author", "admitted", "rejected")]
    assert "| author | admitted | rejected | 2 |" in report.markdown(rows)


def test_shadow_report_scopes_to_one_operation_and_names_the_predicate():
    """One worker log holds several decks; a parity table is per operation.

    The shadow line carries no operation id of its own -- it is content-free --
    so a row belongs to the most recent operation id the worker logged above it.
    """

    report = shadow_report()
    lines = [
        'workspace_operation {"operation_id":"11111111-1111-4111-8111-111111111111"}\n',
        'x authored_admission_shadow {"producer":"render","existingVerdict":"admitted",'
        '"existingCode":"","gateVerdict":"rejected","gateCode":"source_content_browser_readback_failed",'
        '"predicate":"visibility","blockCount":2,"gateError":"","gateErrorDigest":""}\n',
        'workspace_operation {"operation_id":"22222222-2222-4222-8222-222222222222"}\n',
        'x authored_admission_shadow {"producer":"publication","existingVerdict":"unobserved",'
        '"existingCode":"","gateVerdict":"error","gateCode":"authored_admission_internal_error",'
        '"predicate":"","blockCount":0,"gateError":"TypeError","gateErrorDigest":"0123456789abcdef"}\n',
    ]
    whole_log = report.aggregate(lines, columns=report.COLUMNS)
    assert sum(whole_log.values()) == 2
    scoped = report.aggregate(lines, operation="22222222", columns=report.COLUMNS)
    table = report.markdown(scoped, columns=report.COLUMNS)
    assert sum(scoped.values()) == 1
    assert "| publication | unobserved |  | error | authored_admission_internal_error |" in table
    assert table.rstrip().endswith("|  | 0 | TypeError | 1 |")
    assert "| producer | existing verdict |" in table and "| blocks | gate error | count |" in table


