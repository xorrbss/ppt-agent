"""Shared types and receipt identifiers for authored local-fit evidence.

Split out of ``authored_local_fit_evidence`` so the comparison, run-
acceptance, receipt-building, and status modules can all depend on these
without importing back into the orchestrating module (which would be
circular). Every name here stays importable from
``services.authored_local_fit_evidence`` too.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


RECEIPT_KEY = "authored_local_fit"
RECEIPT_SCHEMA = "authored-local-fit-acceptance/v1"
ORIGINAL_DELIVERY_REVISION = "authored-local-fit-original-delivery/v1"


class AuthoredLocalFitRejected(RuntimeError):
    """The deterministic fitter or its independent reconstruction failed."""

    def __init__(self, reason: str):
        self.reason = str(reason or "reconstruction_mismatch")
        super().__init__(self.reason)


@dataclass(frozen=True, slots=True)
class AcceptedLocalFit:
    html: str
    png: bytes
    metrics: dict[str, Any]
    receipt: dict[str, Any]


__all__ = [
    "RECEIPT_KEY",
    "RECEIPT_SCHEMA",
    "ORIGINAL_DELIVERY_REVISION",
    "AuthoredLocalFitRejected",
    "AcceptedLocalFit",
]
