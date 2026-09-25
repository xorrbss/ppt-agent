"""`DriverRun`: accumulates per-stage timings and the first failure for the
product-path driver (generate_raster_deck.py), writing run_report.json after
every stage regardless of where the run stops.

Split out of the original generate_raster_deck.py (2026-09-25 eval suite) to
keep each module under the repo's file-size limit; behavior is unchanged.
"""
from __future__ import annotations

import contextlib
import json
import time
import traceback
from pathlib import Path
from typing import Optional

from raster_deck_http import ApiError


class DriverRun:
    """Accumulates per-stage timings and the first failure, for a clean
    stage/error-code report regardless of where the run stops."""

    def __init__(self, out_dir: Path):
        self.out_dir = out_dir
        self.timings: list[dict] = []
        self.failure: Optional[dict] = None

    @contextlib.contextmanager
    def stage(self, name: str):
        start = time.perf_counter()
        started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        print(f"=== stage: {name} (start {started_at}) ===")
        try:
            yield
        except (ApiError, RuntimeError) as exc:
            duration = time.perf_counter() - start
            self.timings.append({"stage": name, "seconds": round(duration, 1), "ok": False})
            if isinstance(exc, ApiError):
                self.failure = {
                    "stage": name, "method": exc.method, "path": exc.path,
                    "http_status": exc.status_code, "code": exc.code, "detail": exc.raw,
                }
            else:
                self.failure = {"stage": name, "code": None, "detail": str(exc)}
            self._write_report()
            raise
        except Exception as exc:  # unexpected bug in the driver itself
            duration = time.perf_counter() - start
            self.timings.append({"stage": name, "seconds": round(duration, 1), "ok": False})
            self.failure = {
                "stage": name, "code": "driver_internal_error",
                "detail": f"{type(exc).__name__}: {exc}",
                "traceback_tail": traceback.format_exc().splitlines()[-5:],
            }
            self._write_report()
            raise
        else:
            duration = time.perf_counter() - start
            self.timings.append({"stage": name, "seconds": round(duration, 1), "ok": True})
            print(f"=== stage: {name} done in {duration:.1f}s ===")
            self._write_report()

    def _write_report(self) -> None:
        report = {"timings": self.timings, "failure": self.failure}
        (self.out_dir / "run_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
