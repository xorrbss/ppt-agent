"""Stable authored-vNext OOXML package service failures."""


class OoxmlPackageError(RuntimeError):
    status_code = 422

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


class OoxmlPackageNotFound(OoxmlPackageError):
    status_code = 404


class OoxmlPackageConflict(OoxmlPackageError):
    status_code = 409


class OoxmlPackageQuarantined(OoxmlPackageError):
    status_code = 423


class OoxmlAccountFenceError(OoxmlPackageError):
    status_code = 409


class SimulatedOoxmlWorkerCrash(RuntimeError):
    """Test-only abrupt boundary after candidate persistence."""


__all__ = [
    "OoxmlAccountFenceError",
    "OoxmlPackageConflict",
    "OoxmlPackageError",
    "OoxmlPackageNotFound",
    "OoxmlPackageQuarantined",
    "SimulatedOoxmlWorkerCrash",
]
